#!/usr/bin/env python3
"""
ORCA v3.0 Gemini Edition — Bioprinter Control System

Simplified, stable TUI for controlling a 3D bioprinter via serial.
Zero external TUI dependencies (no rich) for maximum operational stability.
All movement speeds are hard-clamped to never exceed F300.
"""

import math
import os
import re
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

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Missing dependency. Please run: pip install pyserial")
    sys.exit(1)

# ============================================================
#  CONSTANTS
# ============================================================
VERSION = "3.0.0-Gemini"
MAX_FEEDRATE = 300
RAW_DIR = "raw_gcode"
OUT_DIR = "translated_gcode"
JOG_STEPS = [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
FILAMENT_DIA = 1.75  # mm (for scaling pure extrusion if needed)

# ============================================================
#  CONFIGURATION
# ============================================================
class Config:
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
        self.jog_step_index = 2            # Default: 0.2 mm
        self.start_from_center = False
        self.baud_rate = 115200

    @property
    def jog_distance(self):
        return JOG_STEPS[self.jog_step_index]

# ============================================================
#  UTILITIES
# ============================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def clamp_feedrate(line):
    """Ensure no F-parameter in a G-code string exceeds MAX_FEEDRATE."""
    def _clamp(match):
        val = min(float(match.group(1)), MAX_FEEDRATE)
        return f"F{int(val)}" if val == int(val) else f"F{val:g}"
    return re.sub(r'F(\d+\.?\d*)', _clamp, line, flags=re.IGNORECASE)

def clean_gcode(cmd):
    """Sanitize typed G-code."""
    cmd = re.sub(r'[\u2013\u2014\u2212]', '-', cmd)           # Smart dashes to ASCII hyphens
    cmd = re.sub(r'([A-Za-z])\s+([-.\d])', r'\1\2', cmd)      # "X -5" -> "X-5"
    return cmd

def _read_key():
    """Non-blocking single-keypress read (requires raw terminal)."""
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
    """Non-blocking check: did the user press Enter/Any key?"""
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
#  BIOPRINTER CLASS (SERIAL COMM)
# ============================================================
class Bioprinter:
    """Manages serial execution. All outgoing commands are clamped to F300."""
    def __init__(self):
        self._conn = None

    @property
    def is_connected(self):
        return self._conn is not None and self._conn.is_open

    @property
    def port(self):
        return self._conn.port if self._conn else None

    @property
    def has_data(self):
        return self._conn is not None and self._conn.in_waiting > 0

    def connect(self, port, baud):
        self.disconnect()
        self._conn = serial.Serial(port, baud, timeout=2)
        # DTR Toggle
        self._conn.setDTR(False)
        time.sleep(0.05)
        self._conn.setDTR(True)
        self.flush()
        self._conn.write(b"\n\n")
        time.sleep(2)
        self.flush()

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def flush(self):
        if self._conn:
            self._conn.reset_input_buffer()
            self._conn.reset_output_buffer()

    def send(self, cmd, timeout=5.0):
        self._require()
        cmd = clamp_feedrate(cmd.strip())
        self._conn.write((cmd + '\n').encode('ascii', errors='ignore'))
        return self._wait_ok(timeout)

    def send_nowait(self, cmd):
        self._require()
        cmd = clamp_feedrate(cmd.strip())
        self._conn.write((cmd + '\n').encode('ascii', errors='ignore'))

    def read_line(self):
        if self.has_data:
            try:
                return self._conn.readline().decode('utf-8', errors='ignore').strip()
            except Exception:
                return None
        return None

    def reset_board(self):
        self._require()
        self._conn.write(b"M112\n")
        time.sleep(0.1)
        self._conn.write(b"M999\n")
        self._conn.setDTR(False)
        time.sleep(0.5)
        self._conn.setDTR(True)
        self.flush()

    def _wait_ok(self, timeout):
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
        return lines

    def _require(self):
        if not self.is_connected:
            raise RuntimeError("Printer not connected")

# ============================================================
#  SPLASH / HEADER
# ============================================================
def display_header():
    print(f"============================================================")
    print(f"               ORCA BIOPRINTER CONTROL                      ")
    print(f"                   Version {VERSION}                        ")
    print(f"============================================================")
    print()

# ============================================================
#  MENUS & FEATURES
# ============================================================
def connect_menu(printer, config):
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("ERROR: No serial ports found. Is the printer plugged in?")
        time.sleep(2)
        return

    print("Available Ports:")
    for i, p in enumerate(ports, 1):
        print(f"  [{i}] {p.device} -- {p.description}")
    print("  [0] Cancel")
    print()

    try:
        choice = int(input("Select port > "))
    except ValueError:
        return

    if choice == 0 or choice > len(ports):
        return

    port = ports[choice - 1].device
    print(f"\nConnecting to {port} at {config.baud_rate} baud...")
    try:
        printer.connect(port, config.baud_rate)
        print(f"SUCCESS: Connected to {port}!")
        time.sleep(1)
    except Exception as e:
        print(f"\nERROR: Connection failed: {e}")
        time.sleep(2)

def reset_menu(printer):
    if not printer.is_connected:
        print("ERROR: Printer not connected.")
        time.sleep(1)
        return

    print("\nSending reset signals...")
    try:
        printer.reset_board()
        print("SUCCESS: Reset complete. Wait a few seconds for reboot.")
        time.sleep(2)
    except Exception as e:
        print(f"ERROR: Reset failed: {e}")
        time.sleep(3)

def settings_menu(config):
    while True:
        clear_screen()
        display_header()
        print("--- CURRENT SETTINGS ---")
        print(f"Extrusion Coefficient : {config.extrusion_coeff}")
        print(f"Auto-Pressurize       : {'ON' if config.auto_pressurize else 'OFF'}")
        print(f"Coordinate Mode       : {config.coordinate_mode}")
        print(f"Start From Center     : {'ON' if config.start_from_center else 'OFF'}")
        print(f"Jog Step Size         : {config.jog_distance} mm")
        print(f"Extrusion Axis        : {config.extrusion_axis}")
        print()

        print("[1] Change Extrusion Coefficient")
        print("[2] Toggle Auto-Pressurize")
        print("[3] Toggle Coordinate Mode (G90/G91)")
        print("[4] Toggle Start from Center")
        print("[5] Back to Main Menu")
        print()

        c = input("Choose > ").strip()
        if c == "1":
            val = input(f"New coefficient (current: {config.extrusion_coeff}): ").strip()
            try:
                config.extrusion_coeff = float(val)
            except ValueError:
                print("Invalid number.")
                time.sleep(1)
        elif c == "2":
            config.auto_pressurize = not config.auto_pressurize
        elif c == "3":
            config.coordinate_mode = "G91" if config.coordinate_mode == "G90" else "G90"
        elif c == "4":
            config.start_from_center = not config.start_from_center
        elif c == "5":
            break

def translate_gcode(config):
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    files = sorted(
        [f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.gcode', '.txt'))],
        key=lambda x: os.path.getmtime(os.path.join(RAW_DIR, x)),
        reverse=True,
    )

    if not files:
        print(f"ERROR: No .gcode or .txt files found in '{RAW_DIR}/'.")
        time.sleep(2)
        return None

    print(f"--- Files in '{RAW_DIR}' ---")
    for i, fname in enumerate(files, 1):
        mtime = datetime.fromtimestamp(os.path.getmtime(os.path.join(RAW_DIR, fname)))
        print(f"  [{i}] {fname}  (Modified: {mtime.strftime('%Y-%m-%d %H:%M')})")
    print("  [0] Cancel")
    print()

    try:
        choice = int(input("Select file to translate > "))
    except ValueError:
        return None
    if choice == 0 or choice > len(files):
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
        print(f"\nERROR reading file: {e}")
        time.sleep(2)
        return None

    print(f"\nTranslating '{selected}' -> '{output_name}'...")

    coord_type = 0 if config.coordinate_mode == "G90" else 1
    extrusion_coeff = config.extrusion_coeff
    ext_axis = config.extrusion_axis
    extruder, net_extrude = 0, 0.0
    x1, y1, z1, a1, e1 = 0.0, 0.0, 0.0, 0.0, 0.0
    e1_orig = 0.0
    total_lines = len(content)

    with open(output_path, "w") as out:
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

        if config.auto_pressurize:
            out.write("; Auto-pressurize syringe\n")
            out.write("G91 ; Relative for pressurize\n")
            out.write(f"G1 {ext_axis}{config.pressurize_amount} F{MAX_FEEDRATE}\n")
            if config.coordinate_mode == "G90":
                out.write("G90 ; Back to absolute\n")
            out.write(f"G92 {ext_axis}0 ; Re-zero extrusion axis\n\n")

        for i_line, line in enumerate(content):
            # Print simple progress safely every 5%
            if i_line % max(1, total_lines // 20) == 0:
                print(f"  Progress: {int((i_line/total_lines)*100)}% ...")

            original = line
            stripped = line.strip()

            if stripped.startswith('M') and not (stripped.startswith('M106') or stripped.startswith('M107')):
                continue
            if any(kw in stripped for kw in ('syringe_diameter', 'nozzle_diameter', 'extrusion_coefficient')):
                continue
            if 'G92 E0' in stripped or f'G92 {ext_axis}0' in stripped:
                x1, y1, z1, a1, e1 = 0, 0, 0, 0, 0
                e1_orig = 0

            pass_through = not stripped or stripped.startswith(';') or 'G90' in stripped or 'G91' in stripped or 'G92' in stripped or 'G21' in stripped or 'G4' in stripped
            if pass_through:
                if ('G90' in stripped or 'G91' in stripped) and 'G9' in original[:3]:
                    continue
                if 'G92' in stripped and 'E' in stripped:
                    out.write(clamp_feedrate(original.replace('E', ext_axis)))
                else:
                    out.write(clamp_feedrate(original))
                continue

            if 'T0' in stripped:
                out.write('T0\n')
                extruder = 0
                continue
            if 'T1' in stripped:
                out.write('T1\n')
                extruder = 1
                continue
            if stripped.upper().startswith('K'):
                parts = stripped.split('=')
                try:
                    extrusion_coeff = float(parts[-1].strip())
                    out.write(f"; extrusion coefficient changed to = {extrusion_coeff}\n")
                except ValueError:
                    pass
                continue
            if stripped[0].upper() in ('B', 'C'):
                continue

            fields = {'G': None, 'X': None, 'Y': None, 'Z': None, 'A': None, 'I': None, 'J': None, 'R': None, 'T': None, 'E': None, 'F': None}
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

            if not any(fields[c] is not None for c in 'XYZAIJRT'):
                out.write(clamp_feedrate(original))
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

            x_val, y_val = (x if x is not None else 0), (y if y is not None else 0)
            z_val, a_val = (z if z is not None else 0), (a if a is not None else 0)
            x_rel, y_rel = ((x_val - x1) if x is not None else 0), ((y_val - y1) if y is not None else 0)
            z_rel, a_rel = ((z_val - z1) if z is not None else 0), ((a_val - a1) if a is not None else 0)

            path_len = 0.0
            if g == 1:
                if coord_type == 1:
                    path_len = math.sqrt(x_val**2 + y_val**2 + a_val**2 + z_val**2)
                else:
                    path_len = math.sqrt(x_rel**2 + y_rel**2 + a_rel**2 + z_rel**2)
            elif g in (2, 3):
                full_circle = False
                radius = r if r is not None else math.sqrt(i_v**2 + j_v**2)
                cur_x, cur_y, cur_z, cur_a = (x_val, y_val, z_val, a_val) if coord_type == 1 else (x_rel, y_rel, z_rel, a_rel)

                if coord_type == 1:
                    if x_val != 0 or y_val != 0 or z_val != 0 or a_val != 0:
                        d = math.sqrt(cur_x**2 + cur_y**2 + cur_a**2 + cur_z**2)
                        val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                        theta = 2 * math.pi - math.acos(val)
                    else:
                        theta, full_circle = 2 * math.pi, True
                else:
                    if x is not None or y is not None or z is not None or a is not None:
                        d = math.sqrt(cur_x**2 + cur_y**2 + cur_a**2 + cur_z**2)
                        val = max(-1.0, min(1.0, 1 - (d**2 / (2 * radius**2))))
                        theta = 2 * math.pi - math.acos(val)
                    else:
                        theta, full_circle = 2 * math.pi, True

                path_len = radius * theta
                if g == 3 and not full_circle:
                    path_len = 2 * math.pi * radius - path_len

            if original_e is None:
                chunk, e = 0, None
            else:
                e_change = original_e if coord_type == 1 else (original_e - e1_orig)
                if e_change == 0:
                    chunk = 0
                elif path_len > 0:
                    dia_nozzle = config.z_nozzle_dia if extruder == 0 else config.a_nozzle_dia
                    dia_syringe = config.z_syringe_dia if extruder == 0 else config.a_syringe_dia
                    chunk = (extrusion_coeff * path_len * dia_nozzle**2 / dia_syringe**2)
                    if e_change < 0: chunk = -chunk
                else:
                    dia_syringe = config.z_syringe_dia if extruder == 0 else config.a_syringe_dia
                    chunk = e_change * FILAMENT_DIA**2 / dia_syringe**2
                
                e = chunk if coord_type == 1 else e1 + chunk
                net_extrude += chunk
                e1_orig = original_e

            out_parts = []
            if g is not None: out_parts.append(f"G{int(g)}")
            if x is not None: out_parts.append(f"X{x}")
            if y is not None: out_parts.append(f"Y{y}")
            if g in (2, 3):
                if r is not None: out_parts.append(f"R{r}")
                if fields['I'] is not None: out_parts.append(f"I{fields['I']}")
                if fields['J'] is not None: out_parts.append(f"J{fields['J']}")
            if z is not None: out_parts.append(f"Z{z}")
            if a is not None: out_parts.append(f"A{a}")
            if e is not None and g != 0: out_parts.append(f"{ext_axis}{round(e, 3)}")
            if f is not None: out_parts.append(f"F{f}")

            out_line = clamp_feedrate(' '.join(out_parts))

            if 'NO E' in original:
                out.write(clamp_feedrate(original))
                if original_e is not None:
                    if coord_type == 0: e -= chunk
                    net_extrude -= chunk
            else:
                out.write(out_line + "\n")

            if x is not None: x1 = x_val
            if y is not None: y1 = y_val
            if z is not None: z1 = z_val
            if a is not None: a1 = a_val
            if e is not None: e1 = e

        if config.auto_pressurize:
            out.write(f"\n; Auto-depressurize syringe\n")
            out.write("G91 ; Relative for depressurize\n")
            out.write(f"G1 {ext_axis}-{config.pressurize_amount} F{MAX_FEEDRATE}\n")
            if config.coordinate_mode == "G90": out.write("G90 ; Back to absolute\n")

        out.write("\n; --- End of Print ---\n")
        out.write("G91 ; Relative positioning\n")
        out.write("G1 Z30 F300 ; Lift nozzle\n")
        out.write("G90 ; Absolute positioning\n")
        if config.start_from_center:
            out.write("G1 X0 Y0 F300 ; Park at center\n")
        else:
            out.write("G1 X-50 Y-50 F300 ; Park at bottom-left\n")
        out.write("; -------------------\n")

    net_vol = net_extrude * math.pi * (config.z_syringe_dia / 2) ** 2 / 1000
    print(f"\nSUCCESS: Translation Complete")
    print(f"Extrusion: {round(net_extrude, 3)} mm  |  Volume: {round(net_vol, 3)} mL\n")

    return output_path

def load_file_menu():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(OUT_DIR) if f.lower().endswith(('.gcode', '.txt'))],
        key=lambda x: os.path.getmtime(os.path.join(OUT_DIR, x)),
        reverse=True,
    )
    if not files:
        print(f"ERROR: No files in '{OUT_DIR}/'. Translate a file first.")
        time.sleep(2)
        return None

    print(f"--- Translated Files in '{OUT_DIR}' ---")
    for i, fname in enumerate(files, 1):
        print(f"  [{i}] {fname}")
    print("  [0] Cancel")
    print()

    try:
        choice = int(input("Select file > "))
    except ValueError:
        return None

    if choice == 0 or choice > len(files):
        return None
    return os.path.join(OUT_DIR, files[choice - 1])

def print_file(printer, filepath, config):
    if not printer.is_connected:
        print("ERROR: Printer not connected.")
        time.sleep(1)
        return

    if not filepath:
        print("ERROR: No file loaded.")
        time.sleep(1)
        return

    if config.start_from_center:
        msg: str = "Move the bed to the CENTER position."
    else:
        msg: str = "Move the bed to the far BOTTOM-LEFT corner."

    print(f"\n======================================")
    print(f"ACTION REQUIRED: {msg}")
    print(f"======================================")
    ready = input("Is the bed in correct position? (y/n): ").strip().lower()

    if ready != 'y':
        print("Print cancelled.")
        time.sleep(1)
        return

    try:
        with open(filepath, "r") as fh:
            lines = fh.readlines()
    except Exception as e:
        print(f"ERROR: Error reading file: {e}")
        time.sleep(2)
        return

    print(f"\nPrinting: {os.path.basename(filepath)}")
    print("--> PRESS 'ENTER' TO PAUSE DURING PRINTING <--\n")

    printer.flush()
    aborted = False
    total_lines = len(lines)

    for i, line in enumerate(lines):
        if i % max(1, total_lines // 20) == 0:
            print(f"Printing... {int((i/total_lines)*100)}% complete.")

        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            continue

        cmd = stripped.split(';')[0].strip()
        if not cmd:
            continue

        printer.send_nowait(cmd)

        start = time.time()
        while True:
            # Check for user pause
            if _check_pause():
                printer.send_nowait("M220 S0")
                print("\n*** PRINT PAUSED ***")
                action = input("(r)esume or (s)top? [r]: ").strip().lower()

                if action == 's':
                    aborted = True
                    break

                printer.send_nowait("M220 S100")
                print("Resuming...")
                start = time.time()

            # Wait for response
            if printer.has_data:
                resp = printer.read_line()
                if resp and 'ok' in resp.lower():
                    break

            if time.time() - start > 60:
                print("Warning: no 'ok' in 60s, continuing.")
                break
            time.sleep(0.01)

        if aborted:
            break

    if not aborted:
        print("Finishing buffered moves...")
        try:
            printer.send("M400", timeout=60)
        except Exception:
            pass

    if aborted:
        try:
            printer.send_nowait("M410")
            time.sleep(0.5)
            printer.flush()
            printer.send_nowait("M220 S100")
            printer.send("G91", timeout=2)
            printer.send("G1 Z30 F300", timeout=10)
            printer.send("G90", timeout=2)
            if config.start_from_center:
                printer.send("G1 X0 Y0 F300", timeout=30)
            else:
                printer.send("G1 X-50 Y-50 F300", timeout=30)
        except Exception as e:
            print(f"Park error: {e}")
        print("Print STOPPED. Bed parked safely.")
    else:
        print("\nSUCCESS: Print complete!")

    time.sleep(2)

def manual_terminal(printer):
    if not printer.is_connected:
        print("ERROR: Printer not connected.")
        time.sleep(1)
        return

    clear_screen()
    display_header()
    print("--- Manual G-Code Terminal ---")
    print("Type commands directly. Speeds will auto-cap at F300.")
    print("Tip: G91 for relative, G90 for absolute.")
    print("Type 'q', 'quit', or 'exit' to leave.\n")

    printer.flush()

    while True:
        cmd = input("> ")
        if cmd.lower() in ('q', 'quit', 'exit'):
            break
        if not cmd.strip():
            continue

        cmd = clean_gcode(cmd).upper().strip()
        if (cmd.startswith("G0") or cmd.startswith("G1")) and "F" not in cmd:
            cmd += " F300"

        try:
            responses = printer.send(cmd, timeout=5)
            for r in responses:
                print(r)
        except serial.SerialException as e:
            print(f"ERROR: Serial error: {e}")
            break
        except RuntimeError as e:
            print(f"ERROR: {e}")
            break

def jog_mode(printer, config):
    if not printer.is_connected:
        print("ERROR: Printer not connected.")
        time.sleep(1)
        return

    clear_screen()
    display_header()
    print("--- Jog Control ---")
    print(f"Step Size  : {config.jog_distance} mm")
    print(f"Max Speed  : F{MAX_FEEDRATE}\n")
    print("  W / S : Y+ / Y-      A / D : X- / X+")
    print("  R / F : Z+ / Z-      T / G : Ext- / Ext+")
    print("  + / - : Adjust Step Size")
    print("  Q     : Quit\n")

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

            if key in ('+', '='):
                if config.jog_step_index < len(JOG_STEPS) - 1:
                    config.jog_step_index += 1
                sys.stdout.write(f"\rStep: {config.jog_distance} mm       ")
                sys.stdout.flush()
                continue
            if key == '-':
                if config.jog_step_index > 0:
                    config.jog_step_index -= 1
                sys.stdout.write(f"\rStep: {config.jog_distance} mm       ")
                sys.stdout.flush()
                continue

            d = config.jog_distance
            ea = config.extrusion_axis
            cmd = None
            if key == 'w': cmd = f"G1 Y{d} F{MAX_FEEDRATE}"
            elif key == 's': cmd = f"G1 Y-{d} F{MAX_FEEDRATE}"
            elif key == 'a': cmd = f"G1 X-{d} F{MAX_FEEDRATE}"
            elif key == 'd': cmd = f"G1 X{d} F{MAX_FEEDRATE}"
            elif key == 'r': cmd = f"G1 Z{d} F{MAX_FEEDRATE}"
            elif key == 'f': cmd = f"G1 Z-{d} F{MAX_FEEDRATE}"
            elif key == 't': cmd = f"G1 {ea}-{d} F{MAX_FEEDRATE}"
            elif key == 'g': cmd = f"G1 {ea}{d} F{MAX_FEEDRATE}"

            if cmd:
                try:
                    printer.send(cmd, timeout=2)
                except Exception:
                    pass

    finally:
        if not is_win:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            termios.tcflush(fd, termios.TCIFLUSH)
        try:
            printer.send("G90")
        except Exception:
            pass

# ============================================================
#  MAIN ENTRY
# ============================================================
def main():
    config = Config()
    printer = Bioprinter()
    loaded_file = None

    try:
        while True:
            clear_screen()
            display_header()

            if printer.is_connected:
                print(f"Printer Status : CONNECTED ({printer.port})")
            else:
                print(f"Printer Status : DISCONNECTED")

            file_disp = os.path.basename(loaded_file) if loaded_file else "None"
            print(f"Loaded File    : {file_disp}\n")

            print("--- Main Menu ---")
            if printer.is_connected:
                print("[0] RESET Printer Board")
            print("[1] Connect to Printer")
            print("[2] Translate G-Code")
            print("[3] Load Translated File")

            if printer.is_connected and loaded_file:
                print("[4] PRINT Loaded File")
            else:
                print("[4] PRINT (Requires connection & file)")

            if printer.is_connected:
                print("[5] Manual Terminal")
                print("[6] Jog Control")
            else:
                print("[5] Manual Terminal (Requires Connection)")
                print("[6] Jog Control (Requires Connection)")
            print("[7] Settings")
            print("[8] Exit\n")

            choice = input("Choose > ").strip()

            if choice == "0" and printer.is_connected:
                reset_menu(printer)
            elif choice == "1":
                connect_menu(printer, config)
            elif choice == "2":
                result = translate_gcode(config)
                if result:
                    req = input("\nLoad this file for printing? (y/n): ").strip().lower()
                    if req == 'y':
                        loaded_file = result
                        print(f"Loaded {os.path.basename(result)}")
                        time.sleep(1)
            elif choice == "3":
                result = load_file_menu()
                if result:
                    loaded_file = result
                    print(f"Loaded {os.path.basename(result)}")
                    time.sleep(1)
            elif choice == "4" and printer.is_connected and loaded_file:
                print_file(printer, loaded_file, config)
            elif choice == "5" and printer.is_connected:
                manual_terminal(printer)
            elif choice == "6" and printer.is_connected:
                jog_mode(printer, config)
            elif choice == "7":
                settings_menu(config)
            elif choice == "8":
                printer.disconnect()
                print("\nGoodbye!\n")
                break
    except KeyboardInterrupt:
        printer.disconnect()
        print("\n\nGoodbye!\n")

if __name__ == "__main__":
    main()
