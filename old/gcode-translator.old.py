import math
import re
import sys
import traceback
import subprocess
import os
import platform

def main():
    print("--- Starting G-Code Conversion ---")
    try:
        file = open("gcode.txt", "r")
        content = file.readlines()
        file.close()
        print(f"Read {len(content)} lines from gcode.txt")
    except FileNotFoundError:
        print("Error: 'gcode.txt' not found. Make sure it is in the same folder.")
        return

    # --- USER CONFIGURATION (Update these if needed) ---
    Z_syringe_diameter    = 10.0
    A_syringe_diameter    = 10.0
    Z_nozzle_diameter     = 0.4
    A_nozzle_diameter     = 0.4
    extrusion_coefficient = 1.0 
    
    # --- AUTO-DETECT SETTINGS ---
    # Default to Absolute (0) unless G91 is explicitly found
    coordinate_type = 0 
    b_extrusion = False 
    
    # Scan first 100 lines for settings
    for i in range(min(100, len(content))):
        line = content[i].upper()
        if 'G91' in line:
            coordinate_type = 1
        if 'G90' in line:
            coordinate_type = 0
        if ' B' in line or 'B-' in line: # Detect if B is used for extrusion
            b_extrusion = True
            
    if coordinate_type == 0: print('Mode detected: G90 ABSOLUTE')
    if coordinate_type == 1: print('Mode detected: G91 RELATIVE')
    if b_extrusion: print('Extruder detected: B-axis')

    # --- SETUP OUTPUT ---
    f_new = open("gcode_modified.txt", "w+t")
    if coordinate_type == 0:
        f_new.write("G90\n")
    else:
        f_new.write("G91\n")

    # Initialize State
    x1, y1, z1, a1, e1 = 0, 0, 0, 0, 0
    extruder = 0 # 0 for T0, 1 for T1
    c_extrusion = False

    # Skip header lines usually containing comments/setup that we just wrote replacement for
    # (Adjust logic here if you want to keep the original header)
    gcode_body = content[6:] 

    line_number = 6
    for line in gcode_body:
        line_number += 1
        try:
            # 1. Reset logic
            if 'G92 E0' in line or 'G92 B0' in line:
                x1, y1, z1, a1, e1 = 0, 0, 0, 0, 0
            
            # 2. Pass-through comments and specific codes
            stripped = line.strip()
            if not stripped or stripped.startswith(';') or \
               any(x in line for x in ['G90', 'G91', 'G92', 'G21', 'M2', 'G4', 'M104', 'M105', 'M109', 'M82', 'M107']):
                f_new.write(line)
                continue
                
            if 'T0' in line:
                f_new.write('T0\n')
                extruder = 0
                continue
            if 'T1' in line:
                f_new.write('T1\n')
                extruder = 1
                continue
            
            # 3. Parse Line
            # We explicitly look for G, X, Y, Z, A, I, J, R, F
            # We ignore existing E/B/C values in the file because we are recalculating them
            current_vals = {'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None, 
                            'I': None, 'J': None, 'R': None, 'F': None}
            
            parts = line.split()
            has_comment = False
            
            for part in parts:
                if has_comment: break
                if part.startswith(';'): 
                    has_comment = True
                    continue
                
                # Cleanup part (remove inline comments like X10;comment)
                if ';' in part:
                    part = part.split(';')[0]
                    has_comment = True
                
                if len(part) > 1:
                    code = part[0].upper()
                    if code in current_vals:
                        try:
                            current_vals[code] = float(part[1:])
                        except ValueError:
                            pass # Skip bad numbers
            
            # Check if this is a movement line (Must have at least X, Y, Z, A, or R/I/J)
            if not any(current_vals[k] is not None for k in ['X', 'Y', 'Z', 'A', 'I', 'J', 'R']):
                # If it's just F or G without movement, write it and continue
                f_new.write(line)
                continue

            # 4. Extract values
            g = current_vals['G']
            x, y, z, a = current_vals['X'], current_vals['Y'], current_vals['Z'], current_vals['A']
            i_val, j_val, r = current_vals['I'], current_vals['J'], current_vals['R']
            f = current_vals['F']

            # Fill missing with current knowns or 0 for logic
            # For deltas (relative), we need the difference
            x_val = x if x is not None else (0 if coordinate_type == 1 else x1)
            y_val = y if y is not None else (0 if coordinate_type == 1 else y1)
            z_val = z if z is not None else (0 if coordinate_type == 1 else z1)
            a_val = a if a is not None else (0 if coordinate_type == 1 else a1)

            # Calculate deltas (movement distance components)
            if coordinate_type == 0: # Absolute
                dx = (x - x1) if x is not None else 0
                dy = (y - y1) if y is not None else 0
                dz = (z - z1) if z is not None else 0
                da = (a - a1) if a is not None else 0
            else: # Relative
                dx, dy, dz, da = x_val, y_val, z_val, a_val

            # 5. Calculate Length (l)
            l = 0
            if g == 1 or g == 0: # Linear move
                l = math.sqrt(dx**2 + dy**2 + dz**2 + da**2)
            elif g == 2 or g == 3: # Arc
                # (Simplified arc logic for stability)
                try:
                    radius = r
                    if radius is None and i_val is not None and j_val is not None:
                        radius = math.sqrt(i_val**2 + j_val**2)
                    
                    if radius:
                        chord = math.sqrt(dx**2 + dy**2 + dz**2 + da**2)
                        # Avoid domain error for acos
                        arg = 1 - (chord**2 / (2 * radius**2))
                        arg = max(-1.0, min(1.0, arg)) 
                        theta = 2 * math.pi - math.acos(arg)
                        l = radius * theta
                except Exception:
                    l = 0 # Fallback if arc math fails

            # 6. Calculate Extrusion (E)
            e_calc = None
            if g != 0: # Don't extrude on G0 (Rapid move)
                numerator = extrusion_coefficient * l * (Z_nozzle_diameter**2 if extruder==0 else A_nozzle_diameter**2)
                denominator = (Z_syringe_diameter**2 if extruder==0 else A_syringe_diameter**2)
                
                if denominator > 0:
                    e_step = numerator / denominator
                else:
                    e_step = 0

                if coordinate_type == 0: # Absolute
                    e_calc = e1 + e_step
                else: # Relative
                    e_calc = e_step
            
            # 7. Construct Output Line
            out_parts = []
            if g is not None: out_parts.append(f"G{int(g)}")
            if x is not None: out_parts.append(f"X{x}")
            if y is not None: out_parts.append(f"Y{y}")
            if z is not None: out_parts.append(f"Z{z}")
            if a is not None: out_parts.append(f"A{a}")
            if r is not None: out_parts.append(f"R{r}")
            if i_val is not None: out_parts.append(f"I{i_val}")
            if j_val is not None: out_parts.append(f"J{j_val}")
            
            # Handle Extrusion Output
            if e_calc is not None and 'NO E' not in line:
                val_str = str(round(e_calc, 4))
                if b_extrusion:
                    out_parts.append(f"B{val_str}")
                elif c_extrusion:
                    out_parts.append(f"C{val_str}")
                else:
                    out_parts.append(f"E{val_str}")
            
            if f is not None: out_parts.append(f"F{f}")
            
            f_new.write(" ".join(out_parts) + "\n")

            # 8. Update State
            if x is not None: x1 = x
            if y is not None: y1 = y
            if z is not None: z1 = z
            if a is not None: a1 = a
            if e_calc is not None: e1 = e_calc

        except Exception as e:
            print(f"\n!!! CRITICAL ERROR on Line {line_number} !!!")
            print(f"Line content: {line.strip()}")
            print(f"Error details: {e}")
            traceback.print_exc()
            break

    f_new.close()
    print("Conversion complete. Saved to gcode_modified.txt")

    # Open file
    filepath = "gcode_modified.txt"
    if platform.system() == 'Darwin':       
        subprocess.call(('open', filepath))
    elif platform.system() == 'Windows':    
        try: os.startfile(filepath)
        except: pass
    else:                                   
        try: subprocess.call(('xdg-open', filepath))
        except: pass

if __name__== "__main__":
    main()