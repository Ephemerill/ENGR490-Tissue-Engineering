import math
import os
import subprocess
import time
import sys
import re
import threading
import queue
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
# --- STATE VARIABLES ---
# ==========================================
printer_conn = None
loaded_filepath = None

printer_lock = threading.Lock()
printer_response_queue = queue.Queue()

printer_listener_running = False
printer_listener_thread = None

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
⠀⠀⠀⣀⣀⣤⢤⢤⡴⢶⣾⡿⠿⣛⠩⠀⠉⠉⠙⠛⠻⠿⢏⡀⠀⠀⠀⠙⠻⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢈⡷⠀⠀⠀⠀⠀⠀⠀⠀⣠⣷⣿⡀⠀⠀⠀⠀⠀⠀⠀         [cyan]v1.0.17[/cyan]
⢠⠖⠋⠉⠀⢀⠀⠂⣌⢇⠀⣰⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⠀⡀⠀⠀⢀⣽⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⣐⠰⠂⠀⠀⠀⠀⡀⣠⣴⣾⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠛⠓⠒⠲⢤⣀⣐⣤⡞⣸⢊⠥⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠀⢀⣤⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⢄⣀⠀⠠⠤⠴⠂⠈⠁⢰⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⠃⠀⠀⠸⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢖⣦⣀⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠾⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⡿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""
    splash = splash.replace('\u2800', ' ')
    console.print(splash, style="bold white")

# ============================================================
# --- SERIAL LISTENER THREAD ---
# ============================================================

def serial_listener():
    """Background thread: continuously reads lines from the serial port into a queue."""
    global printer_listener_running
    global printer_conn

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
    """
    Sends a G-code command with retry logic and ok/error/busy/resend handling.
    Used for all non-interactive serial communication throughout the program.
    """
    global printer_conn

    if not printer_conn or not printer_conn.is_open:
        raise RuntimeError("Printer not connected")

    command = command.strip()
    if not command:
        return True

    for attempt in range(retries):
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
            raise e

        time.sleep(0.25)

    raise RuntimeError(f"Failed command after {retries} retries: {command}")


# ============================================================
# --- SETTINGS MENU ---
# ============================================================

def settings_menu():
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE
    global HIGH_PRECISION_JOG

    while True:
        console.clear()
        display_header()

        config_table = Table(
            show_header=True,
            header_style="bold yellow",
            expand=True,
            title="[bold cyan]Current Configuration[/bold cyan]"
        )
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
        config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
        config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
        config_table.add_row(
            "Extrusion Coeff.", str(EXTRUSION_COEFFICIENT),
            "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]"
        )
        config_table.add_row(
            "Jog Precision", "[green]HIGH[/green]" if HIGH_PRECISION_JOG else "[yellow]LOW[/yellow]",
            "", ""
        )

        console.print(config_table)
        console.print("\n[bold yellow]--- Options Menu ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Toggle Jog Precision Mode")
        console.print("[5] Return to Main Menu\n")

        choice = Prompt.ask(
            "[bold yellow]Choose an option[/bold yellow]",
            choices=["1", "2", "3", "4", "5"]
        )

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
    global printer_conn
    global printer_listener_running
    global printer_listener_thread

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

    choice = IntPrompt.ask(
        "\n[bold yellow]Select the port to connect to[/bold yellow]",
        choices=[str(i) for i in range(len(ports) + 1)]
    )
    if choice == 0:
        return

    selected_port = ports[choice - 1].device

    try:
        with console.status(f"[bold green]Connecting to {selected_port} at {BAUD_RATE} baud...", spinner="dots"):

            printer_conn = serial.Serial(
                selected_port,
                BAUD_RATE,
                timeout=1,
                write_timeout=5,
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

        # Wake the printer and confirm firmware identity (outside status context so
        # the M115 response lines can print cleanly without fighting the spinner)
        send_gcode("M115", timeout=10)

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
        send_gcode("M112", wait_for_ok=False)
        time.sleep(0.5)

        printer_conn.dtr = False
        time.sleep(1.0)
        printer_conn.dtr = True
        time.sleep(4)

        printer_conn.reset_input_buffer()
        printer_conn.reset_output_buffer()

        # Drain stale boot messages from the queue so they don't cause
        # false 'ok' hits on the next send_gcode call
        while not printer_response_queue.empty():
            try:
                printer_response_queue.get_nowait()
            except queue.Empty:
                break

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

    mode_str = (
        "[bold green]HIGH (Instant Stop, Choppy)[/bold green]"
        if HIGH_PRECISION_JOG
        else "[bold yellow]LOW (Smooth Glide, Slight Coast)[/bold yellow]"
    )

    console.print(Panel(
        f"[bold cyan]Jog Control[/bold cyan]\n"
        f"Precision Mode: {mode_str}\n\n"
        f"Press or hold keys to move the printer. Commands are sent at F{JOG_SPEED_MM_MIN} in {JOG_DISTANCE}mm chunks.\n\n"
        " [bold yellow]W[/bold yellow] : +Y    [bold yellow]S[/bold yellow] : -Y\n"
        " [bold yellow]A[/bold yellow] : -X    [bold yellow]D[/bold yellow] : +X\n"
        " [bold yellow]R[/bold yellow] : +Z    [bold yellow]F[/bold yellow] : -Z\n"
        " [bold yellow]T[/bold yellow] : -B    [bold yellow]G[/bold yellow] : +B\n\n"
        "Press [bold magenta]'p'[/bold magenta] to swap between High and Low Precision.\n"
        "Press [bold red]'q'[/bold red] to return to the main menu.",
        border_style="cyan"
    ))

    # Switch to relative mode for jogging; listener thread handles responses
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
                            # M400 after each move flushes the planner for true instant stop
                            send_gcode(cmd, wait_for_ok=False)
                            send_gcode("M400", wait_for_ok=False)
                            in_flight_commands += 2
                        else:
                            send_gcode(cmd, wait_for_ok=False)
                            in_flight_commands += 1

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

    if toggle_requested:
        return "reload"
    return "quit"

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
        "[bold yellow]TIP:[/bold yellow] Send [bold green]G28[/bold green] to sensorless-home all axes using your firmware configuration.\n"
        "Send [bold green]G91[/bold green] to switch to Relative Mode for manual moves.\n\n"
        "Type [bold yellow]'q'[/bold yellow] or [bold yellow]'quit'[/bold yellow] to return to the main menu.",
        border_style="cyan"
    ))

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

        try:
            # G28 (home) and G29 (bed levelling) can take minutes; use a longer timeout
            if cmd_upper.startswith("G28") or cmd_upper.startswith("G29"):
                send_gcode(cmd_upper, timeout=180)
            else:
                send_gcode(cmd_upper)
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")

# ============================================================
# --- GCODE TRANSLATION ---
# ============================================================

def translate_gcode():
    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)
        console.print(Panel(
            f"[bold yellow]Created '{raw_dir}' directory.[/bold yellow]\n\nPlease place your raw files there.",
            title="[bold red]Action Required"
        ))
        time.sleep(2)
        return

    os.makedirs(out_dir, exist_ok=True)

    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(raw_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(f"[bold red]No files found in '{raw_dir}'.[/bold red]"))
        time.sleep(2)
        return

    files.sort(key=lambda x: os.path.getmtime(os.path.join(raw_dir, x)), reverse=True)

    file_table = Table(
        show_header=True, header_style="bold green",
        title="[bold cyan]Available Files in 'raw_gcode'[/bold cyan]"
    )
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")

    for i, f in enumerate(files):
        mtime = os.path.getmtime(os.path.join(raw_dir, f))
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str)

    console.print(file_table)
    console.print("[0] Cancel")

    choice = IntPrompt.ask(
        "\n[bold yellow]Select a file to translate[/bold yellow]",
        choices=[str(i) for i in range(len(files) + 1)]
    )
    if choice == 0:
        return

    selected_file = files[choice - 1]
    input_filepath = os.path.join(raw_dir, selected_file)

    proceed = review_settings_before_translation(selected_file)
    if not proceed:
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
        f_new.write("G28 X Y Z ; Sensorless home all axes (StallGuard, configured in firmware)\n")
        f_new.write("G91 ; Relative positioning to travel off the homed corner\n")
        f_new.write("G1 X50 Y67 Z-90 F300 ; Move from home to the print start position\n")
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
                        # Replace only the axis letter E (e.g. 'E0'), not E inside comments or words
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
                    if command[0].upper() in letters:
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
        f_new.write("G1 Z30 F300 ; Lift nozzle 30mm to safely clear the print\n")
        f_new.write("G90 ; Switch back to absolute positioning\n")
        f_new.write("G1 X0 Y0 F300 ; Park at the print origin\n")
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
    console.print(Panel(
        success_text,
        title="[bold green]Translation Complete[/bold green]",
        border_style="green",
        expand=False
    ))

    load_now = Prompt.ask("\nLoad this file for printing now?", choices=["y", "n"], default="y")
    if load_now.lower() == 'y':
        global loaded_filepath
        loaded_filepath = output_filepath
        console.print(f"[bold green]Loaded {output_filename}![/bold green]")
        time.sleep(1)


# ============================================================
# --- PRINT CONTROLS ---
# ============================================================

def check_for_pause(progress):
    """
    Non-blocking check for an Enter keypress during printing.
    Lets the user pause, then choose to resume or cancel.
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

    if pause_requested:
        # Freeze motion immediately
        try:
            send_gcode("M220 S0", wait_for_ok=False)
        except Exception:
            pass

        progress.stop()
        console.print("\n[bold yellow]PRINT PAUSED[/bold yellow]")

        action = Prompt.ask(
            "[bold cyan]Choose an action:[/bold cyan] [bold green](r)esume[/bold green] or [bold red](s)top[/bold red]",
            choices=["r", "s"],
            default="r"
        )

        if action == 's':
            console.print("[bold red]Cancelling print and parking...[/bold red]")
            try:
                send_gcode("M410", wait_for_ok=False)
                time.sleep(0.5)
                send_gcode("M220 S100", wait_for_ok=False)
                send_gcode("G91", wait_for_ok=False)
                send_gcode("G1 Z30 F300", wait_for_ok=False)
                send_gcode("G90", wait_for_ok=False)
                send_gcode("G1 X0 Y0 F300", wait_for_ok=False)
            except Exception as e:
                console.print(f"[dim]Failed to send park command: {e}[/dim]")
            return True
        else:
            console.print("[bold green]Resuming print...[/bold green]")
            try:
                send_gcode("M220 S100", wait_for_ok=False)
            except Exception:
                pass
            progress.start()
            return False

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
            border_style="red"
        ))
        time.sleep(2)
        return

    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(out_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(
            f"[bold red]No translated files found in '{out_dir}'.[/bold red]\n\nTranslate a file first (option 2).",
            border_style="red"
        ))
        time.sleep(2)
        return

    files.sort(key=lambda x: os.path.getmtime(os.path.join(out_dir, x)), reverse=True)

    file_table = Table(
        show_header=True, header_style="bold green",
        title=f"[bold cyan]Translated Files in '{out_dir}'[/bold cyan]"
    )
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")
    file_table.add_column("Size", justify="right", style="yellow")

    for i, f in enumerate(files):
        full_path = os.path.join(out_dir, f)
        mtime = os.path.getmtime(full_path)
        size_kb = os.path.getsize(full_path) / 1024
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str, f"{size_kb:.1f} KB")

    console.print(file_table)

    if loaded_filepath:
        console.print(f"\nCurrently loaded: [bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]")
    console.print("[0] Cancel\n")

    choice = IntPrompt.ask(
        "[bold yellow]Select a file to load[/bold yellow]",
        choices=[str(i) for i in range(len(files) + 1)]
    )
    if choice == 0:
        return

    selected_file = files[choice - 1]
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

    # The translated file now begins with G28 sensorless homing, so the bed no
    # longer needs to be positioned by hand — just make sure it can home safely.
    warning_text = (
        "ACTION REQUIRED: The print will begin by sensorless-homing all axes (G28).\n"
        "Make sure each axis can travel freely to its endstop and the build area is clear."
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

    # Drain any stale responses before we start
    while not printer_response_queue.empty():
        try:
            printer_response_queue.get_nowait()
        except queue.Empty:
            break

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:

        task = progress.add_task("[cyan]Printing...", total=len(lines))
        print_aborted = False

        for i, line in enumerate(lines):
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
                # G28 (home) at the top of the file can take a while; give it room
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
                    send_gcode("G1 Z20 F300", wait_for_ok=False)
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
# --- GITHUB UPDATE ---
# ============================================================

def update_orca():
    global printer_conn
    console.print(Panel("[bold cyan]Fetching latest updates from GitHub...[/bold cyan]", border_style="cyan"))
    try:
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
        console.print("[bold green]Successfully pulled latest changes![/bold green]")
        if result.stdout.strip():
            console.print(f"[dim]{result.stdout.strip()}[/dim]")

        if "Already up to date." in result.stdout:
            time.sleep(2)
            return

        console.print("\n[bold yellow]Restarting ORCA to apply updates...[/bold yellow]")
        time.sleep(2)

        if printer_conn:
            try:
                printer_conn.close()
            except Exception:
                pass
            printer_conn = None

        os.execl(sys.executable, sys.executable, *sys.argv)

    except subprocess.CalledProcessError as e:
        console.print("[bold red]Failed to update from GitHub.[/bold red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        time.sleep(3)
    except Exception as e:
        console.print(f"[bold red]Unexpected error: {e}[/bold red]")
        time.sleep(3)


# ============================================================
# --- TRANSLATION SETTINGS REVIEW ---
# ============================================================

def review_settings_before_translation(filename):
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE

    while True:
        console.clear()
        display_header()
        console.print(f"Preparing to translate: [bold magenta]{filename}[/bold magenta]\n")

        config_table = Table(
            show_header=True,
            header_style="bold yellow",
            expand=True,
            title="[bold cyan]Translation Settings[/bold cyan]"
        )
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
        config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
        config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
        config_table.add_row(
            "Extrusion Coeff.", str(EXTRUSION_COEFFICIENT),
            "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]"
        )

        console.print(config_table)
        console.print("\n[bold yellow]--- Pre-Translation Check ---[/bold yellow]")
        console.print("[1] [bold green]Proceed with Translation[/bold green]")
        console.print("[2] Change Extrusion Coefficient")
        console.print("[3] Toggle Auto-Pressurize")
        console.print("[4] Toggle Coordinate Mode")
        console.print("[5] Cancel\n")

        choice = Prompt.ask(
            "[bold yellow]Choose an option[/bold yellow]",
            choices=["1", "2", "3", "4", "5"]
        )

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

    while True:
        console.clear()
        display_header()

        conn_status = (
            f"[bold green]Connected ({printer_conn.port})[/bold green]"
            if printer_conn
            else "[bold red]Not Connected[/bold red]"
        )
        console.print(f"Printer Status: {conn_status}")

        file_status = (
            f"[bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]"
            if loaded_filepath
            else "[dim]None[/dim]"
        )
        console.print(f"Loaded File:    {file_status}\n")

        console.print("[bold yellow]--- Main Menu ---[/bold yellow]")

        valid_choices = ["1", "2", "3", "8", "9", "10"]

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

        console.print("[8] Options / Settings")
        console.print("[9] Update ORCA from GitHub")
        console.print("[10] Exit\n")

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
            while True:
                res = interactive_jog_menu()
                if res != "reload":
                    break
        elif choice == "8":
            settings_menu()
        elif choice == "9":
            update_orca()
        elif choice == "10":
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
    try:
        main()
    except KeyboardInterrupt:
        printer_listener_running = False
        if printer_conn:
            try:
                printer_conn.close()
            except Exception:
                pass
        console.print("\n[bold magenta]Goodbye![/bold magenta]")
