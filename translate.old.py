import math
import os
import platform
import subprocess
from datetime import datetime

# Import rich for the beautiful TUI
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import IntPrompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
except ImportError:
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
Z_NOZZLE_DIAMETER = 2         # Nozzle diameter in mm Way too 
A_NOZZLE_DIAMETER = 0.2
EXTRUSION_COEFFICIENT = 1.0     # Scaling factor for extrusion

# Auto-Pressurization Settings
DO_AUTO_PRESSURIZE = True
PRESSURIZE_AMOUNT = 0.2
PRESSURIZE_SPEED = 400
# ==========================================

def display_header():
    console.print(Panel("[bold magenta]G-Code Translator by Gabriel[/bold magenta]", border_style="cyan", expand=False))

    # Build Configuration Table
    config_table = Table(show_header=True, header_style="bold yellow", expand=True)
    config_table.add_column("Parameter")
    config_table.add_column("Value", style="cyan")
    config_table.add_column("Parameter")
    config_table.add_column("Value", style="cyan")

    config_table.add_row("Coordinate Mode", COORDINATE_MODE, "Extrusion Axis", EXTRUSION_AXIS)
    config_table.add_row("Z Syringe (mm)", str(Z_SYRINGE_DIAMETER), "A Syringe (mm)", str(A_SYRINGE_DIAMETER))
    config_table.add_row("Z Nozzle (mm)", str(Z_NOZZLE_DIAMETER), "A Nozzle (mm)", str(A_NOZZLE_DIAMETER))
    config_table.add_row("Extrusion Coeff.", str(EXTRUSION_COEFFICIENT), "Auto-Pressurize", "[green]ON" if DO_AUTO_PRESSURIZE else "[red]OFF")
    
    console.print(config_table)
    console.print()

def main():
    console.clear()
    display_header()

    # Directory configuration
    raw_dir = "raw_gcode"
    out_dir = "translated_gcode"

    # Ensure directories exist
    if not os.path.exists(raw_dir):
        os.makedirs(raw_dir)
        console.print(Panel(f"[bold yellow]Created '{raw_dir}' directory.[/bold yellow]\n\nPlease place your raw .gcode or .txt files in that folder and run this script again.", title="[bold red]Action Required"))
        return

    os.makedirs(out_dir, exist_ok=True)

    # Index .gcode and .txt files
    valid_extensions = ('.gcode', '.txt')
    files = [f for f in os.listdir(raw_dir) if f.lower().endswith(valid_extensions)]

    if not files:
        console.print(Panel(f"[bold red]No .gcode or .txt files found in the '{raw_dir}' directory.[/bold red]"))
        return

    # Sort files by date modified (newest first)
    files.sort(key=lambda x: os.path.getmtime(os.path.join(raw_dir, x)), reverse=True)

    # Present list to user in a table
    file_table = Table(show_header=True, header_style="bold green", title="[bold cyan]Available Files in 'raw_gcode'")
    file_table.add_column("#", justify="right", style="cyan", no_wrap=True)
    file_table.add_column("Filename", style="magenta")
    file_table.add_column("Last Modified", justify="right", style="green")

    for i, f in enumerate(files):
        mtime = os.path.getmtime(os.path.join(raw_dir, f))
        dt_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        file_table.add_row(str(i + 1), f, dt_str)

    console.print(file_table)

    # User selection
    choice = IntPrompt.ask("\n[bold yellow]Select the number of the file to translate[/bold yellow]", choices=[str(i) for i in range(1, len(files) + 1)])
    selected_file = files[choice - 1]
    input_filepath = os.path.join(raw_dir, selected_file)

    # Generate timestamped output filename
    base_name, ext = os.path.splitext(selected_file)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{base_name}_{timestamp}{ext}"
    output_filepath = os.path.join(out_dir, output_filename)

    try:
        with open(input_filepath, "r") as file:
            content = file.readlines()
    except FileNotFoundError:
        console.print(f"[bold red]Error: '{input_filepath}' not found.[/bold red]")
        return

    coordinate_type = 0 if 'G90' in COORDINATE_MODE else 1
    extrusion_coefficient = EXTRUSION_COEFFICIENT
    extruder = 0
    netExtrude = 0

    console.print(f"\n[bold green]Translating[/bold green] [cyan]'{selected_file}'[/cyan] -> [cyan]'{output_filename}'[/cyan]...\n")

    f_new = open(output_filepath, "w+t")
    f_new.write(COORDINATE_MODE + "\n")

    if DO_AUTO_PRESSURIZE:
        f_new.write("; Auto-pressurize syringe\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n\n")

    x1, y1, e1, a1, z1 = 0, 0, 0, 0, 0

    # ---------------------------------------------------------
    # --- PROCESSING LOOP WITH FUN ANIMATION ---
    # ---------------------------------------------------------
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

            # 1. Strip M-commands (except M106/M107 for the mixing nozzle)
            if stripped_line.startswith('M'):
                if not (stripped_line.startswith('M106') or stripped_line.startswith('M107')):
                    progress.advance(task)
                    continue 

            # 2. Skip old config headers
            if "syringe_diameter" in stripped_line or "nozzle_diameter" in stripped_line or "extrusion_coefficient" in stripped_line:
                progress.advance(task)
                continue

            # 3. Handle G92 resets
            if 'G92 E0' in stripped_line or f'G92 {EXTRUSION_AXIS}0' in stripped_line:
                x1, y1, e1, a1, z1 = 0, 0, 0, 0, 0

            # 4. Skip/copy lines that are empty or comments
            if not stripped_line or stripped_line.startswith(';') or 'G90' in stripped_line or 'G91' in stripped_line or 'G92' in stripped_line or 'G21' in stripped_line or 'G4' in stripped_line:
                if ('G90' in stripped_line or 'G91' in stripped_line) and "G9" in original_line[:3]:
                    progress.advance(task)
                    continue
                f_new.write(original_line)
                progress.advance(task)
                continue

            # Handle tool changes
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

            # Handle inline coefficient changes
            if stripped_line.startswith('K') or stripped_line.startswith('k'):
                new_k = stripped_line.split('=')
                try:
                    extrusion_coefficient = float(new_k[-1].strip())
                    f_new.write(f"; extrusion coefficient changed to = {extrusion_coefficient}\n")
                except ValueError:
                    pass
                progress.advance(task)
                continue

            # Ignore manual B/C triggers from the old script
            if stripped_line.startswith('B') or stripped_line.startswith('b') or stripped_line.startswith('C') or stripped_line.startswith('c'):
                progress.advance(task)
                continue

            # Parse commands
            letters = {'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None, 'I': None, 'J': None, 'R': None, 'T': None, 'E': None, 'F': None}
            var = False
            for command in stripped_line.split():
                if command.startswith(';'):
                    break
                if command.endswith(';'):
                    command = command[:-1]
                    var = True
                if command[0] in letters:
                    try:
                        letters[command[0]] = float(command[1:])
                    except ValueError:
                        pass
                if var:
                    break

            if not any((letters[c] for c in 'XYZAIJRT' if c in letters and letters[c] is not None)):
                f_new.write(original_line)
                progress.advance(task)
                continue

            # Retrieve parsed values
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
            e = None
            
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

            # Calculate geometric length
            if g == 1:
                if coordinate_type == 1: 
                    l = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                elif coordinate_type == 0: 
                    l = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
            elif g == 2 or g == 3:
                full_circle = False
                radius = r
                if radius is None:
                    radius = math.sqrt(i_val**2 + j_val**2)
                
                if coordinate_type == 1: 
                    if x_val != 0 or y_val != 0 or z_val != 0 or a_val != 0:
                        d = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                        val = 1 - (d**2 / (2 * radius**2))
                        val = max(-1.0, min(1.0, val))
                        theta = 2*math.pi - math.acos(val)
                    else:
                        theta = 2 * math.pi
                        full_circle = True
                elif coordinate_type == 0: 
                    if x is not None or y is not None or z is not None or a is not None:
                        d = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
                        val = 1 - (d**2 / (2 * radius**2))
                        val = max(-1.0, min(1.0, val))
                        theta = 2*math.pi - math.acos(val)
                    else:
                        theta = 2 * math.pi
                        full_circle = True
                l = radius * theta
                if g == 3 and not full_circle:
                    l = 2 * math.pi * radius - l 
            
            # --- CORRECTED EXTRUSION MATH ---
            # Calculate the incremental chunk for this specific move
            if extruder == 0:
                chunk = (extrusion_coefficient * l * Z_NOZZLE_DIAMETER**2) / (Z_SYRINGE_DIAMETER**2)
            else:
                chunk = (extrusion_coefficient * l * A_NOZZLE_DIAMETER**2) / (A_SYRINGE_DIAMETER**2)
            
            # Apply chunk to coordinates and running total
            if coordinate_type == 1: # relative
                e = chunk
            elif coordinate_type == 0: # absolute
                e = e1 + chunk
            
            netExtrude += chunk

            # Build the modified G-code line
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
            
            if e is not None and g != 0:
                write_line += f' {EXTRUSION_AXIS}' + str(round(e, 3))
            
            if f is not None: write_line += ' F' + str(f)

            # Override if 'NO E' is in the original line comment
            if 'NO E' in original_line:
                f_new.write(original_line)
                # Undo the increment since no extrusion actually happened
                if coordinate_type == 0:
                    e -= chunk
                netExtrude -= chunk
            else:
                f_new.write(write_line + "\n")

            # Update tracking coordinates
            x1 = x_val if x is not None else x1
            y1 = y_val if y is not None else y1
            z1 = z_val if z is not None else z1
            a1 = a_val if a is not None else a1
            e1 = e if e is not None else e1

            # Advance the progress bar
            progress.advance(task)

    # Auto-depressurize at the very end
    if DO_AUTO_PRESSURIZE:
        f_new.write(f"\n; Auto-depressurize syringe\n")
        f_new.write(f"G1 {EXTRUSION_AXIS}{-PRESSURIZE_AMOUNT} F{PRESSURIZE_SPEED}\n")

    f_new.close()

    # Calculate Volume
    netVol = netExtrude * math.pi * (Z_SYRINGE_DIAMETER / 2)**2 / 1000
    
    # Success Panel
    success_text = f"Total Extrusion Distance: [bold yellow]{round(netExtrude, 3)} mm[/bold yellow]\nEstimated Volume: [bold yellow]{round(netVol, 3)} mL[/bold yellow]"
    console.print()
    console.print(Panel(success_text, title="[bold green]Translation Complete[/bold green]", border_style="green", expand=False))

    # Open file automatically
    if platform.system() == 'Darwin':       # macOS
        subprocess.call(('open', output_filepath))
    elif platform.system() == 'Windows':    # Windows
        try:
            os.startfile(output_filepath)
        except AttributeError: 
            subprocess.call(('cmd', '/c', 'start', '', output_filepath))
    else:                                   # linux variants
        subprocess.call(('xdg-open', output_filepath))

if __name__== "__main__":
    main()