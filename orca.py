import math
import os
import platform
import subprocess
import time
import sys
import select
from datetime import datetime

# Import rich for the beautiful TUI
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

# Initialize the Rich Console
console = Console()

# ==========================================
# --- CONFIGURATION PARAMETERS ---
# ==========================================
COORDINATE_MODE = "G90"         # 'G90' for Absolute, 'G91' for Relative
EXTRUSION_AXIS = "B"            # The target axis for extrusion ('B' or 'C')
Z_SYRINGE_DIAMETER = 4.9        # Inner diameter in mm (4.9 for 1mL BD syringe)
A_SYRINGE_DIAMETER = 4.9
Z_NOZZLE_DIAMETER = 0.2         # Nozzle diameter in mm
A_NOZZLE_DIAMETER = 0.2
EXTRUSION_COEFFICIENT = 0.33    # Scaling factor for extrusion

# Auto-Pressurization Settings
DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 0.2
PRESSURIZE_SPEED = 400

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
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⠟⠉⠀⠀⠀⣸⣿⣿⣿⣿⡿⠟⠛⠋⠉⠐⠊⠡⢹⢚   ____  _____   _____         
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⣠⣤⣤⡴⠂⠐⠒⢨⣿⣿⣿⣿⣿⣿⣤⣆⣤⣠⣴⣾⣿⣷⡿⠋⠁⠀⠀⠀⠀⠀⠐⣁⠎⠀⡘  / __ \|  __ \ / ____|  /\    
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢐⣠⣤⣶⣾⣿⣿⣿⣿⣿⣆⡀⡀⣀⣨⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡜⠀⠀⡐⠀ | |  | | |__) | |       /  \   
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⣴⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠄⠀⠄⠀⠀⠀⠀⠀⠂⠀⠀ | |  | |  _  /| |      / /\ \  
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣠⣴⡶⠿⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡠⠂⠀⠀⠀ | |__| | | \ \| |____ / ____ \ 
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣵⣿⣿⣅⠀⠀⠀⠀⢈⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠖⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠂⠀⠀⠀⠀⠀  \____/|_|  \_\\_____/_/    \_\
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣶⣦⣌⠁⠀⠉⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡏⡞⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⠜⠁⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣀⣀⣤⢤⢤⡴⢶⣾⡿⠿⣛⠩⠀⠉⠉⠙⠛⠻⠿⢏⡀⠀⠀⠀⠙⠻⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⢈⡷⠀⠀⠀⠀⠀⠀⠀⠀⣠⣷⣿⡀⠀⠀⠀⠀⠀⠀⠀         [cyan]v1.0.3[/cyan]
⢠⠖⠋⠉⠀⢀⠀⠂⣌⢇⠀⣰⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠳⣄⠀⡀⠀⠀⢀⣽⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⣐⠰⠂⠀⠀⠀⠀⡀⣠⣴⣾⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠛⠓⠒⠲⢤⣀⣐⣤⡞⣸⢊⠥⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠀⢀⣤⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⢄⣀⠀⠠⠤⠴⠂⠈⠁⢰⣿⣿⣿⣿⣿⣿⡇⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⠃⠀⠀⠸⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠉⠉⠉⠉⠋⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⣿⣿⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢖⣦⣀⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢻⣿⣿⣿⣿⠃⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠾⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠸⣿⡿⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀
"""
    # Replace the braille blank characters with standard spaces so they don't render as dots
    splash = splash.replace('\u2800', ' ')
    console.print(splash, style="bold white")

def settings_menu():
    global COORDINATE_MODE, EXTRUSION_COEFFICIENT, DO_AUTO_PRESSURIZE
    
    while True:
        console.clear()
        display_header()
        
        # Build Configuration Table
        config_table = Table(show_header=True, header_style="bold yellow", expand=True, title="[bold cyan]Current Configuration[/bold cyan]")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")
        config_table.add_column("Parameter")
        config_table.add_column("Value", style="cyan")

        config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
        config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
        config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
        config_table.add_row("Extrusion Coeff.", str(EXTRUSION_COEFFICIENT), "Auto-Pressurize", "[green]ON[/green]" if DO_AUTO_PRESSURIZE else "[red]OFF[/red]")
        
        console.print(config_table)
        console.print("\n[bold yellow]--- Options Menu ---[/bold yellow]")
        console.print("[1] Change Extrusion Coefficient")
        console.print("[2] Toggle Auto-Pressurize")
        console.print("[3] Toggle Coordinate Mode (G90/G91)")
        console.print("[4] Return to Main Menu\n")
        
        choice = Prompt.ask("[bold yellow]Choose an option[/bold yellow]", choices=["1", "2", "3", "4"])
        
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
            # Wake up printer
            printer_conn.write(b"\r\n\r\n")
            time.sleep(2)
            printer_conn.flushInput()
            console.print(f"[bold green]Successfully connected to {selected_port}![/bold green]")
            time.sleep(1)
    except Exception as e:
        console.print(f"[bold red]Failed to connect: {e}[/bold red]")
        printer_conn = None
        time.sleep(2)

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
        "Type [bold yellow]'q'[/bold yellow] or [bold yellow]'quit'[/bold yellow] to return to the main menu.", 
        border_style="cyan"
    ))
    
    while True:
        cmd = Prompt.ask("[bold green]>[/bold green]")
        
        if cmd.lower() in ['q', 'quit', 'exit']:
            break
            
        if not cmd.strip():
            continue
            
        try:
            printer_conn.write((cmd + '\n').encode('utf-8'))
            
            # Read response from the printer
            response_lines = []
            while True:
                response = printer_conn.readline().decode('utf-8').strip()
                if response:
                    response_lines.append(response)
                    # Break when the printer confirms it has processed the command
                    if response == 'ok' or response.startswith('ok'):
                        break
                else:
                    # Break if readline times out (no response received)
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

    # --- INJECTED STARTUP SEQUENCE ---
    f_new.write("; --- Center Bed & Clear Dish Sequence ---\n")
    f_new.write("G90 ; Force absolute positioning for setup\n")
    f_new.write("G92 X0 Y0 Z0 ; Zero at confirmed bottom-left corner\n")
    f_new.write("G1 Z30 F300 ; Z-hop up 30mm to clear dish walls\n")
    f_new.write("G1 X50 Y50 F300 ; Move to the center\n")
    f_new.write("G1 Z0 F300 ; Drop back down to original height before printing\n")
    f_new.write("G92 X0 Y0 Z0 ; Re-zero all axes at the center\n")
    
    if COORDINATE_MODE == "G91":
        f_new.write("G91 ; Restore relative positioning\n")
    f_new.write("; ----------------------------------------\n\n")

    if DO_AUTO_PRESSURIZE:
        f_new.write("; Auto-pressurize syringe\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n\n")

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
                        chunk = e_change
            
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
        f_new.write(f"G1 {EXTRUSION_AXIS}{-PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")

    # --- INJECTED END OF PRINT SEQUENCE ---
    f_new.write("\n; --- End of Print Sequence ---\n")
    f_new.write("G91 ; Switch to relative positioning\n")
    f_new.write("G1 Z30 F300 ; Lift nozzle 30mm to safely clear the print\n")
    f_new.write("G90 ; Switch back to absolute positioning\n")
    f_new.write("G1 X-50 Y-50 F800 ; Park the bed back at the original bottom-left edge\n")
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
    """
    Checks if there is keyboard input waiting. 
    If there is, pauses the progress bar, clears the input, and asks what to do.
    Returns True if the print should be STOPPED, False if it should RESUME.
    """
    # select.select on sys.stdin works great for Mac to non-blockingly check for input
    if sys.stdin in select.select([sys.stdin], [], [], 0.0)[0]:
        sys.stdin.readline() # Consume whatever key was pressed
        
        progress.stop()
        console.print("\n[bold yellow]⚠️  PRINT PAUSED[/bold yellow]")
        
        action = Prompt.ask(
            "[bold cyan]Choose an action:[/bold cyan] [bold green](r)esume[/bold green] or [bold red](s)top[/bold red]", 
            choices=["r", "s"], 
            default="r"
        )
        
        if action == 's':
            console.print("[bold red]Cancelling print and parking bed...[/bold red]")
            try:
                # Lift nozzle and park so it doesn't melt the dish
                printer_conn.write(b"G91\n")
                printer_conn.write(b"G1 Z30 F300\n")
                printer_conn.write(b"G90\n")
                printer_conn.write(b"G1 X-50 Y-50 F800\n")
            except Exception as e:
                console.print(f"[dim]Failed to send park command: {e}[/dim]")
            return True # Stop the print loop
        else:
            console.print("[bold green]Resuming print...[/bold green]")
            progress.start()
            return False # Resume the print loop
            
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

    # User confirmation for bed position before printing
    console.print()
    console.print(Panel("[bold yellow]ACTION REQUIRED: Please move the bed to the far bottom left corner before continuing.[/bold yellow]", border_style="yellow"))
    ready = Prompt.ask("Is the bed in the bottom left position?", choices=["y", "n"], default="y")
    
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
        f"[bold cyan]💡 Tip: Press ENTER at any time to PAUSE the print.[/bold cyan]"
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
            # Check for pause BEFORE sending the line
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
                # Only send the command if we haven't already sent it for this index
                if not command_sent:
                    printer_conn.write((command + '\n').encode('utf-8'))
                    command_sent = True
                
                # Wait non-blockingly for 'ok'
                waiting_for_ok = True
                while waiting_for_ok:
                    # Check for pause DURING the wait for the printer
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
                    
                    # Small sleep to keep CPU usage low while waiting
                    time.sleep(0.01)
                    
            if print_aborted:
                break

            i += 1
            command_sent = False
            progress.advance(task)

    if not print_aborted and i >= len(lines):
        console.print("\n[bold green]Print completed successfully![/bold green]")
        time.sleep(2)
    else:
        time.sleep(2)

def main():
    while True:
        console.clear()
        display_header()
        
        # Connection Status
        conn_status = f"[bold green]Connected ({printer_conn.port})[/bold green]" if printer_conn else "[bold red]Not Connected[/bold red]"
        console.print(f"Printer Status: {conn_status}")
        
        # File Status
        file_status = f"[bold cyan]{os.path.basename(loaded_filepath)}[/bold cyan]" if loaded_filepath else "[dim]None[/dim]"
        console.print(f"Loaded File:    {file_status}\n")

        # Render Main Menu
        console.print("[bold yellow]--- Main Menu ---[/bold yellow]")
        console.print("[1] Connect to Printer")
        console.print("[2] Translate G-Code")
        console.print("[3] Load Translated File")
        
        # Dynamic Menu Options based on state
        valid_choices = ["1", "2", "3", "6", "7"]
        
        if printer_conn and loaded_filepath:
            console.print("[4] [bold green]Print Loaded File[/bold green]")
            valid_choices.append("4")
        else:
            console.print("[4] [dim]Print Loaded File (Requires Connection & File)[/dim]")
            
        if printer_conn:
            console.print("[5] [bold cyan]Manual Printer Control[/bold cyan]")
            valid_choices.append("5")
        else:
            console.print("[5] [dim]Manual Printer Control (Requires Connection)[/dim]")
            
        console.print("[6] Options / Settings")
        console.print("[7] Exit\n")

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
            settings_menu()
        elif choice == "7":
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