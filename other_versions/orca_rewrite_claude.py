#!/usr/bin/env python3
"""
ORCA v2.0 — Bioprinter Control System

Simplified and stabilized TUI for controlling a 3D bioprinter via serial.
All movement speeds are hard-clamped to never exceed F300.

Dependencies: rich, pyserial
"""

import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime

# Platform-specific imports for terminal control
if sys.platform == 'win32':
    import msvcrt
else:
    import select
    import tty
    import termios

# --- Third-party imports ---
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, IntPrompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
except ImportError:
    print("Missing dependency. Run: pip install rich")
    sys.exit(1)

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Missing dependency. Run: pip install pyserial")
    sys.exit(1)

console = Console()


# ============================================================
#  CONSTANTS
# ============================================================
VERSION = "2.0.0"
MAX_FEEDRATE = 300
RAW_DIR = "raw_gcode"
OUT_DIR = "translated_gcode"
JOG_STEPS = [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
FILAMENT_DIA = 1.75  # mm — standard filament diameter for scaling pure extrusion


# ============================================================
#  CONFIGURATION
# ============================================================
class Config:
    """All user-adjustable parameters in one place."""

    def __init__(self):
        self.coordinate_mode = "G90"       # G90 = Absolute, G91 = Relative
        self.extrusion_axis = "B"          # B or C
        self.z_syringe_dia = 4.9           # mm
        self.a_syringe_dia = 4.9           # mm
        self.z_nozzle_dia = 2.0            # mm
        self.a_nozzle_dia = 0.2            # mm
        self.extrusion_coeff = 0.33
        self.auto_pressurize = True
        self.pressurize_amount = 0.2       # mm
        self.jog_step_index = 2            # Index into JOG_STEPS → 0.2 mm
        self.start_from_center = False
        self.baud_rate = 115200

    @property
    def jog_distance(self):
        return JOG_STEPS[self.jog_step_index]


# ============================================================
#  UTILITIES
# ============================================================
def clamp_feedrate(line):
    """Guarantee no F-parameter in a G-code string exceeds MAX_FEEDRATE.

    This is the single safety gate — every command passes through here
    before reaching the printer, either from manual input, the jog,
    the translator, or the print sender.
    """
    def _clamp(match):
        val = min(float(match.group(1)), MAX_FEEDRATE)
        return f"F{int(val)}" if val == int(val) else f"F{val:g}"
    return re.sub(r'F(\d+\.?\d*)', _clamp, line, flags=re.IGNORECASE)


def clean_gcode(cmd):
    """Sanitize user-typed G-code (unicode dashes, stray spaces)."""
    cmd = re.sub(r'[\u2013\u2014\u2212]', '-', cmd)           # Smart/unicode dashes → ASCII
    cmd = re.sub(r'([A-Za-z])\s+([-.\d])', r'\1\2', cmd)      # "X -5" → "X-5"
    return cmd


def _read_key():
    """Non-blocking single-keypress read.

    Requires cbreak/raw terminal mode on Unix (set by the caller).
    On Windows, uses msvcrt.
    """
    if sys.platform == 'win32':
        if msvcrt.kbhit():
            return msvcrt.getch().decode('utf-8', errors='ignore').lower()
        return None
    else:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            return sys.stdin.read(1).lower()
        return None


def _check_pause():
    """Non-blocking check: did the user press Enter (Unix) or any key (Windows)?

    Used during printing (normal terminal mode).
    """
    if sys.platform == 'win32':
        if msvcrt.kbhit():
            msvcrt.getch()
            return True
    else:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            sys.stdin.readline()
            return True
    return False


# ============================================================
#  PRINTER COMMUNICATION
# ============================================================
class Printer:
    """Encapsulates all serial communication with the 3D printer.

    Every outgoing command is automatically speed-clamped by
    ``clamp_feedrate()`` so nothing above F300 can ever reach the
    hardware, regardless of the caller.
    """

    def __init__(self):
        self._conn = None

    # --- Properties ---

    @property
    def is_connected(self):
        return self._conn is not None and self._conn.is_open

    @property
    def port(self):
        return self._conn.port if self._conn else None

    @property
    def has_data(self):
        return self._conn is not None and self._conn.in_waiting > 0

    # --- Connection lifecycle ---

    def connect(self, port, baud):
        """Open a serial connection with a hardware DTR reset."""
        self.disconnect()
        self._conn = serial.Serial(port, baud, timeout=2)
        # DTR toggle resets the printer board's serial state
        self._conn.setDTR(False)
        time.sleep(0.05)
        self._conn.setDTR(True)
        self.flush()
        self._conn.write(b"\n\n")
        time.sleep(2)
        self.flush()

    def disconnect(self):
        """Close the serial connection (safe to call repeatedly)."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def flush(self):
        """Clear OS serial buffers in both directions."""
        if self._conn:
            self._conn.reset_input_buffer()
            self._conn.reset_output_buffer()

    # --- Command sending ---

    def send(self, cmd, timeout=5.0):
        """Send a G-code command and wait for 'ok'.

        Returns a list of response lines from the printer.
        The command is speed-clamped before sending.
        """
        self._require()
        cmd = clamp_feedrate(cmd.strip())
        self._conn.write((cmd + '\n').encode('ascii', errors='ignore'))
        return self._wait_ok(timeout)

    def send_nowait(self, cmd):
        """Send a G-code command without waiting for acknowledgment.

        The command is still speed-clamped.
        """
        self._require()
        cmd = clamp_feedrate(cmd.strip())
        self._conn.write((cmd + '\n').encode('ascii', errors='ignore'))

    # --- Reading ---

    def read_line(self):
        """Read one response line if data is available. Returns str or None."""
        if self.has_data:
            try:
                return self._conn.readline().decode('utf-8', errors='ignore').strip()
            except Exception:
                return None
        return None

    # --- Board reset ---

    def reset_board(self):
        """Emergency stop (M112) + restart (M999) + DTR hardware reset."""
        self._require()
        self._conn.write(b"M112\n")
        time.sleep(0.1)
        self._conn.write(b"M999\n")
        self._conn.setDTR(False)
        time.sleep(0.5)
        self._conn.setDTR(True)
        self.flush()

    # --- Internal helpers ---

    def _wait_ok(self, timeout):
        """Block until 'ok' arrives or timeout expires."""
        lines = []
        start = time.time()
        while time.time() - start < timeout:
            if self._conn.in_waiting > 0:
                resp = self._conn.readline().decode('utf-8', errors='ignore').strip()
                if resp:
                    lines.append(resp)
                if 'ok' in resp.lower():
                    return lines
            time.sleep(0.01)
        return lines  # Timeout — return whatever we received

    def _require(self):
        if not self.is_connected:
            raise RuntimeError("Printer not connected")


# ============================================================
#  DISPLAY
# ============================================================
SPLASH = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣿⣿⡆⠀⠀⠀⠀⠀⢀⣀⣄⡀⠰⠴⣶⣶⣤⣤⡀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣿⣿⣿⣿⣿⡇⠀⢀⣤⣶⣻⣾⣿⣴⣴⣾⣿⣿⣿⣿⣿⣿⡆
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⣿⣿⣿⣿⣿⣥⣾⠿⢿⣿⣽⣾⣿⣿⣿⣿⣿⣿⣿⠿⢿⡿⣧
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⠟⠉⠀⠀⠀⣸⣿⣿⣿⣿⡿⠟⠛⠋⠉⠐⠊⠡⢹⢚    ____  _____  _____         
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣠⣤⣤⡴⠂⠐⠒⢨⣿⣿⣿⣿⣿⣿⣤⣆⣤⣠⣴⣾⣿⣷⡿⠋⠁⠀⠀⠀⠀⠀⠐⣁⠎⠀⡘  / __ \|  __ \ / ____|   /\   
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢐⣠⣤⣶⣾⣿⣿⣿⣿⣿⣆⡀⡀⣀⣨⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡜⠀⠀⡐⠀ | |  | | |__) | |       /  \   
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠄⠀⠄⠀⠀⠀⠀⠀⠂⠀⠀ | |  | |  _  /| |      / /\ \  
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣴⡶⠿⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡠⠂⠀⠀⠀ | |__| | | \ \| |____ / ____ \ 
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣵⣿⣿⣅⠀⠀⠀⠀⢈⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠖⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠂⠀⠀⠀⠀⠀  \____/|_|  \_\_____/_/    \_\
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣶⣦⣌⠁⠀⠉⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡏⡞⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠜⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣀⣀⣤⢤⢤⡴⢶⣾⡿⠿⣛⠩⠀⠉⠉⠙⠛⠻⠿⢏⡀⠀⠀⠀⠙⠻⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢈⡷⠀⠀⠀⠀⠀⠀⠀⠀⣠⣷⣿⡀⠀⠀⠀⠀⠀⠀⠀         [cyan]v2.0.0[/cyan]
⢠⠖⠋⠉⠀⢀⠀⠂⣌⢇⠀⣰⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⠀⡀⠀⠀⢀⣽⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⣐⠰⠂⠀⠀⠀⠀⡀⣠⣴⣾⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠛⠓⠒⠲⢤⣀⣐⣤⡞⣸⢊⠥⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠀⢀⣤⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⢄⣀⠀⠠⠤⠴⠂⠈⠁⢰⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⠃⠀⠀⠸⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢖⣦⣀⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠾⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⡿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""


def display_header():
    """Print the ORCA splash art."""
    art = SPLASH.replace('\u2800', ' ')
    console.print(art, style="bold white")


# ============================================================
#  CONNECT / RESET
# ============================================================
def connect_menu(printer, config):
    """List serial ports and connect to the user's choice."""
    ports = serial.tools.list_ports.comports()
    if not ports:
        console.print("[red]No serial ports found. Is the printer plugged in?[/red]")
        time.sleep(2)
        return

    console.print("[bold cyan]Available Ports:[/bold cyan]")
    for i, p in enumerate(ports, 1):
        console.print(f"  [{i}] {p.device} \u2014 {p.description}")
    console.print("  [0] Cancel")

    choice = IntPrompt.ask(
        "Select port",
        choices=[str(i) for i in range(len(ports) + 1)],
    )
    if choice == 0:
        return

    port = ports[choice - 1].device
    try:
        with console.status(f"Connecting to {port} at {config.baud_rate} baud\u2026", spinner="dots"):
            printer.connect(port, config.baud_rate)
        console.print(f"[green]Connected to {port}[/green]")
        time.sleep(1)
    except Exception as e:
        console.print(f"[red]Connection failed: {e}[/red]")
        time.sleep(2)


def reset_menu(printer):
    """Send a hard reset sequence to the printer board."""
    if not printer.is_connected:
        console.print("[red]Printer not connected.[/red]")
        time.sleep(1)
        return

    console.print("[yellow]Sending reset signals\u2026[/yellow]")
    try:
        printer.reset_board()
        console.print("[green]Reset complete. Give it a few seconds to boot.[/green]")
        time.sleep(2)
    except Exception as e:
        console.print(f"[red]Reset failed: {e}[/red]")
        console.print("[yellow]Tip: Try physically unplugging and re-plugging the USB cable.[/yellow]")
        time.sleep(3)


# ============================================================
#  SETTINGS
# ============================================================
def settings_menu(config):
    """Interactive settings editor loop."""
    while True:
        console.clear()
        display_header()

        t = Table(
            title="Configuration",
            show_header=True,
            header_style="bold yellow",
            expand=True,
        )
        t.add_column("Parameter")
        t.add_column("Value", style="cyan")
        t.add_column("Parameter")
        t.add_column("Value", style="cyan")
        t.add_row(
            "Coordinate Mode", config.coordinate_mode,
            "Extrusion Axis", config.extrusion_axis,
        )
        t.add_row(
            "Z Syringe (mm)", str(config.z_syringe_dia),
            "A Syringe (mm)", str(config.a_syringe_dia),
        )
        t.add_row(
            "Z Nozzle (mm)", str(config.z_nozzle_dia),
            "A Nozzle (mm)", str(config.a_nozzle_dia),
        )
        t.add_row(
            "Extrusion Coeff", str(config.extrusion_coeff),
            "Auto-Pressurize",
            "[green]ON[/green]" if config.auto_pressurize else "[red]OFF[/red]",
        )
        t.add_row(
            "Jog Step (mm)", str(config.jog_distance),
            "Start from Center",
            "[green]ON[/green]" if config.start_from_center else "[red]OFF[/red]",
        )
        console.print(t)

        console.print("\n[bold yellow]--- Settings ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Toggle Start from Center")
        console.print("[5] Back\n")

        c = Prompt.ask("Choose", choices=["1", "2", "3", "4", "5"])

        if c == "1":
            val = Prompt.ask("New coefficient", default=str(config.extrusion_coeff))
            try:
                config.extrusion_coeff = float(val)
            except ValueError:
                console.print("[red]Invalid number.[/red]")
                time.sleep(1)
        elif c == "2":
            config.auto_pressurize = not config.auto_pressurize
        elif c == "3":
            config.coordinate_mode = "G91" if config.coordinate_mode == "G90" else "G90"
        elif c == "4":
            config.start_from_center = not config.start_from_center
        elif c == "5":
            break


# ============================================================
#  G-CODE TRANSLATOR
# ============================================================
def translate_gcode(config):
    """Translate raw slicer G-code for the bioprinter.

    Returns the output file path on success, or None.
    """
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    files = sorted(
        [f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.gcode', '.txt'))],
        key=lambda x: os.path.getmtime(os.path.join(RAW_DIR, x)),
        reverse=True,
    )

    if not files:
        console.print(f"[red]No .gcode or .txt files in '{RAW_DIR}/'.[/red]")
        console.print("Place your raw G-code files there and try again.")
        time.sleep(2)
        return None

    # --- File selection ---
    t = Table(title=f"Files in '{RAW_DIR}'", show_header=True, header_style="bold green")
    t.add_column("#", justify="right", style="cyan")
    t.add_column("Filename", style="magenta")
    t.add_column("Modified", justify="right", style="green")
    for i, fname in enumerate(files, 1):
        mtime = datetime.fromtimestamp(os.path.getmtime(os.path.join(RAW_DIR, fname)))
        t.add_row(str(i), fname, mtime.strftime('%Y-%m-%d %H:%M'))
    console.print(t)
    console.print("[0] Cancel")

    choice = IntPrompt.ask("Select file", choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0:
        return None

    selected = files[choice - 1]
    input_path = os.path.join(RAW_DIR, selected)

    base, ext = os.path.splitext(selected)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = f"{base}_{stamp}{ext}"
    output_path = os.path.join(OUT_DIR, output_name)

    try:
        with open(input_path, "r") as fh:
            content = fh.readlines()
    except Exception as e:
        console.print(f"[red]Error reading file: {e}[/red]")
        time.sleep(2)
        return None

    console.print(f"\n[green]Translating[/green] '{selected}' \u2192 '{output_name}'\u2026\n")

    # --- Translation state ---
    coord_type = 0 if config.coordinate_mode == "G90" else 1   # 0=abs, 1=rel
    extrusion_coeff = config.extrusion_coeff
    ext_axis = config.extrusion_axis
    extruder = 0        # 0 = Z syringe, 1 = A syringe
    net_extrude = 0.0
    x1, y1, z1, a1, e1 = 0.0, 0.0, 0.0, 0.0, 0.0
    e1_orig = 0.0

    with open(output_path, "w") as out:
        # ---- Initialization header ----
        out.write(config.coordinate_mode + "\n")
        out.write("; --- Initialization Sequence ---\n")
        out.write("G90 ; Absolute positioning for setup\n")

        if config.start_from_center:
            out.write(f"G92 X0 Y0 Z0 {ext_axis}0 ; Zero all axes at center\n")
        else:
            out.write(f"G92 X0 Y0 Z0 {ext_axis}0 ; Zero at bottom-left corner\n")
            out.write("G1 Z30 F300 ; Z-hop to clear dish walls\n")
            out.write("G1 X50 Y50 F300 ; Move to center\n")
            out.write("G1 Z0 F300 ; Drop back down\n")
            out.write(f"G92 X0 Y0 Z0 {ext_axis}0 ; Re-zero at center\n")

        if config.coordinate_mode == "G91":
            out.write("G91 ; Restore relative positioning\n")
        out.write("; ----------------------------------------\n\n")

        # ---- Auto-pressurize ----
        if config.auto_pressurize:
            out.write("; Auto-pressurize syringe\n")
            out.write("G91 ; Relative for pressurize\n")
            out.write(f"G1 {ext_axis}{config.pressurize_amount} F{MAX_FEEDRATE}\n")
            if config.coordinate_mode == "G90":
                out.write("G90 ; Back to absolute\n")
            out.write(f"G92 {ext_axis}0 ; Re-zero extrusion axis\n\n")

        # ---- Process each line ----
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Processing\u2026", total=len(content))

            for line in content:
                original = line
                stripped = line.strip()

                # -- Skip M-codes except fan control --
                if stripped.startswith('M'):
                    if not (stripped.startswith('M106') or stripped.startswith('M107')):
                        progress.advance(task)
                        continue

                # -- Skip metadata-style comments --
                if any(kw in stripped for kw in (
                    'syringe_diameter', 'nozzle_diameter', 'extrusion_coefficient',
                )):
                    progress.advance(task)
                    continue

                # -- Handle G92 E0 reset (resets tracking state) --
                if 'G92 E0' in stripped or f'G92 {ext_axis}0' in stripped:
                    x1, y1, z1, a1, e1 = 0, 0, 0, 0, 0
                    e1_orig = 0

                # -- Pass-through lines: blank, comments, special G-codes --
                if (not stripped or stripped.startswith(';')
                        or 'G90' in stripped or 'G91' in stripped
                        or 'G92' in stripped or 'G21' in stripped
                        or 'G4' in stripped):

                    # Skip standalone G90/G91 (ORCA manages the mode)
                    if ('G90' in stripped or 'G91' in stripped) and 'G9' in original[:3]:
                        progress.advance(task)
                        continue

                    # Translate E→extrusion_axis in G92 commands
                    if 'G92' in stripped and 'E' in stripped:
                        out.write(clamp_feedrate(original.replace('E', ext_axis)))
                    else:
                        out.write(clamp_feedrate(original))

                    progress.advance(task)
                    continue

                # -- Tool change --
                if 'T0' in stripped:
                    out.write('T0\n')
                    extruder = 0
                    progress.advance(task)
                    continue
                if 'T1' in stripped:
                    out.write('T1\n')
                    extruder = 1
                    progress.advance(task)
                    continue

                # -- Runtime extrusion coefficient override (K=value) --
                if stripped.upper().startswith('K'):
                    parts = stripped.split('=')
                    try:
                        extrusion_coeff = float(parts[-1].strip())
                        out.write(f"; extrusion coefficient changed to = {extrusion_coeff}\n")
                    except ValueError:
                        pass
                    progress.advance(task)
                    continue

                # -- Skip raw B/C axis commands --
                if stripped[0].upper() in ('B', 'C'):
                    progress.advance(task)
                    continue

                # ---- Parse G-code fields ----
                fields = {
                    'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None,
                    'I': None, 'J': None, 'R': None, 'T': None, 'E': None,
                    'F': None,
                }
                for token in stripped.split():
                    if token.startswith(';'):
                        break
                    end_comment = token.endswith(';')
                    if end_comment:
                        token = token[:-1]
                    if token and token[0] in fields:
                        try:
                            fields[token[0]] = float(token[1:])
                        except ValueError:
                            pass
                    if end_comment:
                        break

                # If no movement axes present, pass through as-is
                if not any(fields[c] is not None for c in 'XYZAIJRT'):
                    out.write(clamp_feedrate(original))
                    progress.advance(task)
                    continue

                g = fields['G']
                x = fields['X']
                y = fields['Y']
                z = fields['Z']
                a = fields['A']
                i_v = fields['I'] if fields['I'] is not None else 0
                j_v = fields['J'] if fields['J'] is not None else 0
                r = fields['R']
                f = fields['F']
                original_e = fields['E']

                x_val = x if x is not None else 0
                y_val = y if y is not None else 0
                z_val = z if z is not None else 0
                a_val = a if a is not None else 0

                x_rel = (x_val - x1) if x is not None else 0
                y_rel = (y_val - y1) if y is not None else 0
                z_rel = (z_val - z1) if z is not None else 0
                a_rel = (a_val - a1) if a is not None else 0

                # ---- Calculate path length ----
                path_len = 0.0

                if g == 1:
                    if coord_type == 1:     # relative
                        path_len = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                    else:                   # absolute
                        path_len = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)

                elif g in (2, 3):
                    full_circle = False
                    radius = r if r is not None else math.sqrt(i_v**2 + j_v**2)

                    if coord_type == 1:     # relative
                        if x_val != 0 or y_val != 0 or z_val != 0 or a_val != 0:
                            d = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                            val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                            theta = 2 * math.pi - math.acos(val)
                        else:
                            theta = 2 * math.pi
                            full_circle = True
                    else:                   # absolute
                        if x is not None or y is not None or z is not None or a is not None:
                            d = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                            val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                            theta = 2 * math.pi - math.acos(val)
                        else:
                            theta = 2 * math.pi
                            full_circle = True

                    path_len = radius * theta
                    if g == 3 and not full_circle:
                        path_len = 2 * math.pi * radius - path_len

                # ---- Calculate extrusion ----
                if original_e is None:
                    chunk = 0
                    e = None
                else:
                    e_change = original_e if coord_type == 1 else (original_e - e1_orig)

                    if e_change == 0:
                        chunk = 0
                    elif path_len > 0:
                        # Scale by nozzle/syringe cross-section ratio
                        if extruder == 0:
                            chunk = (extrusion_coeff * path_len
                                     * config.z_nozzle_dia**2 / config.z_syringe_dia**2)
                        else:
                            chunk = (extrusion_coeff * path_len
                                     * config.a_nozzle_dia**2 / config.a_syringe_dia**2)
                        if e_change < 0:
                            chunk = -chunk
                    else:
                        # Pure extrusion (no XY movement) — scale by filament/syringe ratio
                        if extruder == 0:
                            chunk = e_change * FILAMENT_DIA**2 / config.z_syringe_dia**2
                        else:
                            chunk = e_change * FILAMENT_DIA**2 / config.a_syringe_dia**2

                    if coord_type == 1:
                        e = chunk
                    else:
                        e = e1 + chunk
                    net_extrude += chunk
                    e1_orig = original_e

                # ---- Build output line ----
                out_parts = []
                if g is not None:
                    out_parts.append(f"G{int(g)}")
                if x is not None:
                    out_parts.append(f"X{x}")
                if y is not None:
                    out_parts.append(f"Y{y}")
                if g in (2, 3):
                    if r is not None:
                        out_parts.append(f"R{r}")
                    if fields['I'] is not None:
                        out_parts.append(f"I{fields['I']}")
                    if fields['J'] is not None:
                        out_parts.append(f"J{fields['J']}")
                if z is not None:
                    out_parts.append(f"Z{z}")
                if a is not None:
                    out_parts.append(f"A{a}")
                if e is not None and g != 0:
                    out_parts.append(f"{ext_axis}{round(e, 3)}")
                if f is not None:
                    out_parts.append(f"F{f}")

                out_line = ' '.join(out_parts)
                out_line = clamp_feedrate(out_line)

                # "NO E" marker — write original, undo extrusion tracking
                if 'NO E' in original:
                    out.write(clamp_feedrate(original))
                    if original_e is not None:
                        if coord_type == 0:
                            e -= chunk
                        net_extrude -= chunk
                else:
                    out.write(out_line + "\n")

                # Update position tracking
                if x is not None:
                    x1 = x_val
                if y is not None:
                    y1 = y_val
                if z is not None:
                    z1 = z_val
                if a is not None:
                    a1 = a_val
                if e is not None:
                    e1 = e

                progress.advance(task)

        # ---- Footer ----
        if config.auto_pressurize:
            out.write(f"\n; Auto-depressurize syringe\n")
            out.write("G91 ; Relative for depressurize\n")
            out.write(f"G1 {ext_axis}-{config.pressurize_amount} F{MAX_FEEDRATE}\n")
            if config.coordinate_mode == "G90":
                out.write("G90 ; Back to absolute\n")

        out.write("\n; --- End of Print ---\n")
        out.write("G91 ; Relative positioning\n")
        out.write("G1 Z30 F300 ; Lift nozzle\n")
        out.write("G90 ; Absolute positioning\n")
        if config.start_from_center:
            out.write("G1 X0 Y0 F300 ; Park at center\n")
        else:
            out.write("G1 X-50 Y-50 F300 ; Park at bottom-left\n")
        out.write("; -------------------\n")

    # ---- Summary ----
    net_vol = net_extrude * math.pi * (config.z_syringe_dia / 2) ** 2 / 1000
    console.print()
    console.print(Panel(
        f"Extrusion: [yellow]{round(net_extrude, 3)} mm[/yellow]  |  "
        f"Volume: [yellow]{round(net_vol, 3)} mL[/yellow]",
        title="[green]Translation Complete[/green]",
        border_style="green",
        expand=False,
    ))

    return output_path


# ============================================================
#  LOAD FILE
# ============================================================
def load_file_menu():
    """Browse translated_gcode/ and select a file. Returns path or None."""
    os.makedirs(OUT_DIR, exist_ok=True)

    files = sorted(
        [f for f in os.listdir(OUT_DIR) if f.lower().endswith(('.gcode', '.txt'))],
        key=lambda x: os.path.getmtime(os.path.join(OUT_DIR, x)),
        reverse=True,
    )

    if not files:
        console.print(f"[red]No files in '{OUT_DIR}/'. Translate a file first.[/red]")
        time.sleep(2)
        return None

    t = Table(title="Translated Files", show_header=True, header_style="bold green")
    t.add_column("#", justify="right", style="cyan")
    t.add_column("Filename", style="magenta")
    t.add_column("Modified", justify="right", style="green")
    for i, fname in enumerate(files, 1):
        mtime = datetime.fromtimestamp(os.path.getmtime(os.path.join(OUT_DIR, fname)))
        t.add_row(str(i), fname, mtime.strftime('%Y-%m-%d %H:%M'))
    console.print(t)
    console.print("[0] Cancel")

    choice = IntPrompt.ask("Select file", choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0:
        return None

    return os.path.join(OUT_DIR, files[choice - 1])


# ============================================================
#  PRINT FILE
# ============================================================
def print_file(printer, filepath, config):
    """Send a translated G-code file to the printer line-by-line.

    Supports pause (Enter) / resume / stop during the print.
    """
    if not printer.is_connected:
        console.print("[red]Printer not connected.[/red]")
        time.sleep(1)
        return

    if not filepath:
        console.print("[red]No file loaded.[/red]")
        time.sleep(1)
        return

    # ---- Pre-print positioning check ----
    if config.start_from_center:
        msg = "Move the bed to the CENTER position."
        prompt_txt = "Bed at center?"
    else:
        msg = "Move the bed to the far BOTTOM-LEFT corner."
        prompt_txt = "Bed at bottom-left?"

    console.print(Panel(f"[yellow]{msg}[/yellow]", border_style="yellow"))
    ready = Prompt.ask(prompt_txt, choices=["y", "n"], default="y")
    if ready != 'y':
        console.print("[red]Print cancelled.[/red]")
        time.sleep(1)
        return

    try:
        with open(filepath, "r") as fh:
            lines = fh.readlines()
    except Exception as e:
        console.print(f"[red]Error reading file: {e}[/red]")
        time.sleep(2)
        return

    console.print(Panel(
        f"[yellow]Printing: {os.path.basename(filepath)}[/yellow]\n"
        "[cyan]Press ENTER to pause.[/cyan]",
    ))

    printer.flush()
    aborted = False

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as prog:
        task = prog.add_task("[cyan]Printing\u2026", total=len(lines))

        for line in lines:
            stripped = line.strip()

            # Skip blanks and comments
            if not stripped or stripped.startswith(';'):
                prog.advance(task)
                continue

            cmd = stripped.split(';')[0].strip()
            if not cmd:
                prog.advance(task)
                continue

            # Send command (auto-clamped by Printer)
            printer.send_nowait(cmd)

            # Wait for 'ok', checking for pause in between
            start = time.time()
            while True:
                # Pause check
                if _check_pause():
                    printer.send_nowait("M220 S0")     # Freeze motion via feedrate override
                    prog.stop()
                    console.print("\n[bold yellow]PAUSED[/bold yellow]")

                    action = Prompt.ask(
                        "(r)esume or (s)top?",
                        choices=["r", "s"],
                        default="r",
                    )

                    if action == 's':
                        aborted = True
                        break

                    printer.send_nowait("M220 S100")   # Restore speed
                    console.print("[green]Resuming\u2026[/green]")
                    prog.start()
                    start = time.time()                 # Reset timeout

                # Read printer response
                if printer.has_data:
                    resp = printer.read_line()
                    if resp and 'ok' in resp.lower():
                        break

                # Timeout (60s covers very long moves)
                if time.time() - start > 60:
                    console.print("[yellow]Warning: no 'ok' in 60 s, continuing.[/yellow]")
                    break

                time.sleep(0.01)

            if aborted:
                break

            prog.advance(task)

        # ---- Wait for final buffered moves ----
        if not aborted:
            prog.update(task, description="[cyan]Finishing buffered moves\u2026")
            try:
                printer.send("M400", timeout=60)
            except Exception:
                pass

    # ---- Post-print ----
    if aborted:
        try:
            printer.send_nowait("M410")                # Quick-stop buffered moves
            time.sleep(0.5)
            printer.flush()
            printer.send_nowait("M220 S100")           # Restore normal speed
            printer.send("G91", timeout=2)
            printer.send("G1 Z30 F300", timeout=10)
            printer.send("G90", timeout=2)
            if config.start_from_center:
                printer.send("G1 X0 Y0 F300", timeout=30)
            else:
                printer.send("G1 X-50 Y-50 F300", timeout=30)
        except Exception as e:
            console.print(f"[dim]Park error: {e}[/dim]")
        console.print("[red]Print stopped. Bed parked.[/red]")
    else:
        console.print("\n[bold green]Print complete![/bold green]")

    time.sleep(2)


# ============================================================
#  MANUAL G-CODE TERMINAL
# ============================================================
def manual_terminal(printer):
    """Free-form G-code terminal — type commands, see responses."""
    if not printer.is_connected:
        console.print("[red]Printer not connected.[/red]")
        time.sleep(1)
        return

    console.clear()
    display_header()
    console.print(Panel(
        "[bold cyan]Manual G-Code Terminal[/bold cyan]\n"
        "Type G-code commands and press Enter.\n"
        "Movement commands default to F300 if no speed is set.\n\n"
        "[yellow]Tip:[/yellow] Send [green]G91[/green] for relative mode, "
        "[green]G90[/green] for absolute.\n"
        "Type [bold yellow]q[/bold yellow] to return to menu.",
        border_style="cyan",
    ))

    printer.flush()

    while True:
        cmd = Prompt.ask("[green]>[/green]")
        if cmd.lower() in ('q', 'quit', 'exit'):
            break
        if not cmd.strip():
            continue

        cmd = clean_gcode(cmd).upper().strip()

        # Default feedrate for movement commands without one
        if (cmd.startswith("G0") or cmd.startswith("G1")) and "F" not in cmd:
            cmd += " F300"

        try:
            responses = printer.send(cmd, timeout=5)
            for r in responses:
                console.print(f"[dim]{r}[/dim]")
        except serial.SerialException as e:
            console.print(f"[red]Serial error: {e}[/red]")
            break
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            break


# ============================================================
#  JOG CONTROL
# ============================================================
def jog_mode(printer, config):
    """Interactive keyboard jog — press keys to move, q to quit.

    Uses raw terminal input (no pynput dependency).
    Each keypress sends one move command and waits for acknowledgment,
    preventing buffer overflows and ensuring precise, predictable motion.
    Hold a key for continuous movement at the OS key-repeat rate.

    Keys:
        W/S  Y+/Y-      A/D  X-/X+
        R/F  Z+/Z-      T/G  Ext-/Ext+
        +/-  Step size   Q    Quit
    """
    if not printer.is_connected:
        console.print("[red]Printer not connected.[/red]")
        time.sleep(1)
        return

    console.clear()
    display_header()
    console.print(Panel(
        f"[bold cyan]Jog Control[/bold cyan]\n\n"
        f"Step: [yellow]{config.jog_distance} mm[/yellow]   "
        f"Speed: F{MAX_FEEDRATE}\n\n"
        " [yellow]W[/yellow]/[yellow]S[/yellow] : Y+ / Y-"
        "      [yellow]A[/yellow]/[yellow]D[/yellow] : X- / X+\n"
        " [yellow]R[/yellow]/[yellow]F[/yellow] : Z+ / Z-"
        "      [yellow]T[/yellow]/[yellow]G[/yellow] : Ext- / Ext+\n"
        " [yellow]+[/yellow]/[yellow]-[/yellow] : Bigger / Smaller step\n"
        " [yellow]Q[/yellow]   : Return to menu\n",
        border_style="cyan",
    ))

    # Switch to relative mode for jogging
    printer.send("G91")

    is_win = sys.platform == 'win32'
    if not is_win:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    try:
        while True:
            key = _read_key()

            if key is None:
                time.sleep(0.01)
                continue

            if key == 'q':
                break

            # Step size adjustment
            if key in ('+', '='):
                if config.jog_step_index < len(JOG_STEPS) - 1:
                    config.jog_step_index += 1
                sys.stdout.write(f"\r  Step: {config.jog_distance} mm       ")
                sys.stdout.flush()
                continue
            if key == '-':
                if config.jog_step_index > 0:
                    config.jog_step_index -= 1
                sys.stdout.write(f"\r  Step: {config.jog_distance} mm       ")
                sys.stdout.flush()
                continue

            # Map key → G-code command
            d = config.jog_distance
            ea = config.extrusion_axis
            cmd = None
            if key == 'w':
                cmd = f"G1 Y{d} F{MAX_FEEDRATE}"
            elif key == 's':
                cmd = f"G1 Y-{d} F{MAX_FEEDRATE}"
            elif key == 'a':
                cmd = f"G1 X-{d} F{MAX_FEEDRATE}"
            elif key == 'd':
                cmd = f"G1 X{d} F{MAX_FEEDRATE}"
            elif key == 'r':
                cmd = f"G1 Z{d} F{MAX_FEEDRATE}"
            elif key == 'f':
                cmd = f"G1 Z-{d} F{MAX_FEEDRATE}"
            elif key == 't':
                cmd = f"G1 {ea}-{d} F{MAX_FEEDRATE}"
            elif key == 'g':
                cmd = f"G1 {ea}{d} F{MAX_FEEDRATE}"

            if cmd:
                try:
                    printer.send(cmd, timeout=2)
                except Exception:
                    pass    # Skip silently if printer is momentarily busy

    finally:
        # Restore terminal state
        if not is_win:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            termios.tcflush(fd, termios.TCIFLUSH)

        # Restore absolute mode
        try:
            printer.send("G90")
        except Exception:
            pass


# ============================================================
#  UPDATE
# ============================================================
def update_orca(printer):
    """Pull latest changes from GitHub; restart if updated."""
    console.print(Panel("[cyan]Fetching updates from GitHub\u2026[/cyan]", border_style="cyan"))
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        console.print("[green]Update successful.[/green]")
        if result.stdout.strip():
            console.print(f"[dim]{result.stdout.strip()}[/dim]")

        if "Already up to date." in result.stdout:
            time.sleep(2)
            return

        console.print("\n[yellow]Restarting ORCA\u2026[/yellow]")
        time.sleep(2)
        printer.disconnect()
        os.execl(sys.executable, sys.executable, *sys.argv)

    except subprocess.CalledProcessError as e:
        console.print("[red]Update failed.[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        time.sleep(3)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        time.sleep(3)


# ============================================================
#  MAIN MENU
# ============================================================
def main():
    config = Config()
    printer = Printer()
    loaded_file = None

    try:
        while True:
            console.clear()
            display_header()

            # ---- Status bar ----
            if printer.is_connected:
                conn = f"[green]Connected ({printer.port})[/green]"
            else:
                conn = "[red]Not Connected[/red]"

            file_disp = (
                f"[cyan]{os.path.basename(loaded_file)}[/cyan]"
                if loaded_file else "[dim]None[/dim]"
            )
            console.print(f"Printer: {conn}")
            console.print(f"File:    {file_disp}\n")

            # ---- Menu options ----
            console.print("[bold yellow]--- Main Menu ---[/bold yellow]")
            choices = ["1", "2", "3", "7", "8", "9"]

            if printer.is_connected:
                console.print("[0] [red]Reset Printer Board[/red]")
                choices.append("0")

            console.print("[1] Connect to Printer")
            console.print("[2] Translate G-Code")
            console.print("[3] Load Translated File")

            if printer.is_connected and loaded_file:
                console.print("[4] [green]Print Loaded File[/green]")
                choices.append("4")
            else:
                console.print("[4] [dim]Print (requires connection & file)[/dim]")

            if printer.is_connected:
                console.print("[5] [cyan]Manual G-Code Terminal[/cyan]")
                console.print("[6] [cyan]Jog Control[/cyan]")
                choices.extend(["5", "6"])
            else:
                console.print("[5] [dim]Manual Terminal (requires connection)[/dim]")
                console.print("[6] [dim]Jog Control (requires connection)[/dim]")

            console.print("[7] Settings")
            console.print("[8] Update from GitHub")
            console.print("[9] Exit\n")

            choices.sort()
            choice = Prompt.ask("[yellow]Choose[/yellow]", choices=choices)

            # ---- Dispatch ----
            if choice == "0":
                reset_menu(printer)

            elif choice == "1":
                connect_menu(printer, config)

            elif choice == "2":
                result = translate_gcode(config)
                if result:
                    load_now = Prompt.ask(
                        "Load this file for printing?",
                        choices=["y", "n"],
                        default="y",
                    )
                    if load_now == 'y':
                        loaded_file = result
                        console.print(f"[green]Loaded {os.path.basename(result)}[/green]")
                        time.sleep(1)

            elif choice == "3":
                result = load_file_menu()
                if result:
                    loaded_file = result
                    console.print(f"[green]Loaded {os.path.basename(result)}[/green]")
                    time.sleep(1)

            elif choice == "4":
                print_file(printer, loaded_file, config)

            elif choice == "5":
                manual_terminal(printer)

            elif choice == "6":
                jog_mode(printer, config)

            elif choice == "7":
                settings_menu(config)

            elif choice == "8":
                update_orca(printer)

            elif choice == "9":
                printer.disconnect()
                console.print("[magenta]Goodbye![/magenta]")
                break

    except KeyboardInterrupt:
        printer.disconnect()
        console.print("\n[magenta]Goodbye![/magenta]")


if __name__ == "__main__":
    main()
