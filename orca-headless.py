import math
import os
import time
import sys
import re
import threading
import queue
import json
import signal
import atexit
from datetime import datetime

# Platform-specific imports for terminal control
if sys.platform == 'win32':
    import msvcrt
else:
    import select
    import tty
    import termios

# Import Rich
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import IntPrompt, Prompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
except ImportError:
    print("Please install the 'rich' library: pip install rich")
    sys.exit()

# Import pyserial for printer communication
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Please install the 'pyserial' library: pip install pyserial==3.5")
    sys.exit()

# Initialize the Rich Console
console = Console()

# ==========================================
# --- CONFIGURATION PARAMETERS ---
# ==========================================
COORDINATE_MODE = "G90"         # 'G90' for Absolute, 'G91' for Relative
EXTRUSION_AXIS = "B"            # The target axis for extrusion ('B' or 'C')
Z_SYRINGE_DIAMETER = 4.9        # Inner diameter in mm (4.9 for 1mL BD syringe)
A_SYRINGE_DIAMETER = 4.9
Z_NOZZLE_DIAMETER = 2           # Nozzle diameter in mm
A_NOZZLE_DIAMETER = 0.2
EXTRUSION_COEFFICIENT = 0.33    # Scaling factor for extrusion

# Auto-Pressurization Settings
DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 0.2
PRESSURIZE_SPEED = 300          # Capped at 300

# Jog Settings
JOG_DISTANCE = 0.2              # Distance in mm per keystroke tick
JOG_SPEED_MM_MIN = 300          # The F-value for jogging speed
HIGH_PRECISION_JOG = True       # Start in high precision mode

# Bed Origin Settings
START_FROM_CENTER = False       # If True, expects bed to start in center, skipping init travel

# Serial Connection Settings
BAUD_RATE = 115200

# ==========================================
# --- Z SAFETY CONFIGURATION ---
# ==========================================
# Z_SAFE_HOME_HEIGHT is the position Z must be AT OR BELOW before G28 is safe
# to run.  When the firmware homes, it drives Z toward its endstop (the TOP of
# the rail).  If Z is already near the top the carriage crashes.  Setting this
# to a low value (e.g. 5 mm) guarantees Z is well away from the top before any
# homing sequence starts.
#
# Z_PREDROP_HEIGHT is the absolute position Z is commanded to before G28.
# It must be <= Z_SAFE_HOME_HEIGHT to be effective.  The move uses G90 so the
# value is always interpreted as an absolute coordinate regardless of the
# current positioning mode.
Z_SAFE_HOME_HEIGHT  = 5.0   # mm — threshold: if Z is above this, drop first
Z_PREDROP_HEIGHT    = 2.0   # mm — absolute target before G28 (must be < Z_SAFE_HOME_HEIGHT)
Z_PREDROP_SPEED     = 300   # mm/min for the safety drop move

# ==========================================
# --- PERSISTENT STATE CONFIGURATION ---
# ==========================================
# All axis positions (X, Y, Z, B, C) are written to this file every time any
# axis moves so that a crash, power outage, or forced kill cannot leave the
# safety guard blind on the next run.  The file lives next to the script so it
# travels with the project folder.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orca_state.json")

# Canonical axis list — order is display order throughout the UI.
AXES = ("x", "y", "z", "b", "c")

# ==========================================
# --- STATE VARIABLES ---
# ==========================================
printer_conn = None
loaded_filepath = None

printer_lock = threading.Lock()
printer_response_queue = queue.Queue()

printer_listener_running = False
printer_listener_thread = None

# Live axis position table.  Each entry is float mm or None (= unknown).
# Access and mutation MUST go through the helper functions below so every
# change is immediately persisted to disk.
_axis_pos = {a: None for a in AXES}


# ============================================================
# --- PERSISTENT AXIS STATE ---
# ============================================================

def _save_axis_state():
    """
    Atomically persist _axis_pos to disk.

    Uses a write-then-rename pattern so a kill mid-write cannot corrupt the
    file — the OS swap is atomic on every POSIX filesystem and on NTFS.
    Failures are logged but never raised so the safety logic degrades
    gracefully to the conservative always-drop default.
    """
    try:
        payload = {
            "axes":     dict(_axis_pos),
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        console.print(f"[dim yellow]Warning: could not save axis state ({e})[/dim yellow]")


def load_axis_state():
    """
    Load all axis positions from disk on startup.

    Populates _axis_pos in-place.  Prints a dim status line for every axis
    that has a known position so the operator can confirm the values look
    sane before connecting or homing.

    Returns True if the file existed and was readable, False otherwise.
    """
    global _axis_pos

    if not os.path.exists(STATE_FILE):
        _axis_pos = {a: None for a in AXES}
        return False

    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        saved = data.get("axes", {})
        ts    = data.get("saved_at", "unknown time")

        # Accept both the old single-key format {"last_known_z": ...} and the
        # new multi-axis format {"axes": {"x": ..., "z": ..., ...}} so existing
        # state files from v1.0.20 are not silently lost on first upgrade.
        if "last_known_z" in data and "axes" not in data:
            saved = {"z": data.get("last_known_z")}

        for a in AXES:
            _axis_pos[a] = saved.get(a)  # None if key absent

        known = {a: v for a, v in _axis_pos.items() if v is not None}
        if known:
            parts = "  ".join(f"{a.upper()}={v:.2f}" for a, v in known.items())
            console.print(f"[dim]State restored ({ts}): {parts} mm[/dim]")
        else:
            console.print("[dim]State file found but all axes unknown.[/dim]")

        return True

    except Exception as e:
        console.print(f"[dim yellow]Could not read state file ({e}); all axes start unknown.[/dim yellow]")
        _axis_pos = {a: None for a in AXES}
        return False


# ---- Axis accessors / mutators ----------------------------------------

def get_axis(axis: str):
    """
    Return the last known position of *axis* in mm, or None if unknown.

    axis must be one of AXES ('x', 'y', 'z', 'b', 'c'), case-insensitive.
    """
    return _axis_pos.get(axis.lower())


def set_axis(axis: str, value: float):
    """
    Set *axis* to *value* mm and persist immediately.

    Use for absolute moves and post-homing resets.
    """
    _axis_pos[axis.lower()] = value
    _save_axis_state()


def offset_axis(axis: str, delta: float):
    """
    Add *delta* mm to *axis* and persist immediately.

    If the axis position is currently unknown (None) it stays None — we
    cannot compute an absolute position from a relative offset without a
    reference.  Call set_axis() first to establish a reference.
    """
    a = axis.lower()
    cur = _axis_pos.get(a)
    if cur is not None:
        _axis_pos[a] = cur + delta
        _save_axis_state()
    # else: remains None — do not call _save_axis_state() for a no-op


def clear_axes(*axes):
    """
    Mark one or more axes as unknown (None) and persist.

    Call with no arguments to clear every axis (used after a board reset or
    fresh connect when carriage position is genuinely unknown).

    Examples
    --------
    clear_axes()              # all axes unknown
    clear_axes('x', 'y')     # only X and Y unknown, others unchanged
    """
    targets = [a.lower() for a in axes] if axes else list(AXES)
    for a in targets:
        _axis_pos[a] = None
    _save_axis_state()


# Convenience shorthands kept for Z since it is the safety-critical axis
# referenced throughout the code base — avoids renaming every callsite.
def get_z():  return get_axis("z")
def set_z(v): set_axis("z", v)
def clear_z(): clear_axes("z")


# ============================================================
# --- GRACEFUL SHUTDOWN (atexit + signals) ---
# ============================================================

def _emergency_save():
    """
    Final-chance axis state flush called by atexit and signal handlers.

    By the time this runs the Rich console may be in a bad state, so we
    bypass it and write directly — _save_axis_state() already handles its
    own exceptions silently.
    """
    try:
        _save_axis_state()
    except Exception:
        pass  # Truly last resort — cannot do anything useful here


def _signal_handler(signum, frame):
    """
    Handle SIGINT (Ctrl+C) and SIGTERM (kill / system shutdown).

    Saves Z state, closes the serial port cleanly if possible, then exits
    with an appropriate code so the shell knows the process was signalled.
    """
    global printer_listener_running, printer_conn

    _emergency_save()

    # Stop the listener thread so it does not block port closure
    printer_listener_running = False

    if printer_conn:
        try:
            printer_conn.close()
        except Exception:
            pass

    # Exit with the conventional signal exit code (128 + signal number)
    sys.exit(128 + signum)


# Register the final-chance save for all normal exit paths (sys.exit,
# end of main(), unhandled exceptions, etc.)
atexit.register(_emergency_save)

# Register the signal handler for external kill signals.
# SIGINT  = Ctrl+C in the terminal
# SIGTERM = `kill <pid>` and most OS/service-manager shutdowns
signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# SIGBREAK is a Windows-only signal sent by Ctrl+Break; register it only
# on Windows so the import does not fail on POSIX systems.
if sys.platform == "win32":
    try:
        signal.signal(signal.SIGBREAK, _signal_handler)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


def display_header():
    # Show the awesome ASCII splash art on the main menus
    splash = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣸⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣧⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣿⣿⣿⣿⡆⠀⠀⠀⠀⠀⢀⣀⣄⡀⠰⠴⣶⣶⣤⣤⡀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣿⣿⣿⣿⣿⡇⠀⢀⣤⣶⣻⣾⣿⣴⣴⣾⣿⣿⣿⣿⣿⣿⡆
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣴⣿⣿⣿⣿⣿⣿⣥⣾⠿⢿⣿⣽⣾⣿⣿⣿⣿⣿⣿⣿⠿⢿⡿⣧
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⠟⠉⠀⠀⠀⣸⣿⣿⣿⣿⡿⠟⠛⠋⠉⠐⠊⠡⢹⢚    __   ____     ____         
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣠⣤⣤⡴⠂⠐⠒⢨⣿⣿⣿⣿⣿⣿⣤⣆⣤⣠⣴⣾⣿⣷⡿⠋⠁⠀⠀⠀⠀⠀⠐⣁⠎⠀⡘  / __ \|  __ \ / ____|   /\   
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢐⣠⣤⣶⣾⣿⣿⣿⣿⣿⣆⡀⡀⣀⣨⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡜⠀⠀⡐⠀ | |  | | |__) | |       /  \   
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠄⠀⠄⠀⠀⠀⠀⠀⠂⠀⠀ | |  | |  _  /| |      / /\ \  
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣴⡶⠿⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡠⠂⠀⠀⠀ | |__| | | \ \| |____ / ____ \ 
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣵⣿⣿⣅⠀⠀⠀⠀⢈⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠖⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠂⠀⠀⠀⠀⠀  \____/|_|  \_\\_____/_/    \_\
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣶⣦⣌⠁⠀⠉⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡏⡞⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠜⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣀⣀⣤⢤⢤⡴⢶⣾⡿⠿⣛⠩⠀⠉⠉⠙⠛⠻⠿⢏⡀⠀⠀⠀⠙⠻⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢈⡷⠀⠀⠀⠀⠀⠀⠀⠀⣠⣷⣿⡀⠀⠀⠀⠀⠀⠀⠀         [cyan]v1.0.21[/cyan]
⢠⠖⠋⠉⠀⢀⠀⠂⣌⢇⠀⣰⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⠀⡀⠀⠀⢀⣽⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⣐⠰⠂⠀⠀⠀⠀⡀⣠⣴⣾⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠛⠓⠒⠲⢤⣀⣐⣤⡞⣸⢊⠥⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠀⢀⣤⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⢄⣀⠀⠠⠤⠴⠂⠈⠁⢰⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⠃⠀⠀⠸⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢖⣦⣀⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠾⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⡿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""
    splash = splash.replace('\u2800', ' ')
    console.print(splash, style="bold white")


# ============================================================
# --- SHARED HELPERS ---
# ============================================================

def drain_queue():
    """Empty the response queue so stale boot/echo lines can't trigger false 'ok' hits."""
    while not printer_response_queue.empty():
        try:
            printer_response_queue.get_nowait()
        except queue.Empty:
            break


def build_config_table(title, include_jog=False):
    """Build the two-column configuration table shared by Settings and the translation review."""
    t = Table(show_header=True, header_style="bold yellow", expand=True,
              title=f"[bold cyan]{title}[/bold cyan]")
    t.add_column("Parameter")
    t.add_column("Value", style="cyan")
    t.add_column("Parameter")
    t.add_column("Value", style="cyan")

    t.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
    t.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
    t.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
    t.add_row("Extrusion Coeff.", str(EXTRUSION_COEFFICIENT),
              "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]")
    if include_jog:
        t.add_row("Jog Precision",
                  "[green]HIGH[/green]" if HIGH_PRECISION_JOG else "[yellow]LOW[/yellow]", "", "")
    return t


def pick_file(directory, title, show_size=False):
    """List .gcode/.txt files in a directory (newest first) and return the chosen filename, or None."""
    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(directory) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(f"[bold red]No files found in '{directory}'.[/bold red]", border_style="red"))
        time.sleep(2)
        return None

    files.sort(key=lambda x: os.path.getmtime(os.path.join(directory, x)), reverse=True)

    table = Table(show_header=True, header_style="bold green", title=f"[bold cyan]{title}[/bold cyan]")
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("Filename", style="magenta")
    table.add_column("Last Modified", justify="right", style="green")
    if show_size:
        table.add_column("Size", justify="right", style="yellow")

    for i, f in enumerate(files):
        full = os.path.join(directory, f)
        dt_str = datetime.fromtimestamp(os.path.getmtime(full)).strftime('%Y-%m-%d %H:%M:%S')
        if show_size:
            table.add_row(str(i + 1), f, dt_str, f"{os.path.getsize(full) / 1024:.1f} KB")
        else:
            table.add_row(str(i + 1), f, dt_str)

    console.print(table)
    console.print("[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select a file[/bold yellow]",
                           choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0:
        return None
    return files[choice - 1]


# ============================================================
# --- SERIAL LISTENER THREAD ---
# ============================================================

def serial_listener():
    """Background thread: continuously reads lines from the serial port into a queue."""
    global printer_listener_running, printer_conn

    while printer_listener_running:
        try:
            if printer_conn and printer_conn.is_open:
                line = printer_conn.readline()
                if line:
                    decoded = line.decode('utf-8', errors='ignore').strip()
                    if decoded:
                        printer_response_queue.put(decoded)
            else:
                time.sleep(0.05)
        except serial.SerialException:
            time.sleep(0.1)
        except Exception:
            time.sleep(0.1)


# ============================================================
# --- SAFE COMMAND SENDER ---
# ============================================================

def send_gcode(command, timeout=15, retries=3, wait_for_ok=True):
    """Send a G-code command with retry logic and ok/error/busy/resend handling."""
    global printer_conn

    if not printer_conn or not printer_conn.is_open:
        raise RuntimeError("Printer not connected")

    command = command.strip()
    if not command:
        return True

    for _ in range(retries):
        try:
            with printer_lock:
                printer_conn.write((command + '\n').encode('utf-8'))
                printer_conn.flush()

            if not wait_for_ok:
                return True

            start = time.time()
            while time.time() - start < timeout:
                try:
                    response = printer_response_queue.get(timeout=0.25)
                except queue.Empty:
                    continue

                response_lower = response.lower()

                if response_lower.startswith("ok"):
                    return True
                if "error" in response_lower:
                    console.print(f"[bold red]{response}[/bold red]")
                    break
                if "resend" in response_lower:
                    break
                if response_lower.startswith("busy"):
                    start = time.time()  # Reset timeout on busy
                    continue
                # Informational line (temperature reports, echo, etc.)
                console.print(f"[dim]{response}[/dim]")

            console.print(f"[yellow]Retrying:[/yellow] {command}")

        except serial.SerialTimeoutException:
            console.print("[yellow]Serial timeout, retrying...[/yellow]")

        except serial.SerialException as e:
            if "temporarily unavailable" in str(e).lower():
                time.sleep(0.1)
                continue
            raise

        time.sleep(0.25)

    raise RuntimeError(f"Failed command after {retries} retries: {command}")


# ============================================================
# --- SAFE HOMING (Z PRE-DROP + SIMULTANEOUS G28) ---
# ============================================================

def safe_home_all_axes():
    """
    Home all axes safely.

    Problem: G28 drives Z toward its endstop at the TOP of the rail.  If Z is
    already near the top (e.g. 90 mm) the carriage crashes before StallGuard
    can trigger.

    Fix applied here:
      1. Switch to absolute mode temporarily.
      2. Drop Z (and only Z) to Z_PREDROP_HEIGHT so it is well clear of the
         top endstop before any homing command is sent.
      3. Issue a single G28 X Y Z so ALL axes home simultaneously — required
         to prevent the needle hitting the petri dish edge.
      4. Restore the user's coordinate mode.

    The pre-drop uses G1 (not G0) so feedrate is explicit and controllable.
    M400 is sent after the drop to flush the planner before G28 fires.

    All axis positions are updated via set_axis() / clear_axes() so every
    change is immediately persisted to disk.
    """
    # Decide whether a pre-drop is actually needed.
    # If we have no position data for Z, always drop (safe default).
    z_now = get_z()
    needs_drop = (z_now is None) or (z_now > Z_SAFE_HOME_HEIGHT)

    if needs_drop:
        console.print(
            f"[bold yellow]Z-SAFETY:[/bold yellow] Dropping Z to {Z_PREDROP_HEIGHT} mm "
            f"before homing (last known Z = "
            f"{'unknown' if z_now is None else f'{z_now:.1f} mm'})..."
        )
        # Use absolute mode for the safety drop regardless of current mode
        send_gcode("G90")
        send_gcode(f"G1 Z{Z_PREDROP_HEIGHT} F{Z_PREDROP_SPEED}")
        send_gcode("M400")   # Wait for move to complete before homing
        set_axis("z", Z_PREDROP_HEIGHT)
    else:
        console.print(
            f"[dim]Z at {z_now:.1f} mm — within safe range, no pre-drop needed.[/dim]"
        )

    # Home all axes simultaneously in one command so X/Y/Z move together.
    # This prevents the needle from catching the petri dish edge.
    console.print("[bold cyan]Homing all axes simultaneously (G28 X Y Z)...[/bold cyan]")
    send_gcode("G28 X Y Z", timeout=180)

    # After homing the firmware resets X/Y/Z to 0.  B/C (extrusion) are not
    # homed so their absolute positions are now unknown relative to any
    # previous reference — mark them as unknown.
    set_axis("x", 0.0)
    set_axis("y", 0.0)
    set_axis("z", 0.0)
    clear_axes("b", "c")


# ============================================================
# --- SETTINGS MENU ---
# ============================================================

def settings_menu():
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE, HIGH_PRECISION_JOG

    while True:
        console.clear()
        display_header()
        console.print(build_config_table("Current Configuration", include_jog=True))

        console.print("\n[bold yellow]--- Options Menu ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Toggle Jog Precision Mode")
        console.print("[5] Return to Main Menu\n")

        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=["1", "2", "3", "4", "5"])

        if choice == "1":
            new_coeff = Prompt.ask("Enter new Extrusion Coefficient", default=str(EXTRUSION_COEFFICIENT))
            try:
                EXTRUSION_COEFFICIENT = float(new_coeff)
            except ValueError:
                console.print("[bold red]Invalid number.[/bold red]")
                time.sleep(1.5)
        elif choice == "2":
            DO_AUTO_PRESSURIZE = not DO_AUTO_PRESSURIZE
        elif choice == "3":
            COORDINATE_MODE = "G91" if COORDINATE_MODE == "G90" else "G90"
        elif choice == "4":
            HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
        elif choice == "5":
            break


# ============================================================
# --- CONNECT TO PRINTER ---
# ============================================================

def connect_to_printer():
    global printer_conn, printer_listener_running, printer_listener_thread

    # Stop any existing listener and close the old connection
    if printer_conn and printer_conn.is_open:
        try:
            printer_listener_running = False
            if printer_listener_thread and printer_listener_thread.is_alive():
                printer_listener_thread.join(timeout=2)
            printer_conn.close()
        except Exception:
            pass

    ports = serial.tools.list_ports.comports()
    if not ports:
        console.print("[bold red]No serial ports found. Make sure the printer is plugged in.[/bold red]")
        time.sleep(2)
        return

    console.print("[bold cyan]Available Ports:[/bold cyan]")
    for i, port in enumerate(ports):
        console.print(f"[{i + 1}] {port.device} - {port.description}")
    console.print("[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select the port to connect to[/bold yellow]",
                           choices=[str(i) for i in range(len(ports) + 1)])
    if choice == 0:
        return

    selected_port = ports[choice - 1].device

    try:
        with console.status(f"[bold green]Connecting to {selected_port} at {BAUD_RATE} baud...", spinner="dots"):
            printer_conn = serial.Serial(
                selected_port, BAUD_RATE, timeout=1, write_timeout=5,
                exclusive=True if sys.platform == "darwin" else None
            )

            # Hard reset: toggle DTR to reboot the board's serial state
            printer_conn.dtr = False
            time.sleep(1.0)
            printer_conn.reset_input_buffer()
            printer_conn.reset_output_buffer()
            printer_conn.dtr = True

            # Wait for board to fully boot before sending anything
            time.sleep(4)
            printer_conn.reset_input_buffer()

            # Start the background listener thread
            printer_listener_running = True
            printer_listener_thread = threading.Thread(target=serial_listener, daemon=True)
            printer_listener_thread.start()

        # Clear queued boot text, then confirm firmware identity (outside the status
        # context so M115 lines can print cleanly without fighting the spinner)
        drain_queue()
        send_gcode("M115", timeout=10)

        # After a fresh connect we don't know where any axis is — mark all as
        # unknown so the next G28 unconditionally performs the safety pre-drop.
        # clear_axes() also persists this to disk immediately.
        clear_axes()

        console.print(f"[bold green]Successfully connected to {selected_port}![/bold green]")
        time.sleep(1)

    except Exception as e:
        console.print(f"[bold red]Failed to connect: {e}[/bold red]")
        printer_conn = None
        printer_listener_running = False
        time.sleep(2)


# ============================================================
# --- RESET PRINTER ---
# ============================================================

def reset_printer_board():
    """Forces a hard reboot and serial flush to clear hangs."""
    global printer_conn

    if not printer_conn:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1.5)
        return

    console.print("[bold yellow]Resetting printer board...[/bold yellow]")
    try:
        old_wt = printer_conn.write_timeout
        try:
            printer_conn.write_timeout = 1
            with printer_lock:
                printer_conn.reset_output_buffer()
                printer_conn.write(b"M112\n")
                printer_conn.flush()
        except Exception:
            pass
        finally:
            printer_conn.write_timeout = old_wt

        time.sleep(0.3)

        # Hardware reset via DTR toggle
        printer_conn.dtr = False
        time.sleep(1.0)
        printer_conn.dtr = True
        time.sleep(4)

        printer_conn.reset_input_buffer()
        printer_conn.reset_output_buffer()
        drain_queue()

        # After a board reset, all axis positions are unknown — persist that.
        clear_axes()

        console.print("[bold green]Printer reset complete. Give it a moment to finish booting.[/bold green]")
        time.sleep(2)

    except Exception as e:
        console.print(f"[bold red]Reset failed: {e}[/bold red]")
        console.print("[yellow]Tip: If the port is completely locked, physically unplug the USB cable and plug it back in.[/yellow]")
        time.sleep(2)


# ============================================================
# --- JOG MENU ---
# ============================================================

def interactive_jog_menu():
    global printer_conn, HIGH_PRECISION_JOG

    if not printer_conn:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1.5)
        return "quit"

    console.clear()
    display_header()

    mode_str = ("[bold green]HIGH (Instant Stop, Choppy)[/bold green]" if HIGH_PRECISION_JOG
                else "[bold yellow]LOW (Smooth Glide, Slight Coast)[/bold yellow]")

    console.print(Panel(
        f"[bold cyan]Jog Control[/bold cyan]\n"
        f"Precision Mode: {mode_str}\n\n"
        f"Press or hold keys to move the printer. Commands are sent at F{JOG_SPEED_MM_MIN} in {JOG_DISTANCE}mm chunks.\n\n"
        " [bold yellow]W[/bold yellow] : +Y    [bold yellow]S[/bold yellow] : -Y\n"
        " [bold yellow]A[/bold yellow] : -X    [bold yellow]D[/bold yellow] : +X\n"
        " [bold yellow]R[/bold yellow] : +Z    [bold yellow]F[/bold yellow] : -Z\n"
        f" [bold yellow]T[/bold yellow] : -{EXTRUSION_AXIS}    [bold yellow]G[/bold yellow] : +{EXTRUSION_AXIS}\n\n"
        "Press [bold magenta]'p'[/bold magenta] to swap between High and Low Precision.\n"
        "Press [bold red]'q'[/bold red] to return to the main menu.",
        border_style="cyan"
    ))

    # Switch to relative mode for jogging; listener thread handles responses
    drain_queue()
    send_gcode("G91", wait_for_ok=False)

    is_windows = sys.platform == 'win32'
    fd = None
    old_settings = None
    if not is_windows:
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            fd = None
            old_settings = None

    toggle_requested = False
    in_flight_commands = 0
    last_command_time = time.time()

    try:
        while True:
            # Drain any pending ok responses from the queue
            while not printer_response_queue.empty():
                try:
                    resp = printer_response_queue.get_nowait()
                    if 'ok' in resp.lower():
                        in_flight_commands = max(0, in_flight_commands - 1)
                except queue.Empty:
                    break

            # Stale in-flight counter safety reset
            if in_flight_commands > 0 and (time.time() - last_command_time) > 0.5:
                in_flight_commands = 0

            char = None
            if is_windows:
                if msvcrt.kbhit():
                    char = msvcrt.getch().decode('utf-8', errors='ignore').lower()
            else:
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    char = sys.stdin.read(1).lower()

            if char:
                if char == 'q':
                    break
                elif char == 'p':
                    HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
                    toggle_requested = True
                    break

                limit = 0 if HIGH_PRECISION_JOG else 2

                if in_flight_commands <= limit:
                    dx, dy, dz, de = 0.0, 0.0, 0.0, 0.0

                    if char == 'w':   dy += JOG_DISTANCE
                    elif char == 's': dy -= JOG_DISTANCE
                    elif char == 'a': dx -= JOG_DISTANCE
                    elif char == 'd': dx += JOG_DISTANCE
                    elif char == 'r': dz += JOG_DISTANCE
                    elif char == 'f': dz -= JOG_DISTANCE
                    elif char == 't': de -= JOG_DISTANCE
                    elif char == 'g': de += JOG_DISTANCE

                    if dx != 0 or dy != 0 or dz != 0 or de != 0:
                        cmd = "G1"
                        if dx != 0: cmd += f" X{dx:.2f}"
                        if dy != 0: cmd += f" Y{dy:.2f}"
                        if dz != 0: cmd += f" Z{dz:.2f}"
                        if de != 0: cmd += f" {EXTRUSION_AXIS}{de:.2f}"
                        cmd += f" F{JOG_SPEED_MM_MIN}"

                        if HIGH_PRECISION_JOG:
                            send_gcode(cmd, wait_for_ok=False)
                            send_gcode("M400", wait_for_ok=False)
                            in_flight_commands += 2
                        else:
                            send_gcode(cmd, wait_for_ok=False)
                            in_flight_commands += 1

                        # Update all moved axes (jogging is always relative).
                        # offset_axis() silently skips axes that are still None
                        # so we never fabricate a position from thin air.
                        if dx != 0: offset_axis("x", dx)
                        if dy != 0: offset_axis("y", dy)
                        if dz != 0: offset_axis("z", dz)
                        if de != 0: offset_axis(EXTRUSION_AXIS.lower(), de)

                        last_command_time = time.time()

    finally:
        if not is_windows and fd is not None and old_settings is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
        # Return to absolute mode
        send_gcode("G90", wait_for_ok=False)

    return "reload" if toggle_requested else "quit"


# ============================================================
# --- MANUAL G-CODE TERMINAL ---
# ============================================================

def manual_control_menu():
    global printer_conn

    if not printer_conn:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1.5)
        return

    console.clear()
    display_header()
    console.print(Panel(
        "[bold cyan]Manual G-Code Terminal[/bold cyan]\n"
        "Type your G-Code commands and press Enter.\n"
        "Movement commands (G0/G1) default to F300 if no speed is specified.\n\n"
        "[bold yellow]TIP:[/bold yellow] Send [bold green]G28[/bold green] to safely home all axes.\n"
        "[bold yellow]     Z will be lowered to a safe height first, then all axes home simultaneously.[/bold yellow]\n"
        "Send [bold green]G91[/bold green] to switch to Relative Mode for manual moves.\n\n"
        "Type [bold yellow]'q'[/bold yellow] or [bold yellow]'quit'[/bold yellow] to return to the main menu.",
        border_style="cyan"
    ))

    # Track whether we're currently in relative mode so axis tracking stays accurate
    current_mode_relative = False

    while True:
        cmd = Prompt.ask("[bold green]>[/bold green]")

        if cmd.lower() in ['q', 'quit', 'exit']:
            break
        if not cmd.strip():
            continue

        # Normalize smart dashes and spaced axis values from copy-paste
        cmd_clean = re.sub(r'[–—−]', '-', cmd)
        cmd_clean = re.sub(r'([A-Z])\s+([-\.0-9])', r'\1\2', cmd_clean, flags=re.IGNORECASE)
        cmd_upper = cmd_clean.upper().strip()

        if cmd_upper.startswith("G0") or cmd_upper.startswith("G1"):
            if "F" not in cmd_upper:
                cmd_upper += " F300"

        # -------------------------------------------------------
        # Intercept G28: route through the safe homing function
        # instead of sending the raw command, so the Z pre-drop
        # and simultaneous-axis requirement are always enforced.
        # -------------------------------------------------------
        if cmd_upper.startswith("G28"):
            try:
                safe_home_all_axes()
            except Exception as e:
                console.print(f"[bold red]Homing error:[/bold red] {e}")
            continue

        # Track mode switches so position tracking stays valid
        if cmd_upper.strip() == "G91":
            current_mode_relative = True
        elif cmd_upper.strip() == "G90":
            current_mode_relative = False

        # G92 (set position / re-zero): update the state table to match
        # whatever the firmware is now told to believe.
        if cmd_upper.startswith("G92"):
            for axis in AXES:
                m = re.search(rf'{axis.upper()}([-\d.]+)', cmd_upper)
                if m:
                    try:
                        set_axis(axis, float(m.group(1)))
                    except ValueError:
                        pass
            # G92 with no arguments zeros all axes in the firmware
            if not any(re.search(rf'{a.upper()}[-\d.]', cmd_upper) for a in AXES):
                for a in AXES:
                    set_axis(a, 0.0)

        # Update axis positions from manual G0/G1 commands and persist each
        # change immediately so a crash cannot leave the state stale.
        if cmd_upper.startswith(("G0", "G1")):
            for axis in AXES:
                m = re.search(rf'{axis.upper()}([-\d.]+)', cmd_upper)
                if m:
                    try:
                        val = float(m.group(1))
                        if current_mode_relative:
                            offset_axis(axis, val)
                        else:
                            set_axis(axis, val)
                    except ValueError:
                        pass

        try:
            if cmd_upper.startswith("G29"):
                send_gcode(cmd_upper, timeout=180)
            else:
                send_gcode(cmd_upper)
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")


# ============================================================
# --- GCODE TRANSLATION ---
# ============================================================

def translate_gcode():
    global loaded_filepath

    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)
        console.print(Panel(
            f"[bold yellow]Created '{raw_dir}' directory.[/bold yellow]\n\nPlease place your raw files there.",
            title="[bold red]Action Required"))
        time.sleep(2)
        return

    os.makedirs(out_dir, exist_ok=True)

    selected_file = pick_file(raw_dir, "Available Files in 'raw_gcode'")
    if not selected_file:
        return

    input_filepath = os.path.join(raw_dir, selected_file)

    if not review_settings_before_translation(selected_file):
        return

    base_name, ext = os.path.splitext(selected_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{base_name}_{timestamp}{ext}"
    output_filepath = os.path.join(out_dir, output_filename)

    try:
        with open(input_filepath, "r") as file:
            content = file.readlines()
    except FileNotFoundError:
        console.print(f"[bold red]Error: '{input_filepath}' not found.[/bold red]")
        time.sleep(2)
        return

    coordinate_type = 0 if COORDINATE_MODE == "G90" else 1
    extrusion_coefficient = EXTRUSION_COEFFICIENT
    extruder = 0
    netExtrude = 0
    netExtrude_A = 0

    console.print(
        f"\n[bold green]Translating[/bold green] [cyan]'{selected_file}'[/cyan] "
        f"-> [cyan]'{output_filename}'[/cyan]...\n"
    )

    f_new = open(output_filepath, "w+t")
    try:
        f_new.write(COORDINATE_MODE + "\n")
        f_new.write("; --- Initialization Sequence ---\n")
        f_new.write("; SAFETY: Drop Z to a safe height before homing so it cannot\n")
        f_new.write(";         crash into the top of the rail during G28.\n")
        f_new.write(";         All axes then home simultaneously (single G28 X Y Z)\n")
        f_new.write(";         to prevent the needle hitting the petri dish edge.\n")
        f_new.write("G90 ; Absolute mode for the safety pre-drop\n")
        f_new.write(f"G1 Z{Z_PREDROP_HEIGHT} F{Z_PREDROP_SPEED} ; Lower Z to safe height before homing\n")
        f_new.write("M400 ; Wait for Z pre-drop to complete\n")
        f_new.write("G28 X Y Z ; Home ALL axes simultaneously (StallGuard)\n")
        f_new.write("G91 ; Relative positioning to travel to print start\n")
        f_new.write("G1 X50 Y67 Z-89 F300 ; Move from home to the print start position\n")
        f_new.write("G90 ; Back to absolute positioning\n")
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Zero all axes at the print start position\n")

        if COORDINATE_MODE == "G91":
            f_new.write("G91 ; Restore relative positioning\n")
        f_new.write("; ----------------------------------------\n\n")

        if DO_AUTO_PRESSURIZE:
            f_new.write("; Auto-pressurize syringe\n")
            f_new.write("G91 ; Switch to relative positioning for pressurize\n")
            f_new.write(f"G1 {EXTRUSION_AXIS}{PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")
            if COORDINATE_MODE == "G90":
                f_new.write("G90 ; Switch back to absolute positioning\n")
            f_new.write(f"G92 {EXTRUSION_AXIS}0 ; Re-zero the extrusion axis after pressurizing\n\n")

        x1, y1, e1, a1, z1 = 0.0, 0.0, 0.0, 0.0, 0.0
        e1_orig = 0.0

        with Progress(
            SpinnerColumn(spinner_name="monkey"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40, style="magenta", complete_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:

            task = progress.add_task("[cyan]Processing G-Code...", total=len(content))

            for line in content:
                original_line = line
                stripped_line = line.strip()

                if stripped_line.startswith('M'):
                    if not (stripped_line.startswith('M106') or stripped_line.startswith('M107')):
                        progress.advance(task)
                        continue

                if ("syringe_diameter" in stripped_line
                        or "nozzle_diameter" in stripped_line
                        or "extrusion_coefficient" in stripped_line):
                    progress.advance(task)
                    continue

                if 'G92 E0' in stripped_line or f'G92 {EXTRUSION_AXIS}0' in stripped_line:
                    e1 = 0.0
                    e1_orig = 0.0

                if (not stripped_line
                        or stripped_line.startswith(';')
                        or 'G90' in stripped_line
                        or 'G91' in stripped_line
                        or 'G92' in stripped_line
                        or 'G21' in stripped_line
                        or 'G4' in stripped_line):

                    if ('G90' in stripped_line or 'G91' in stripped_line) and "G9" in original_line[:3]:
                        progress.advance(task)
                        continue

                    if 'G92' in stripped_line and 'E' in stripped_line:
                        safe_line = re.sub(r'(?<![;\w])E(?=[\d\-\.])', EXTRUSION_AXIS, original_line)
                        f_new.write(safe_line)
                    else:
                        f_new.write(original_line)

                    progress.advance(task)
                    continue

                if 'T0' in stripped_line:
                    f_new.write('T0\n')
                    extruder = 0
                    progress.advance(task)
                    continue
                if 'T1' in stripped_line:
                    f_new.write('T1\n')
                    extruder = 1
                    progress.advance(task)
                    continue

                if stripped_line.startswith('K') or stripped_line.startswith('k'):
                    new_k = stripped_line.split('=')
                    try:
                        extrusion_coefficient = float(new_k[-1].strip())
                        f_new.write(f"; extrusion coefficient changed to = {extrusion_coefficient}\n")
                    except ValueError:
                        pass
                    progress.advance(task)
                    continue

                if stripped_line.startswith(('B', 'b', 'C', 'c')):
                    progress.advance(task)
                    continue

                letters = {
                    'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None,
                    'I': None, 'J': None, 'R': None, 'T': None, 'E': None, 'F': None
                }
                var = False
                for command in stripped_line.split():
                    if command.startswith(';'):
                        break
                    if command.endswith(';'):
                        command = command[:-1]
                        var = True
                    if command and command[0].upper() in letters:
                        try:
                            letters[command[0].upper()] = float(command[1:])
                        except ValueError:
                            pass
                    if var:
                        break

                motion_axes = ['X', 'Y', 'Z', 'A', 'I', 'J', 'R', 'T']
                if not any(letters.get(c) is not None for c in motion_axes):
                    f_new.write(original_line)
                    progress.advance(task)
                    continue

                g = letters.get('G')
                x = letters.get('X')
                y = letters.get('Y')
                z = letters.get('Z')
                a = letters.get('A')
                i = letters.get('I')
                j = letters.get('J')
                r = letters.get('R')
                f = letters.get('F')

                l = 0

                x_val = x if x is not None else (x1 if coordinate_type == 0 else 0)
                y_val = y if y is not None else (y1 if coordinate_type == 0 else 0)
                z_val = z if z is not None else (z1 if coordinate_type == 0 else 0)
                a_val = a if a is not None else (a1 if coordinate_type == 0 else 0)
                i_val = i if i is not None else 0
                j_val = j if j is not None else 0

                if coordinate_type == 0:
                    x_rel = (x_val - x1) if x is not None else 0
                    y_rel = (y_val - y1) if y is not None else 0
                    z_rel = (z_val - z1) if z is not None else 0
                    a_rel = (a_val - a1) if a is not None else 0
                else:
                    x_rel = x if x is not None else 0
                    y_rel = y if y is not None else 0
                    z_rel = z if z is not None else 0
                    a_rel = a if a is not None else 0

                if g == 1:
                    l = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                elif g == 2 or g == 3:
                    full_circle = False
                    radius = r
                    if radius is None:
                        radius = math.sqrt(i_val**2 + j_val**2)

                    if x_rel != 0 or y_rel != 0 or z_rel != 0 or a_rel != 0:
                        d = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                        if radius > 0:
                            val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                            theta = 2 * math.pi - math.acos(val)
                        else:
                            theta = 0
                    else:
                        theta = 2 * math.pi
                        full_circle = True

                    l = radius * theta
                    if g == 3 and not full_circle:
                        l = 2 * math.pi * radius - l

                original_e = letters.get('E')

                if original_e is None:
                    chunk = 0
                else:
                    if coordinate_type == 1:
                        e_change = original_e
                    else:
                        e_change = original_e - e1_orig

                    if e_change == 0:
                        chunk = 0
                    else:
                        if l > 0:
                            if extruder == 0:
                                chunk = (extrusion_coefficient * l * Z_NOZZLE_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                            else:
                                chunk = (extrusion_coefficient * l * A_NOZZLE_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)
                            if e_change < 0:
                                chunk = -chunk
                        else:
                            FILAMENT_DIAMETER = 1.75
                            if extruder == 0:
                                chunk = e_change * (FILAMENT_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                            else:
                                chunk = e_change * (FILAMENT_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)

                if original_e is not None:
                    if coordinate_type == 1:
                        e = chunk
                    else:
                        e = e1 + chunk
                    if extruder == 0:
                        netExtrude += chunk
                    else:
                        netExtrude_A += chunk
                    e1_orig = original_e
                else:
                    e = None

                write_line = ""
                if g is not None:  write_line += 'G' + str(int(g))
                if x is not None:  write_line += ' X' + str(x)
                if y is not None:  write_line += ' Y' + str(y)
                if g in (2, 3):
                    if r is not None: write_line += ' R' + str(r)
                    if i is not None: write_line += ' I' + str(i)
                    if j is not None: write_line += ' J' + str(j)
                if z is not None:  write_line += ' Z' + str(z)
                if a is not None:  write_line += ' A' + str(a)
                if e is not None and g != 0:
                    write_line += f' {EXTRUSION_AXIS}' + str(round(e, 3))
                if f is not None:  write_line += ' F' + str(f)

                if 'NO E' in original_line:
                    f_new.write(original_line)
                    if original_e is not None:
                        if coordinate_type == 0:
                            e -= chunk
                        if extruder == 0:
                            netExtrude -= chunk
                        else:
                            netExtrude_A -= chunk
                else:
                    f_new.write(write_line + "\n")

                if coordinate_type == 0:
                    x1 = x_val if x is not None else x1
                    y1 = y_val if y is not None else y1
                    z1 = z_val if z is not None else z1
                    a1 = a_val if a is not None else a1
                else:
                    if x is not None: x1 += x
                    if y is not None: y1 += y
                    if z is not None: z1 += z
                    if a is not None: a1 += a

                e1 = e if e is not None else e1
                progress.advance(task)

        if DO_AUTO_PRESSURIZE:
            f_new.write("\n; Auto-depressurize syringe\n")
            f_new.write("G91 ; Switch to relative positioning for depressurize\n")
            f_new.write(f"G1 {EXTRUSION_AXIS}{-PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")
            if COORDINATE_MODE == "G90":
                f_new.write("G90 ; Switch back to absolute positioning\n")

        f_new.write("\n; --- End of Print Sequence ---\n")
        f_new.write("G91 ; Switch to relative positioning\n")
        f_new.write("G1 Z-5 F300 ; Lower nozzle 5mm to park near bed (safe for next home)\n")
        f_new.write("G90 ; Switch back to absolute positioning\n")
        f_new.write("G1 X0 Y0 F300 ; Park X/Y at origin\n")
        f_new.write("; NOTE: Z is left at a low position so the next G28 cannot crash\n")
        f_new.write("; -----------------------------\n")

    finally:
        f_new.close()

    netVol_Z = netExtrude   * math.pi * (Z_SYRINGE_DIAMETER / 2)**2 / 1000
    netVol_A = netExtrude_A * math.pi * (A_SYRINGE_DIAMETER / 2)**2 / 1000

    success_text = (
        f"[bold cyan]Extruder B (Z syringe):[/bold cyan]\n"
        f"  Distance: [bold yellow]{round(netExtrude, 3)} mm[/bold yellow]   "
        f"Volume: [bold yellow]{round(netVol_Z, 3)} mL[/bold yellow]\n\n"
        f"[bold cyan]Extruder C (A syringe):[/bold cyan]\n"
        f"  Distance: [bold yellow]{round(netExtrude_A, 3)} mm[/bold yellow]   "
        f"Volume: [bold yellow]{round(netVol_A, 3)} mL[/bold yellow]"
    )
    console.print()
    console.print(Panel(success_text, title="[bold green]Translation Complete[/bold green]",
                        border_style="green", expand=False))

    load_now = Prompt.ask("\nLoad this file for printing now?", choices=["y", "n"], default="y")
    if load_now.lower() == 'y':
        loaded_filepath = output_filepath
        console.print(f"[bold green]Loaded {output_filename}![/bold green]")
        time.sleep(1)


# ============================================================
# --- PRINT CONTROLS ---
# ============================================================

def check_for_pause(progress):
    """
    Non-blocking check for an Enter keypress during printing.
    Returns True if the print should be aborted.
    """
    pause_requested = False

    if sys.platform == 'win32':
        if msvcrt.kbhit():
            msvcrt.getch()
            pause_requested = True
    else:
        if sys.stdin in select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.readline()
            pause_requested = True

    if not pause_requested:
        return False

    try:
        send_gcode("M220 S0", wait_for_ok=False)
    except Exception:
        pass

    progress.stop()
    console.print("\n[bold yellow]PRINT PAUSED[/bold yellow]")

    action = Prompt.ask(
        "[bold cyan]Choose an action:[/bold cyan] [bold green](r)esume[/bold green] or [bold red](s)top[/bold red]",
        choices=["r", "s"], default="r"
    )

    if action == 's':
        console.print("[bold red]Cancelling print and parking...[/bold red]")
        try:
            send_gcode("M410", wait_for_ok=False)
            time.sleep(0.5)
            send_gcode("M220 S100", wait_for_ok=False)
            send_gcode("G91", wait_for_ok=False)
            send_gcode("G1 Z-5 F300", wait_for_ok=False)   # Park LOW, not high
            send_gcode("G90", wait_for_ok=False)
            send_gcode("G1 X0 Y0 F300", wait_for_ok=False)
        except Exception as e:
            console.print(f"[dim]Failed to send park command: {e}[/dim]")
        return True

    console.print("[bold green]Resuming print...[/bold green]")
    try:
        send_gcode("M220 S100", wait_for_ok=False)
    except Exception:
        pass
    progress.start()
    return False


# ============================================================
# --- LOAD FILE MENU ---
# ============================================================

def load_file_menu():
    global loaded_filepath

    out_dir = "translated_gcode"

    if not os.path.exists(out_dir):
        console.print(Panel(
            f"[bold red]No '{out_dir}' directory found.[/bold red]\n\nTranslate a file first (option 2) to create it.",
            border_style="red"))
        time.sleep(2)
        return

    if loaded_filepath:
        console.print(f"Currently loaded: [bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]\n")

    selected_file = pick_file(out_dir, "Translated Files in 'translated_gcode'", show_size=True)
    if not selected_file:
        return

    loaded_filepath = os.path.join(out_dir, selected_file)
    console.print(f"\n[bold green]Loaded:[/bold green] [cyan]{selected_file}[/cyan]")
    time.sleep(1.5)


# ============================================================
# --- PRINT FILE ---
# ============================================================

def print_file():
    global printer_conn, loaded_filepath

    if not printer_conn:
        console.print("[bold red]Printer not connected![/bold red]")
        time.sleep(1)
        return

    if not loaded_filepath:
        console.print("[bold red]No file loaded![/bold red]")
        time.sleep(1)
        return

    console.print()

    warning_text = (
        "ACTION REQUIRED: The print will begin by homing all axes simultaneously (G28 X Y Z).\n"
        "Z will be lowered to a safe position first to prevent rail crashes.\n"
        "Make sure each axis can travel freely and the build area is clear."
    )
    console.print(Panel(f"[bold yellow]{warning_text}[/bold yellow]", border_style="yellow"))
    ready = Prompt.ask("Ready to home and start the print?", choices=["y", "n"], default="y")

    if ready.lower() != 'y':
        console.print("[bold red]Print cancelled.[/bold red]")
        time.sleep(1.5)
        return

    try:
        with open(loaded_filepath, "r") as file:
            lines = file.readlines()
    except Exception as e:
        console.print(f"[bold red]Error reading file: {e}[/bold red]")
        time.sleep(2)
        return

    console.print(Panel(
        f"[bold yellow]Starting print: {os.path.basename(loaded_filepath)}[/bold yellow]\n"
        f"[bold cyan]Press ENTER to PAUSE the print.[/bold cyan]"
    ))

    drain_queue()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:

        task = progress.add_task("[cyan]Printing...", total=len(lines))
        print_aborted = False

        for line in lines:
            if check_for_pause(progress):
                print_aborted = True
                break

            stripped = line.strip()
            if not stripped or stripped.startswith(';'):
                progress.advance(task)
                continue

            command = stripped.split(';')[0].strip()
            if not command:
                progress.advance(task)
                continue

            try:
                if command.upper().startswith("G28") or command.upper().startswith("G29"):
                    send_gcode(command, timeout=180)
                else:
                    send_gcode(command)
            except RuntimeError as e:
                console.print(f"\n[bold red]PRINT FAILED:[/bold red] {e}")
                print_aborted = True
                break
            except KeyboardInterrupt:
                console.print("\n[bold red]Print interrupted.[/bold red]")
                try:
                    send_gcode("M400", wait_for_ok=False)
                    send_gcode("G91", wait_for_ok=False)
                    send_gcode("G1 Z-5 F300", wait_for_ok=False)   # Park LOW
                    send_gcode("G90", wait_for_ok=False)
                except Exception:
                    pass
                print_aborted = True
                break

            progress.advance(task)

        if not print_aborted:
            progress.update(task, description="[cyan]Finishing buffered moves...")
            try:
                send_gcode("M400")
            except Exception:
                pass

    if not print_aborted:
        console.print("\n[bold green]Print completed successfully![/bold green]")

    time.sleep(2)


# ============================================================
# --- TRANSLATION SETTINGS REVIEW ---
# ============================================================

def review_settings_before_translation(filename):
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE

    while True:
        console.clear()
        display_header()
        console.print(f"Preparing to translate: [bold magenta]{filename}[/bold magenta]\n")
        console.print(build_config_table("Translation Settings"))

        console.print("\n[bold yellow]--- Pre-Translation Check ---[/bold yellow]")
        console.print("[1] [bold green]Proceed with Translation[/bold green]")
        console.print("[2] Change Extrusion Coefficient")
        console.print("[3] Toggle Auto-Pressurize")
        console.print("[4] Toggle Coordinate Mode")
        console.print("[5] Cancel\n")

        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=["1", "2", "3", "4", "5"])

        if choice == "1":
            return True
        elif choice == "2":
            new_coeff = Prompt.ask("Enter new Extrusion Coefficient", default=str(EXTRUSION_COEFFICIENT))
            try:
                EXTRUSION_COEFFICIENT = float(new_coeff)
            except ValueError:
                console.print("[bold red]Invalid number.[/bold red]")
                time.sleep(1.5)
        elif choice == "3":
            DO_AUTO_PRESSURIZE = not DO_AUTO_PRESSURIZE
        elif choice == "4":
            COORDINATE_MODE = "G91" if COORDINATE_MODE == "G90" else "G90"
        elif choice == "5":
            return False


# ============================================================
# --- MAIN MENU ---
# ============================================================

def main():
    global printer_listener_running

    # -------------------------------------------------------
    # Restore all axis positions from disk on startup so the
    # safety guard has a reference even after a crash or outage.
    # -------------------------------------------------------
    load_axis_state()

    while True:
        console.clear()
        display_header()

        conn_status = (f"[bold green]Connected ({printer_conn.port})[/bold green]"
                       if printer_conn else "[bold red]Not Connected[/bold red]")
        console.print(f"Printer Status: {conn_status}")

        file_status = (f"[bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]"
                       if loaded_filepath else "[dim]None[/dim]")
        console.print(f"Loaded File:    {file_status}")

        # Show all axis positions so the operator can verify state at a glance.
        # Unknown axes are shown in dim red with a note that homing will pre-drop.
        axis_parts = []
        for a in AXES:
            v = get_axis(a)
            if v is not None:
                axis_parts.append(f"[bold yellow]{a.upper()}={v:.2f}[/bold yellow]")
            else:
                axis_parts.append(f"[dim red]{a.upper()}=?[/dim red]")
        console.print(f"Axis Positions: {' '.join(axis_parts)}"
                      + ("" if get_z() is not None else
                         "  [dim](Z unknown — will pre-drop before homing)[/dim]"))
        console.print()

        console.print("[bold yellow]--- Main Menu ---[/bold yellow]")

        valid_choices = ["1", "2", "3", "7", "8"]

        if printer_conn:
            console.print("[0] [bold red]Reset / Reboot Printer Board[/bold red]")
            valid_choices.append("0")

        console.print("[1] Connect to Printer")
        console.print("[2] Translate G-Code")
        console.print("[3] Load Translated File")

        if printer_conn and loaded_filepath:
            console.print("[4] [bold green]Print Loaded File[/bold green]")
            valid_choices.append("4")
        else:
            console.print("[4] [dim]Print Loaded File (Requires Connection & File)[/dim]")

        if printer_conn:
            console.print("[5] [bold cyan]Manual G-Code Terminal[/bold cyan]")
            console.print("[6] [bold cyan]Jog Control[/bold cyan]")
            valid_choices.extend(["5", "6"])
        else:
            console.print("[5] [dim]Manual G-Code Terminal (Requires Connection)[/dim]")
            console.print("[6] [dim]Jog Control (Requires Connection)[/dim]")

        console.print("[7] Options / Settings")
        console.print("[8] Exit\n")

        valid_choices = sorted(set(valid_choices), key=int)
        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=valid_choices)

        if choice == "0":
            reset_printer_board()
        elif choice == "1":
            connect_to_printer()
        elif choice == "2":
            translate_gcode()
        elif choice == "3":
            load_file_menu()
        elif choice == "4":
            print_file()
        elif choice == "5":
            manual_control_menu()
        elif choice == "6":
            while interactive_jog_menu() == "reload":
                pass
        elif choice == "7":
            settings_menu()
        elif choice == "8":
            printer_listener_running = False
            if printer_listener_thread and printer_listener_thread.is_alive():
                printer_listener_thread.join(timeout=2)
            if printer_conn:
                try:
                    printer_conn.close()
                except Exception:
                    pass
            console.print("[bold magenta]Goodbye![/bold magenta]")
            break


if __name__ == "__main__":
    # KeyboardInterrupt is now handled by _signal_handler (SIGINT), but keep
    # a fallback here in case Python delivers it as an exception instead of a
    # signal (e.g. when running inside some IDEs or wrapped launchers).
    try:
        main()
    except KeyboardInterrupt:
        _emergency_save()
        printer_listener_running = False
        if printer_conn:
            try:
                printer_conn.close()
            except Exception:
                pass
        console.print("\n[bold magenta]Goodbye![/bold magenta]")
