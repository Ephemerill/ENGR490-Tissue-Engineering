import math
import os
import platform
import subprocess
import time
import sys
import select
import tty
import termios
from datetime import datetime

# Import Rich
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import IntPrompt, Prompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
except ImportError:
    print("Please install the 'rich' library: pip install rich")
    exit()

# Import pyserial for printer communication
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Please install the 'pyserial' library: pip install pyserial")
    exit()

# Import pynput for raw keyboard hardware monitoring
try:
    from pynput import keyboard
except ImportError:
    print("Please install the 'pynput' library: pip install pynput")
    print("(Note for Mac users: You may need to grant your terminal Accessibility permissions in System Settings)")
    exit()

# Initialize the Rich Console
console = Console()

# ==========================================
# --- CONFIGURATION PARAMETERS ---
# ==========================================
COORDINATE_MODE = "G90"         # 'G90' for Absolute, 'G91' for Relative
EXTRUSION_AXIS = "B"            # The target axis for extrusion ('B' or 'C')
Z_SYRINGE_DIAMETER = 4.9        # Inner diameter in mm (4.9 for 1mL BD syringe)
A_SYRINGE_DIAMETER = 4.9
Z_NOZZLE_DIAMETER = 0.5         # Nozzle diameter in mm
A_NOZZLE_DIAMETER = 0.2
EXTRUSION_COEFFICIENT = 5    # Scaling factor for extrusion

# Auto-Pressurization Settings
DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 5
PRESSURIZE_SPEED = 300          # Capped at 300

# Jog Settings
JOG_DISTANCE = 0.2              # Distance in mm per keystroke tick
JOG_SPEED_MM_MIN = 300          # The F-value for jogging speed
HIGH_PRECISION_JOG = True       # Start in high precision mode

# Bed Origin Settings
START_FROM_CENTER = True       # If True, expects bed to start in center, skipping init travel

# Serial Connection Settings
BAUD_RATE = 115200
# ==========================================

# --- STATE VARIABLES ---
printer_conn = None
loaded_filepath = None

def display_header():
    # Show the awesome ASCII splash art on the main menus
    splash = r"""
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
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣵⣿⣿⣅⠀⠀⠀⠀⢈⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠖⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠂⠀⠀⠀⠀⠀  \____/|_|  \_\\_____/_/    \_\
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣶⣦⣌⠁⠀⠉⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡏⡞⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠜⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣀⣀⣤⢤⢤⡴⢶⣾⡿⠿⣛⠩⠀⠉⠉⠙⠛⠻⠿⢏⡀⠀⠀⠀⠙⠻⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢈⡷⠀⠀⠀⠀⠀⠀⠀⠀⣠⣷⣿⡀⠀⠀⠀⠀⠀⠀⠀         [cyan]v1.0.12[/cyan]
⢠⠖⠋⠉⠀⢀⠀⠂⣌⢇⠀⣰⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⠀⡀⠀⠀⢀⣽⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⣐⠰⠂⠀⠀⠀⠀⡀⣠⣴⣾⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠛⠓⠒⠲⢤⣀⣐⣤⡞⣸⢊⠥⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠀⢀⣤⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⢄⣀⠀⠠⠤⠴⠂⠈⠁⢰⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⠃⠀⠀⠸⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢖⣦⣀⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠾⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⡿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""
    splash = splash.replace('\u2800', ' ')
    console.print(splash, style="bold white")

def settings_menu():
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE, HIGH_PRECISION_JOG, START_FROM_CENTER
    
    while True:
        console.clear()
        display_header()
        
        config_table = Table(show_header=True, header_style="bold yellow", expand=True, title="[bold cyan]Current Configuration[/bold cyan]")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
        config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
        config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
        config_table.add_row("Extrusion Coeff.", str(EXTRUSION_COEFFICIENT), "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]")
        config_table.add_row("Jog Precision", "[green]HIGH[/green]" if HIGH_PRECISION_JOG else "[yellow]LOW[/yellow]", "Start from Center", "[green]ON[/green]" if START_FROM_CENTER else "[red]OFF[/red]")
        
        console.print(config_table)
        console.print("\n[bold yellow]--- Options Menu ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Toggle Jog Precision Mode")
        console.print("[5] Toggle Start from Center")
        console.print("[6] Return to Main Menu\n")
        
        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=["1", "2", "3", "4", "5", "6"])
        
        if choice == "1":
            new_coeff = Prompt.ask("Enter new Extrusion Coefficient", default=str(EXTRUSION_COEFFICIENT))
            try:
                EXTRUSION_COEFFICIENT = float(new_coeff)
            except ValueError:
                console.print("[bold red]Invalid number. Please enter a valid float.[/bold red]")
                time.sleep(1.5)
        elif choice == "2":
            DO_AUTO_PRESSURIZE = not DO_AUTO_PRESSURIZE
        elif choice == "3":
            COORDINATE_MODE = "G91" if COORDINATE_MODE == "G90" else "G90"
        elif choice == "4":
            HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
        elif choice == "5":
            START_FROM_CENTER = not START_FROM_CENTER
        elif choice == "6":
            break

def connect_to_printer():
    global printer_conn
    
    ports = serial.tools.list_ports.comports()
    if not ports:
        console.print("[bold red]No serial ports found. Make sure the printer is plugged in.[/bold red]")
        time.sleep(2)
        return

    console.print("[bold cyan]Available Ports:[/bold cyan]")
    for i, port in enumerate(ports):
        console.print(f"[{i + 1}] {port.device} - {port.description}")
    
    console.print(f"[0] Cancel")
    
    choice = IntPrompt.ask("\n[bold yellow]Select the port to connect to[/bold yellow]", choices=[str(i) for i in range(len(ports) + 1)])
    
    if choice == 0:
        return
        
    selected_port = ports[choice - 1].device
    
    try:
        with console.status(f"[bold green]Connecting to {selected_port} at {BAUD_RATE} baud...", spinner="dots"):
            printer_conn = serial.Serial(selected_port, BAUD_RATE, timeout=2)
            printer_conn.write(b"\r\n\r\n")
            time.sleep(2)
            printer_conn.flushInput()
            console.print(f"[bold green]Successfully connected to {selected_port}![/bold green]")
            time.sleep(1)
    except Exception as e:
        console.print(f"[bold red]Failed to connect: {e}[/bold red]")
        printer_conn = None
        time.sleep(2)

def interactive_jog_menu():
    global printer_conn, HIGH_PRECISION_JOG
    
    if not printer_conn:
        console.print("[bold red]Printer not connected! Please connect first.[/bold red]")
        time.sleep(1.5)
        return
        
    console.clear()
    display_header()
    
    mode_str = "[bold green]HIGH (Instant Stop, Choppy)[/bold green]" if HIGH_PRECISION_JOG else "[bold yellow]LOW (Smooth Glide, Slight Coast)[/bold yellow]"
    
    console.print(Panel(
        f"[bold cyan]Adjust Printer[/bold cyan]\n"
        f"Precision Mode: {mode_str}\n\n" 
        f"Hold keys to move the printer smoothly. Commands are sent at F{JOG_SPEED_MM_MIN} in {JOG_DISTANCE}mm chunks.\n"
        "You can press multiple keys at once for diagonal movement!\n\n"
        " [bold yellow]W[/bold yellow] : +Y    [bold yellow]S[/bold yellow] : -Y\n"
        " [bold yellow]A[/bold yellow] : -X    [bold yellow]D[/bold yellow] : +X\n"
        " [bold yellow]R[/bold yellow] : +Z    [bold yellow]F[/bold yellow] : -Z\n"
        " [bold yellow]T[/bold yellow] : -B    [bold yellow]G[/bold yellow] : +B\n\n"
        "Press [bold magenta]'p'[/bold magenta] to swap between High and Low Precision instantly.\n"
        "Press [bold red]'q'[/bold red] to return to the main menu.", 
        border_style="cyan"
    ))
    
    # Set to relative mode for jogging
    printer_conn.write(b"G91\n")
    
    # --- TERMINAL BLINDFOLDING ---
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    new_settings = termios.tcgetattr(fd)
    new_settings[3] = new_settings[3] & ~termios.ECHO & ~termios.ICANON
    
    active_keys = set()
    exit_flag = False
    toggle_requested = False

    def on_press(key):
        nonlocal exit_flag, toggle_requested
        global HIGH_PRECISION_JOG
        try:
            char = key.char.lower()
            if char == 'q':
                exit_flag = True
                return False 
            elif char == 'p':
                HIGH_PRECISION_JOG = not HIGH_PRECISION_JOG
                toggle_requested = True
                exit_flag = True
                return False
            active_keys.add(char)
        except AttributeError:
            pass

    def on_release(key):
        try:
            char = key.char.lower()
            active_keys.discard(char)
        except AttributeError:
            pass

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    
    try:
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        
        in_flight_commands = 0
        
        while not exit_flag:
            while printer_conn.in_waiting > 0:
                try:
                    resp = printer_conn.readline().decode('utf-8').strip()
                    if resp == 'ok' or resp.startswith('ok'):
                        in_flight_commands = max(0, in_flight_commands - 1)
                except serial.SerialException:
                    pass

            # High Precision allows 0 buffered commands (must finish M400 physically)
            # Low Precision allows 1 buffered command (sliding window for blending)
            limit = 0 if HIGH_PRECISION_JOG else 1
            
            if in_flight_commands <= limit and active_keys:
                dx, dy, dz, de = 0.0, 0.0, 0.0, 0.0
                
                # W and S swapped based on your request
                if 'w' in active_keys: dy += JOG_DISTANCE
                if 's' in active_keys: dy -= JOG_DISTANCE
                if 'a' in active_keys: dx -= JOG_DISTANCE
                if 'd' in active_keys: dx += JOG_DISTANCE
                if 'r' in active_keys: dz += JOG_DISTANCE
                if 'f' in active_keys: dz -= JOG_DISTANCE
                if 't' in active_keys: de -= JOG_DISTANCE
                if 'g' in active_keys: de += JOG_DISTANCE
                
                if dx != 0 or dy != 0 or dz != 0 or de != 0:
                    cmd = "G1"
                    if dx != 0: cmd += f" X{dx:.2f}"
                    if dy != 0: cmd += f" Y{dy:.2f}"
                    if dz != 0: cmd += f" Z{dz:.2f}"
                    if de != 0: cmd += f" {EXTRUSION_AXIS}{de:.2f}"
                    cmd += f" F{JOG_SPEED_MM_MIN}\n"
                    
                    if HIGH_PRECISION_JOG:
                        full_cmd = cmd + "M400\n"
                        printer_conn.write(full_cmd.encode('utf-8'))
                        in_flight_commands += 2
                    else:
                        printer_conn.write(cmd.encode('utf-8'))
                        in_flight_commands += 1
                else:
                    time.sleep(0.005) 
            else:
                time.sleep(0.005) 
    finally:
        listener.stop()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        termios.tcflush(fd, termios.TCIFLUSH)
        
    printer_conn.write(b"G90\n")
    
    if toggle_requested:
        return "reload"
    return "quit"

def manual_control_menu():
    global printer_conn
    
    if not printer_conn:
        console.print("[bold red]Printer not connected! Please connect first.[/bold red]")
        time.sleep(1.5)
        return
        
    console.clear()
    display_header()
    console.print(Panel(
        "[bold cyan]Manual G-Code Terminal[/bold cyan]\n"
        "Type your G-Code commands and press Enter.\n"
        "Movement commands (G0/G1) default to F300 if no speed is specified.\n"
        "Type [bold yellow]'q'[/bold yellow] or [bold yellow]'quit'[/bold yellow] to return to the main menu.", 
        border_style="cyan"
    ))
    
    while True:
        cmd = Prompt.ask("[bold green]>[/bold green]")
        
        if cmd.lower() in ['q', 'quit', 'exit']:
            break
            
        if not cmd.strip():
            continue
            
        cmd_upper = cmd.upper().strip()
        
        # Auto-append F300 for G0/G1 if no F parameter is specified
        parts = cmd_upper.split()
        if parts and (parts[0] == "G0" or parts[0] == "G1"):
            has_f = any(part.startswith("F") for part in parts)
            if not has_f:
                cmd_upper += " F300"
                
        try:
            printer_conn.write((cmd_upper + '\n').encode('utf-8'))
            
            response_lines = []
            while True:
                response = printer_conn.readline().decode('utf-8').strip()
                if response:
                    response_lines.append(response)
                    if response == 'ok' or response.startswith('ok'):
                        break
                else:
                    break
                    
            for r in response_lines:
                console.print(f"[dim]{r}[/dim]")
                
        except serial.SerialException as e:
            console.print(f"[bold red]Serial connection error: {e}[/bold red]")
            break

def translate_gcode():
    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)
        console.print(Panel(f"[bold yellow]Created '{raw_dir}' directory.[/bold yellow]\n\nPlease place your raw files there.", title="[bold red]Action Required"))
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

    file_table = Table(show_header=True, header_style="bold green", title="[bold cyan]Available Files in 'raw_gcode'")
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")

    for i, f in enumerate(files):
        mtime = os.path.getmtime(os.path.join(raw_dir, f))
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str)

    console.print(file_table)
    console.print(f"[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select a file to translate[/bold yellow]", choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0: return

    selected_file = files[choice - 1]
    input_filepath = os.path.join(raw_dir, selected_file)

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

    coordinate_type = 0 if 'G90' in COORDINATE_MODE else 1
    extrusion_coefficient = EXTRUSION_COEFFICIENT
    extruder = 0
    netExtrude = 0

    console.print(f"\n[bold green]Translating[/bold green] [cyan]'{selected_file}'[/cyan] -> [cyan]'{output_filename}'[/cyan]...\n")

    f_new = open(output_filepath, "w+t")
    f_new.write(COORDINATE_MODE + "\n")

    f_new.write("; --- Initialization Sequence ---\n")
    f_new.write("G90 ; Force absolute positioning for setup\n")
    
    if START_FROM_CENTER:
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Zero all axes at the current center position\n")
    else:
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Zero at confirmed bottom-left corner and zero extrusion axis\n")
        f_new.write("G1 Z30 F300 ; Z-hop up 30mm to clear dish walls\n")
        f_new.write("G1 X50 Y50 F300 ; Move to the center\n")
        f_new.write("G1 Z0 F300 ; Drop back down to original height before printing\n")
        f_new.write(f"G92 X0 Y0 Z0 {EXTRUSION_AXIS}0 ; Re-zero all axes at the center\n")
    
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

    x1, y1, e1, a1, z1 = 0, 0, 0, 0, 0
    e1_orig = 0 

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

            if "syringe_diameter" in stripped_line or "nozzle_diameter" in stripped_line or "extrusion_coefficient" in stripped_line:
                progress.advance(task)
                continue

            if 'G92 E0' in stripped_line or f'G92 {EXTRUSION_AXIS}0' in stripped_line:
                x1, y1, e1, a1, z1 = 0, 0, 0, 0, 0
                e1_orig = 0

            if not stripped_line or stripped_line.startswith(';') or 'G90' in stripped_line or 'G91' in stripped_line or 'G92' in stripped_line or 'G21' in stripped_line or 'G4' in stripped_line:
                if ('G90' in stripped_line or 'G91' in stripped_line) and "G9" in original_line[:3]:
                    progress.advance(task)
                    continue
                
                # Catch G92 E0 and translate E to our EXTRUSION_AXIS
                if 'G92' in stripped_line and 'E' in stripped_line:
                    f_new.write(original_line.replace('E', EXTRUSION_AXIS))
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

            if stripped_line.startswith('B') or stripped_line.startswith('b') or stripped_line.startswith('C') or stripped_line.startswith('c'):
                progress.advance(task)
                continue

            letters = {'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None, 'I': None, 'J': None, 'R': None, 'T': None, 'E': None, 'F': None}
            var = False
            for command in stripped_line.split():
                if command.startswith(';'): break
                if command.endswith(';'):
                    command = command[:-1]
                    var = True
                if command[0] in letters:
                    try:
                        letters[command[0]] = float(command[1:])
                    except ValueError:
                        pass
                if var: break

            if not any((letters[c] for c in 'XYZAIJRT' if c in letters and letters[c] is not None)):
                f_new.write(original_line)
                progress.advance(task)
                continue

            g = letters['G']
            x = letters['X']
            y = letters['Y']
            z = letters['Z']
            a = letters['A']
            i = letters['I']
            j = letters['J']
            r = letters['R']
            f = letters['F']

            l = 0
            
            x_val = x if x is not None else 0
            y_val = y if y is not None else 0
            z_val = z if z is not None else 0
            a_val = a if a is not None else 0
            i_val = i if i is not None else 0
            j_val = j if j is not None else 0

            x_rel = x_val - x1 if x is not None else 0
            y_rel = y_val - y1 if y is not None else 0
            z_rel = z_val - z1 if z is not None else 0
            a_rel = a_val - a1 if a is not None else 0

            if g == 1:
                if coordinate_type == 1: 
                    l = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                elif coordinate_type == 0: 
                    l = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
            elif g == 2 or g == 3:
                full_circle = False
                radius = r
                if radius is None: radius = math.sqrt(i_val**2 + j_val**2)
                
                if coordinate_type == 1: 
                    if x_val != 0 or y_val != 0 or z_val != 0 or a_val != 0:
                        d = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                        val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                        theta = 2*math.pi - math.acos(val)
                    else:
                        theta = 2 * math.pi
                        full_circle = True
                elif coordinate_type == 0: 
                    if x is not None or y is not None or z is not None or a is not None:
                        d = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                        val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                        theta = 2*math.pi - math.acos(val)
                    else:
                        theta = 2 * math.pi
                        full_circle = True
                l = radius * theta
                if g == 3 and not full_circle: l = 2 * math.pi * radius - l 
            
            original_e = letters['E']
            
            if original_e is None:
                chunk = 0
            else:
                if coordinate_type == 1: e_change = original_e
                else: e_change = original_e - e1_orig
                
                if e_change == 0:
                    chunk = 0
                else:
                    if l > 0:
                        if extruder == 0: chunk = (extrusion_coefficient * l * Z_NOZZLE_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                        else: chunk = (extrusion_coefficient * l * A_NOZZLE_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)
                        if e_change < 0: chunk = -chunk
                    else:
                        # Scale pure extrusion moves (like primes and retractions) 
                        FILAMENT_DIAMETER = 1.75
                        if extruder == 0:
                            chunk = e_change * (FILAMENT_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
                        else:
                            chunk = e_change * (FILAMENT_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)
            
            if original_e is not None:
                if coordinate_type == 1: e = chunk
                elif coordinate_type == 0: e = e1 + chunk
                netExtrude += chunk
                e1_orig = original_e
            else:
                e = None

            write_line = ""
            if g is not None: write_line += 'G' + str(int(g))
            if x is not None: write_line += ' X' + str(x)
            if y is not None: write_line += ' Y' + str(y)
            if g in (2, 3):
                if r is not None: write_line += ' R' + str(r)
                if i is not None: write_line += ' I' + str(i)
                if j is not None: write_line += ' J' + str(j)
            if z is not None: write_line += ' Z' + str(z)
            if a is not None: write_line += ' A' + str(a)
            if e is not None and g != 0: write_line += f' {EXTRUSION_AXIS}' + str(round(e, 3))
            if f is not None: write_line += ' F' + str(f)

            if 'NO E' in original_line:
                f_new.write(original_line)
                if original_e is not None:
                    if coordinate_type == 0: e -= chunk
                    netExtrude -= chunk
            else:
                f_new.write(write_line + "\n")

            x1 = x_val if x is not None else x1
            y1 = y_val if y is not None else y1
            z1 = z_val if z is not None else z1
            a1 = a_val if a is not None else a1
            e1 = e if e is not None else e1

            progress.advance(task)

    if DO_AUTO_PRESSURIZE:
        f_new.write(f"\n; Auto-depressurize syringe\n")
        f_new.write("G91 ; Switch to relative positioning for depressurize\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{-PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")
        if COORDINATE_MODE == "G90":
            f_new.write("G90 ; Switch back to absolute positioning\n")

    f_new.write("\n; --- End of Print Sequence ---\n")
    f_new.write("G91 ; Switch to relative positioning\n")
    f_new.write("G1 Z30 F300 ; Lift nozzle 30mm to safely clear the print\n")
    f_new.write("G90 ; Switch back to absolute positioning\n")
    if START_FROM_CENTER:
        f_new.write("G1 X0 Y0 F300 ; Park the bed back at the center\n")
    else:
        f_new.write("G1 X-50 Y-50 F300 ; Park the bed back at the original bottom-left edge\n")
    f_new.write("; -----------------------------\n")

    f_new.close()

    netVol = netExtrude * math.pi * (Z_SYRINGE_DIAMETER / 2)**2 / 1000
    
    success_text = f"Total Extrusion Distance: [bold yellow]{round(netExtrude, 3)} mm[/bold yellow]\nEstimated Volume: [bold yellow]{round(netVol, 3)} mL[/bold yellow]"
    console.print()
    console.print(Panel(success_text, title="[bold green]Translation Complete[/bold green]", border_style="green", expand=False))

    load_now = Prompt.ask("\nLoad this file for printing now?", choices=["y", "n"], default="y")
    if load_now.lower() == 'y':
        global loaded_filepath
        loaded_filepath = output_filepath
        console.print(f"[bold green]Loaded {output_filename}![/bold green]")
        time.sleep(1)

def load_file_menu():
    global loaded_filepath
    out_dir = "translated_gcode"

    os.makedirs(out_dir, exist_ok=True)
    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(out_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(f"[bold red]No files found in '{out_dir}'. Please translate a file first.[/bold red]"))
        time.sleep(2)
        return

    files.sort(key=lambda x: os.path.getmtime(os.path.join(out_dir, x)), reverse=True)

    file_table = Table(show_header=True, header_style="bold green", title="[bold cyan]Translated Files")
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")

    for i, f in enumerate(files):
        mtime = os.path.getmtime(os.path.join(out_dir, f))
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str)

    console.print(file_table)
    console.print(f"[0] Cancel")

    choice = IntPrompt.ask("\n[bold yellow]Select a file to load[/bold yellow]", choices=[str(i) for i in range(len(files) + 1)])
    if choice == 0: return

    selected_file = files[choice - 1]
    loaded_filepath = os.path.join(out_dir, selected_file)
    console.print(f"[bold green]Successfully loaded {selected_file}![/bold green]")
    time.sleep(1)

def check_for_pause(progress):
    if sys.stdin in select.select([sys.stdin], [], [], 0.0)[0]:
        sys.stdin.readline() 
        
        # Instantly freeze the printer's current movement using feedrate override
        try:
            printer_conn.write(b"M220 S0\n")
        except serial.SerialException:
            pass

        progress.stop()
        console.print("\n[bold yellow]PRINT PAUSED[/bold yellow]")
        
        action = Prompt.ask(
            "[bold cyan]Choose an action:[/bold cyan] [bold green](r)esume[/bold green] or [bold red](s)top[/bold red]", 
            choices=["r", "s"], 
            default="r"
        )
        
        if action == 's':
            console.print("[bold red]Cancelling print and parking bed...[/bold red]")
            try:
                printer_conn.write(b"M410\n")         # Quick Stop: Drops all buffered moves
                time.sleep(0.5)
                printer_conn.write(b"M220 S100\n")    # Restore normal speed 
                printer_conn.write(b"G91\n")
                printer_conn.write(b"G1 Z30 F300\n")
                printer_conn.write(b"G90\n")
                
                if START_FROM_CENTER:
                    printer_conn.write(b"G1 X0 Y0 F300\n")
                else:
                    printer_conn.write(b"G1 X-50 Y-50 F300\n")
            except Exception as e:
                console.print(f"[dim]Failed to send park command: {e}[/dim]")
            return True 
        else:
            console.print("[bold green]Resuming print...[/bold green]")
            try:
                printer_conn.write(b"M220 S100\n")    # Restore normal speed
            except serial.SerialException:
                pass
            progress.start()
            return False 
            
    return False

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
    
    if START_FROM_CENTER:
        warning_text = "ACTION REQUIRED: Please move the bed to the CENTER before continuing."
        prompt_text = "Is the bed in the center position?"
    else:
        warning_text = "ACTION REQUIRED: Please move the bed to the far bottom left corner before continuing."
        prompt_text = "Is the bed in the bottom left position?"
        
    console.print(Panel(f"[bold yellow]{warning_text}[/bold yellow]", border_style="yellow"))
    ready = Prompt.ask(prompt_text, choices=["y", "n"], default="y")
    
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
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        
        task = progress.add_task("[cyan]Printing...", total=len(lines))

        i = 0
        command_sent = False
        print_aborted = False
        
        while i < len(lines):
            if check_for_pause(progress):
                print_aborted = True
                break

            line = lines[i]
            stripped = line.strip()
            
            if not stripped or stripped.startswith(';'):
                i += 1
                progress.advance(task)
                continue
            
            command = stripped.split(';')[0].strip()
            
            if command:
                if not command_sent:
                    printer_conn.write((command + '\n').encode('utf-8'))
                    command_sent = True
                
                waiting_for_ok = True
                while waiting_for_ok:
                    if check_for_pause(progress):
                        print_aborted = True
                        break
                        
                    if printer_conn.in_waiting > 0:
                        try:
                            response = printer_conn.readline().decode('utf-8').strip()
                            if response == 'ok' or response.startswith('ok'):
                                waiting_for_ok = False
                        except serial.SerialException:
                            console.print("[bold red]Serial connection lost during print![/bold red]")
                            return
                    
                    time.sleep(0.01)
                    
            if print_aborted:
                break

            i += 1
            command_sent = False
            progress.advance(task)

        # Fix 2: Force synchronization at the very end of the file so we don't declare success early
        if not print_aborted and i >= len(lines):
            progress.update(task, description="[cyan]Finishing buffered moves in printer hardware...")
            try:
                printer_conn.write(b"M400\n")
                waiting_for_ok = True
                while waiting_for_ok:
                    if check_for_pause(progress):
                        print_aborted = True
                        break
                        
                    if printer_conn.in_waiting > 0:
                        try:
                            response = printer_conn.readline().decode('utf-8').strip()
                            if response == 'ok' or response.startswith('ok'):
                                waiting_for_ok = False
                        except serial.SerialException:
                            break
                    time.sleep(0.01)
            except Exception:
                pass

    if not print_aborted:
        console.print("\n[bold green]Print completed successfully![/bold green]")
        time.sleep(2)
    else:
        time.sleep(2)

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
            printer_conn.close()
            
        os.execl(sys.executable, sys.executable, *sys.argv)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]Failed to update from GitHub.[/bold red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        time.sleep(3)
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred: {e}[/bold red]")
        time.sleep(3)

def main():
    while True:
        console.clear()
        display_header()
        
        conn_status = f"[bold green]Connected ({printer_conn.port})[/bold green]" if printer_conn else "[bold red]Not Connected[/bold red]"
        console.print(f"Printer Status: {conn_status}")
        
        file_status = f"[bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]" if loaded_filepath else "[dim]None[/dim]"
        console.print(f"Loaded File:    {file_status}\n")

        console.print("[bold yellow]--- Main Menu ---[/bold yellow]")
        console.print("[1] Connect to Printer")
        console.print("[2] Translate G-Code")
        console.print("[3] Load Translated File")
        
        valid_choices = ["1", "2", "3", "7", "8", "9"]
        
        if printer_conn and loaded_filepath:
            console.print("[4] [bold green]Print Loaded File[/bold green]")
            valid_choices.append("4")
        else:
            console.print("[4] [dim]Print Loaded File (Requires Connection & File)[/dim]")
            
        if printer_conn:
            console.print("[5] [bold cyan]Manual G-Code Terminal[/bold cyan]")
            console.print("[6] [bold cyan]Adjust Printer[/bold cyan]")
            valid_choices.extend(["5", "6"])
        else:
            console.print("[5] [dim]Manual G-Code Terminal (Requires Connection)[/dim]")
            console.print("[6] [dim]Interactive Jog Control (Requires Connection)[/dim]")
            
        console.print("[7] Options / Settings")
        console.print("[8] Update ORCA from GitHub")
        console.print("[9] Exit\n")

        valid_choices.sort()

        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=valid_choices)

        if choice == "1":
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
        elif choice == "7":
            settings_menu()
        elif choice == "8":
            update_orca()
        elif choice == "9":
            if printer_conn:
                printer_conn.close()
            console.print("[bold magenta]Goodbye![/bold magenta]")
            break

if __name__== "__main__":
    try:
        main()
    except KeyboardInterrupt:
        if printer_conn:
            printer_conn.close()
        console.print("\n[bold magenta]Goodbye![/bold magenta]")