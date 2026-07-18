#!/usr/bin/env python3
"""
rflord — RF Lord: Real-time RF spectrum monitor with drone detection and voice alerts.
Uses curses for proper terminal display.
Author: Ihor Kolodyuk
"""

import warnings
warnings.filterwarnings("ignore")
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import subprocess
import sys
import time
import math
import tempfile
import signal
import shutil
import glob
import curses

# Config
INTERVAL = 120
TTS_VOICE = "en-US-SteffanNeural"
VOICE_THRESHOLD = -15
ARTEMIS_DB = "/opt/artemis/Data/db.csv"
DECODED_DIR = "/home/ihorman/sdr_captures/rflord_decoded"
MAX_AGE_DAYS = 30

# Color pairs
CP_HEADER = 1
CP_SUS_RED = 2
CP_SUS_YEL = 3
CP_OK = 4
CP_DIM = 5
CP_SEP = 6

def run_cmd(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except:
        return ""

def detect_device():
    lsusb = run_cmd("lsusb")
    if "1d50:6018" in lsusb:
        import serial
        for port in ["/dev/ttyACM1", "/dev/ttyACM0"]:
            try:
                s = serial.Serial(port, 115200, timeout=2)
                time.sleep(0.5)
                s.write(b'restore\r\n')
                time.sleep(1.5)
                s.read(s.in_waiting or 500)
                s.write(b'hackrf\r\n')
                time.sleep(4)
                s.close()
                break
            except:
                pass
        lsusb = run_cmd("lsusb")
    if "1d50:6089" in lsusb:
        return "hackrf"
    if "0bda:2838" in lsusb:
        return "rtlsdr"
    return None

def hackrf_sweep(f_lo, f_hi, bw=2000000, n=3):
    cmd = f"/usr/bin/hackrf_sweep -f {f_lo}:{f_hi} -w {bw} -l 32 -g 40 -a 1 -N {n} 2>/dev/null | grep '^[0-9]'"
    return run_cmd(cmd, timeout=45)

def parse_sweep(output):
    signals = []
    for line in output.strip().split('\n'):
        if not line or not line[0].isdigit():
            continue
        parts = line.split(', ')
        if len(parts) < 7:
            continue
        try:
            freq_low = int(parts[2])
            freq_high = int(parts[3])
        except:
            continue
        db_vals = []
        for p in parts[6:]:
            try:
                db_vals.append(float(p.strip()))
            except:
                pass
        if db_vals:
            center = (freq_low + freq_high) / 2
            signals.append({
                'freq': center,
                'peak': max(db_vals),
                'avg': sum(db_vals) / len(db_vals),
                'std': math.sqrt(sum((x - sum(db_vals)/len(db_vals))**2 for x in db_vals) / len(db_vals)) if len(db_vals) > 1 else 0,
            })
    return signals

def get_band(f):
    bands = [
        (88, 108, "FM"), (108, 137, "AIR"), (144, 148, "2m"), (150, 174, "VHF"),
        (400, 470, "UHF"), (470, 608, "DTV"), (806, 960, "GSM"),
        (960, 1215, "L"), (1700, 2000, "3G"), (2300, 2700, "LTE"),
        (2400, 2500, "WiFi"), (5150, 5900, "5G"),
    ]
    for lo, hi, name in bands:
        if lo <= f <= hi:
            return name
    return "?"

def classify(f, power, std):
    wifi_ch = [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472]
    for ch in wifi_ch:
        if abs(f - ch) < 3:
            return "ok"
    if 2402 <= f <= 2480 and std > 3:
        return "ok"
    if 935 <= f <= 960 or 1805 <= f <= 1880:
        return "ok"
    if 88 <= f <= 108 or 174 <= f <= 230:
        return "ok"
    if 108 <= f <= 137 or 144 <= f <= 148 or 150 <= f <= 174:
        return "ok"
    if 1089 <= f <= 1091 or 1574 <= f <= 1576:
        return "ok"
    if 700 <= f <= 960 and std > 3:
        return "ok"
    if 1700 <= f <= 2000 or 2000 <= f <= 2200:
        return "ok"
    if 2300 <= f <= 2700:
        return "ok"
    if 400 <= f <= 510:
        return "ok"
    if 510 <= f <= 610:
        return "ok"
    # Known military/satellite signals
    if 225 <= f <= 400 and std > 3:
        return "ok"  # Link-11 with bursty data
    if 243 <= f <= 244:
        return "ok"  # Milstar
    if 264 <= f <= 266:
        return "ok"  # Gonets
    if 140 <= f <= 150 and std < 2:
        return "ok"  # Military
    if 150 <= f <= 174 and std < 2:
        return "ok"  # Military
    if power > -20:
        return "sus"
    return "ok"

def est_distance(freq_mhz, power_dbfs):
    rx_dbm = power_dbfs - 30
    if 88 <= freq_mhz <= 108: tx = 70
    elif 174 <= freq_mhz <= 230: tx = 60
    elif 800 <= freq_mhz <= 960: tx = 43
    elif 2400 <= freq_mhz <= 2500: tx = 20
    elif 5150 <= freq_mhz <= 5900: tx = 23
    else: tx = 30
    fspl = tx + 2 - rx_dbm
    fspl = max(20, min(160, fspl))
    d = 10 ** ((fspl - 32.44 - 20 * math.log10(max(freq_mhz, 1))) / 20)
    d = max(0.001, min(500, d))
    if d < 10:
        return f"{d*1000:.0f}m"
    else:
        return f"{d:.0f}km"

def speak(text):
    try:
        wav = tempfile.mktemp(suffix='.mp3', prefix='tts_')
        subprocess.run(["edge-tts", "--voice", TTS_VOICE, "--rate", "+10%",
                        "--text", text, "--write-media", wav],
                       capture_output=True, timeout=10)
        if os.path.exists(wav):
            subprocess.run(["paplay", wav], capture_output=True, timeout=15)
            os.unlink(wav)
    except:
        pass

def ensure_sink():
    try:
        r = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=3)
        if "auto_null" in r.stdout and "alsa_output" not in r.stdout:
            subprocess.run(["pactl", "load-module", "module-alsa-sink", "device=hw:0,0"],
                           capture_output=True, timeout=3)
            subprocess.run(["pactl", "set-default-sink", "alsa_output.hw:0,0"],
                           capture_output=True, timeout=3)
    except:
        pass

def load_artemis():
    db = []
    if not os.path.exists(ARTEMIS_DB):
        return db
    try:
        with open(ARTEMIS_DB, 'r') as f:
            for line in f:
                parts = line.strip().split('*')
                if len(parts) < 8:
                    continue
                try:
                    freq_low = int(parts[1]) if parts[1] else 0
                    freq_high = int(parts[2]) if parts[2] else 0
                except:
                    continue
                if freq_low > 0 and freq_high > 0:
                    db.append({
                        'name': parts[0].strip("'"),
                        'freq_low': freq_low,
                        'freq_high': freq_high,
                    })
    except:
        pass
    return db

def identify_signal(freq_mhz, artemis_db):
    freq_hz = freq_mhz * 1e6
    best = None
    best_width = float('inf')
    for entry in artemis_db:
        tol = max((entry['freq_high'] - entry['freq_low']) * 0.1, 2_000_000)
        if (entry['freq_low'] - tol) <= freq_hz <= (entry['freq_high'] + tol):
            width = entry['freq_high'] - entry['freq_low']
            if width < best_width:
                best_width = width
                best = entry
    if best:
        return best['name']
    return None

def get_signal_type(freq_mhz, bw, pmr, std):
    # Known real signals FIRST (before Display Port range)
    if 240 <= freq_mhz <= 242: return "DAB"
    elif 235 <= freq_mhz <= 238: return "DAB+"
    elif 390 <= freq_mhz <= 400: return "TETRA"
    elif 337 <= freq_mhz <= 362: return "Keyfob"
    elif 140 <= freq_mhz <= 150 and std < 2:
        return "Mil/Enc"
    elif 150 <= freq_mhz <= 174 and std < 2:
        return "Mil/Enc"
    elif 300 <= freq_mhz <= 330:
        return "Mil/Enc"
    # Real signals in Display Port range (230-285 MHz)
    elif 225 <= freq_mhz <= 400 and std > 3:
        return "Link-11"
    elif 243 <= freq_mhz <= 244:
        return "Milstar"
    elif 264 <= freq_mhz <= 266:
        return "Gonets"
    elif 174 <= freq_mhz <= 230:
        return "DAB+"
    # Display Port range (230-285 MHz) — only if NOT a known signal
    elif 230 <= freq_mhz <= 285:
        return "Display Port"
    elif 612 <= freq_mhz <= 700:
        if bw < 10000: return "USB-noise"
        else: return "USB-burst"
    elif 900 <= freq_mhz <= 928 and std < 2:
        return "CAM?"
    elif 1080 <= freq_mhz <= 1300 and std < 2:
        return "SPY-CAM"
    elif 2410 <= freq_mhz <= 2483 and std < 2 and bw and bw < 100000:
        return "CAM?"
    elif 5725 <= freq_mhz <= 5875 and std < 2:
        return "FPV?"
    elif 5150 <= freq_mhz <= 5900:
        return "WiFi/FPV"
    elif 2400 <= freq_mhz <= 2500:
        return "WiFi/BT"
    elif 1200 <= freq_mhz <= 1400 and std < 2:
        return "SPY-CAM"
    elif std < 2: return "CW"
    elif pmr > 8: return "Digital"
    elif pmr > 4: return "Bursty"
    else: return "Analog"

def ensure_decoded_dir():
    os.makedirs(os.path.join(DECODED_DIR, "audio"), exist_ok=True)
    os.makedirs(os.path.join(DECODED_DIR, "video"), exist_ok=True)

def cleanup_old_decoded():
    cutoff = time.time() - (MAX_AGE_DAYS * 86400)
    for subdir in ["audio", "video"]:
        for f in glob.glob(os.path.join(DECODED_DIR, subdir, "*")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.unlink(f)
            except:
                pass

def save_decoded_audio(freq_mhz, wav_path, sig_type=""):
    try:
        ensure_decoded_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        freq_label = f"{freq_mhz:.1f}".replace('.', 'p')
        name = f"{ts}_{freq_label}MHz_{sig_type}.wav"
        dest = os.path.join(DECODED_DIR, "audio", name)
        shutil.copy2(wav_path, dest)
        return dest
    except:
        return None

def play_voice_sample(freq_mhz):
    try:
        freq_hz = int(freq_mhz * 1e6)
        raw = tempfile.mktemp(suffix='.raw', prefix='voice_')
        wav = tempfile.mktemp(suffix='.wav', prefix='voice_')
        subprocess.run(["hackrf_transfer", "-r", raw, "-f", str(freq_hz),
                        "-s", "2000000", "-n", "4000000", "-l", "32", "-g", "40", "-a", "1"],
                       capture_output=True, timeout=10)
        if not os.path.exists(raw) or os.path.getsize(raw) < 1000:
            return
        import numpy as np
        data = np.fromfile(raw, dtype=np.int8)
        iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
        iq /= 128.0
        os.unlink(raw)
        if 88 <= freq_mhz <= 108:
            phase = np.unwrap(np.angle(iq))
            audio = np.diff(phase) * 2000000 / (2 * np.pi)
            alpha = 1.0 / (1.0 + 2000000 * 75e-6)
            for i in range(1, len(audio)):
                audio[i] = audio[i] * (1 - alpha) + audio[i-1] * alpha
        else:
            phase = np.unwrap(np.angle(iq))
            audio = np.diff(phase) * 2000000 / (2 * np.pi)
        audio = audio / (np.max(np.abs(audio)) + 1e-10) * 0.8
        import wave
        target_rate = 48000
        step = 2000000 / target_rate
        indices = np.arange(0, len(audio), step).astype(int)
        indices = indices[indices < len(audio)]
        audio_48k = audio[indices]
        audio_16 = (audio_48k * 32767).astype(np.int16)
        with wave.open(wav, 'w') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(target_rate)
            w.writeframes(audio_16.tobytes())
        sig_type = get_signal_type(freq_mhz, 0, 0, 0)
        save_decoded_audio(freq_mhz, wav, sig_type)
        ensure_sink()
        subprocess.run(["paplay", wav], capture_output=True, timeout=10)
        os.unlink(wav)
    except:
        pass

def try_voice_decode(freq_mhz):
    try:
        scripts = "/home/ihorman/.hermes/profiles/shared/skills/devops/scan-radio/scripts"
        cmd = f"python3 {scripts}/voice_decode.py scan {freq_mhz} --duration 3 2>&1"
        r = run_cmd(cmd, timeout=15)
        is_voice_band = (88 <= freq_mhz <= 108) or (108 <= freq_mhz <= 137) or (150 <= freq_mhz <= 174) or (400 <= freq_mhz <= 470)
        if is_voice_band:
            play_voice_sample(freq_mhz)
        if "DMR" in r: return "DMR digital voice"
        if "D-STAR" in r: return "D-STAR ham radio"
        if "NFM" in r and "Power" in r:
            if "Analog NFM" in r: return "FM voice radio"
        if "AM" in r and "Air band" in r: return "AM aviation radio"
        if "POCSAG" in r: return "POCSAG pager"
        if "DTMF" in r: return "DTMF tones"
        if "Morse" in r: return "Morse code"
    except:
        pass
    return None

def draw_table(stdscr, signals, start_time, known_freqs, alert_count, artemis_db):
    """Draw split-screen table: suspicious left, known right. NO SCROLL."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    
    suspicious = sorted([s for s in signals if classify(s['freq']/1e6, s['peak'], s['std']) == 'sus'],
                        key=lambda x: x['peak'], reverse=True)
    ok = sorted([s for s in signals if classify(s['freq']/1e6, s['peak'], s['std']) != 'sus'],
                key=lambda x: x['peak'], reverse=True)
    
    elapsed = int(time.time() - start_time)
    uh, um, us = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    
    mid = w // 2
    row = 0
    
    # Header
    header = f" RF LORD {time.strftime('%H:%M:%S')} │ Up {uh:02d}:{um:02d}:{us:02d} │ Alerts {alert_count} │ Tracked {len(known_freqs)} │ Sig {len(signals)} │ Ihor Kolodyuk"
    try:
        stdscr.addstr(row, 0, header[:w-1], curses.color_pair(CP_HEADER) | curses.A_BOLD)
    except: pass
    row += 1
    
    # Column titles
    try:
        stdscr.addstr(row, 0, f" {'SUSPICIOUS':^{mid-2}}"[:mid-1], curses.color_pair(CP_SUS_RED) | curses.A_BOLD)
        stdscr.addstr(row, mid, f" {'KNOWN SIGNALS':^{w-mid-2}}"[:w-mid-1], curses.color_pair(CP_OK) | curses.A_BOLD)
    except: pass
    row += 1
    
    # Sub-headers
    try:
        stdscr.addstr(row, 0, " Freq    Pwr   Std  Dist Type         "[:mid-1], curses.color_pair(CP_DIM))
        stdscr.addstr(row, mid, " Freq    Pwr   Std  Dist Bnd  Identification    "[:w-mid-1], curses.color_pair(CP_DIM))
    except: pass
    row += 1
    
    # Separator
    try:
        stdscr.addstr(row, 0, (" " + "─" * (mid-2))[:mid-1], curses.color_pair(CP_SEP))
        stdscr.addstr(row, mid, (" " + "─" * (w-mid-2))[:w-mid-1], curses.color_pair(CP_SEP))
    except: pass
    row += 1
    
    # Available rows for data
    avail = h - row - 2  # 2 for footer
    
    for i in range(avail):
        if row >= h - 2: break
        
        # Left — suspicious
        if i < len(suspicious):
            s = suspicious[i]
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            sig_type = get_signal_type(f, 0, 0, s['std'])
            cp = CP_SUS_RED if i < 3 else CP_SUS_YEL
            line = f" {f:>6.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {sig_type:<13}"
            try:
                stdscr.addstr(row, 0, line[:mid-1], curses.color_pair(cp))
            except: pass
        
        # Right — known
        if i < len(ok):
            s = ok[i]
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            art = identify_signal(f, artemis_db) if artemis_db else None
            art_str = (art[:18] if art else "")
            line = f" {f:>6.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {band:>4} {art_str}"
            try:
                stdscr.addstr(row, mid, line[:w-mid-1], curses.color_pair(CP_OK))
            except: pass
        
        row += 1
    
    # Footer
    try:
        stdscr.addstr(row, 0, (" " + "─" * (mid-2))[:mid-1], curses.color_pair(CP_SEP))
        stdscr.addstr(row, mid, (" " + "─" * (w-mid-2))[:w-mid-1], curses.color_pair(CP_SEP))
    except: pass
    row += 1
    
    extra = ""
    if len(suspicious) > avail: extra += f" +{len(suspicious)-avail} sus"
    if len(ok) > avail: extra += f" +{len(ok)-avail} ok"
    try:
        stdscr.addstr(row, 0, f" Ctrl+C{extra}"[:w-1], curses.color_pair(CP_DIM))
    except: pass
    
    stdscr.refresh()

def main_curses(stdscr):
    global INTERVAL, VOICE_THRESHOLD
    
    # Setup curses
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(CP_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_SUS_RED, curses.COLOR_RED, -1)
    curses.init_pair(CP_SUS_YEL, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_OK, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(CP_SEP, curses.COLOR_WHITE, -1)
    
    stdscr.nodelay(False)
    stdscr.timeout(-1)
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--interval" and i + 2 <= len(sys.argv):
            INTERVAL = int(sys.argv[i + 2])
        if arg == "--threshold" and i + 2 <= len(sys.argv):
            VOICE_THRESHOLD = int(sys.argv[i + 2])
    
    device = detect_device()
    if not device:
        print("No SDR device found.")
        sys.exit(1)
    
    ensure_sink()
    artemis_db = load_artemis()
    
    bands = [
        (88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3),
        (1000, 1700, 2000000, 3), (1700, 2500, 1000000, 3), (2500, 3500, 1000000, 3),
        (5150, 5900, 500000, 3),
    ]
    
    scan_num = 0
    known_freqs = set()
    alert_count = 0
    start_time = time.time()
    
    while True:
        scan_num += 1
        
        if device == "hackrf":
            subprocess.run(["sudo", "usbreset", "1d50:6089"],
                           capture_output=True, timeout=5)
            time.sleep(2)
        
        all_signals = []
        for f_lo, f_hi, bw, n in bands:
            output = hackrf_sweep(f_lo, f_hi, bw, n)
            all_signals.extend(parse_sweep(output))
        
        seen = {}
        unique = []
        for s in all_signals:
            key = round(s['freq'] / 1e6)
            if key not in seen or s['peak'] > seen[key]['peak']:
                seen[key] = s
        unique = list(seen.values())
        
        new_suspicious = []
        for s in unique:
            f = s['freq'] / 1e6
            if classify(f, s['peak'], s['std']) == "sus":
                if round(f) not in known_freqs:
                    known_freqs.add(round(f))
                    new_suspicious.append(s)
                    alert_count += 1
        
        draw_table(stdscr, unique, start_time, known_freqs, alert_count, artemis_db)
        
        if scan_num % 10 == 0:
            cleanup_old_decoded()
        
        # Voice alert
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            above_threshold = [s for s in new_suspicious if s['peak'] > VOICE_THRESHOLD]
            
            if above_threshold:
                announcements = []
                for s in above_threshold[:4]:
                    f = s['freq'] / 1e6
                    dist = est_distance(f, s['peak'])
                    sig_type = get_signal_type(f, 0, 0, s['std'])
                    artemis_name = identify_signal(f, artemis_db)
                    if artemis_name:
                        announcements.append(f"{f:.0f} megahertz, identified as {artemis_name}, about {dist}")
                    else:
                        announcements.append(f"{f:.0f} megahertz, {sig_type}, about {dist}")
                
                voice_result = None
                for s in above_threshold:
                    if s['std'] < 6:
                        voice_result = try_voice_decode(s['freq'] / 1e6)
                        break
                
                for s in above_threshold:
                    f = s['freq'] / 1e6
                    sig_type = get_signal_type(f, 0, 0, s['std'])
                    if sig_type == "Analog" and s['std'] < 4:
                        play_voice_sample(f)
                        if not voice_result:
                            voice_result = "analog voice sample, saved to decoded folder"
                        break
                
                count = len(above_threshold)
                if count == 1:
                    msg = f"Alert. New signal at {announcements[0]}."
                elif count == 2:
                    msg = f"Alert. Two new signals. First at {announcements[0]}. Second at {announcements[1]}."
                else:
                    msg = f"Alert. {count} new signals above threshold. Strongest at {announcements[0]}."
                    if count > 2:
                        msg += f" Also at {announcements[1]}."
                
                if voice_result:
                    msg += f" Detected {voice_result}."
                
                speak(msg)
            else:
                s0 = new_suspicious[0]
                f0 = s0['freq'] / 1e6
                dist = est_distance(f0, s0['peak'])
                speak(f"{len(new_suspicious)} new weak signals. Strongest at {f0:.0f} megahertz, below threshold.")
        
        time.sleep(INTERVAL)

def main():
    # Try curses first (proper terminal), fallback to ANSI
    try:
        if sys.stdout.isatty():
            curses.wrapper(main_curses)
        else:
            # Non-TTY: use ANSI mode
            main_ansi()
    except Exception:
        main_ansi()

def main_ansi():
    """ANSI fallback mode for non-TTY or when curses fails."""
    global INTERVAL, VOICE_THRESHOLD
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--interval" and i + 2 <= len(sys.argv):
            INTERVAL = int(sys.argv[i + 2])
        if arg == "--threshold" and i + 2 <= len(sys.argv):
            VOICE_THRESHOLD = int(sys.argv[i + 2])
    
    device = detect_device()
    if not device:
        print("No SDR device found.")
        sys.exit(1)
    
    ensure_sink()
    artemis_db = load_artemis()
    
    bands = [
        (88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3),
        (1000, 1700, 2000000, 3), (1700, 2500, 1000000, 3), (2500, 3500, 1000000, 3),
        (5150, 5900, 500000, 3),
    ]
    
    scan_num = 0
    known_freqs = set()
    alert_count = 0
    start_time = time.time()
    
    # ANSI colors
    R = "\033[1;31m"; Y = "\033[1;33m"; G = "\033[1;32m"; C = "\033[1;36m"; D = "\033[2m"; N = "\033[0m"
    
    signal.signal(signal.SIGINT, lambda *_: (sys.stdout.write("\033[?25h\033[H\033[J"), print(f"\n{C}Stopped.{N}"), sys.exit(0)))
    print("\033[2J\033[H\033[?25l", end="")
    
    while True:
        scan_num += 1
        
        if device == "hackrf":
            subprocess.run(["sudo", "usbreset", "1d50:6089"], capture_output=True, timeout=5)
            time.sleep(2)
        
        all_signals = []
        for f_lo, f_hi, bw, n in bands:
            output = hackrf_sweep(f_lo, f_hi, bw, n)
            all_signals.extend(parse_sweep(output))
        
        seen = {}
        unique = []
        for s in all_signals:
            key = round(s['freq'] / 1e6)
            if key not in seen or s['peak'] > seen[key]['peak']:
                seen[key] = s
        unique = list(seen.values())
        
        suspicious = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) == 'sus'],
                            key=lambda x: x['peak'], reverse=True)
        ok = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) != 'sus'],
                    key=lambda x: x['peak'], reverse=True)
        
        new_suspicious = []
        for s in unique:
            f = s['freq'] / 1e6
            if classify(f, s['peak'], s['std']) == "sus":
                if round(f) not in known_freqs:
                    known_freqs.add(round(f))
                    new_suspicious.append(s)
                    alert_count += 1
        
        elapsed = int(time.time() - start_time)
        uh, um, us = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        
        sys.stdout.write("\033[H")
        
        # Header
        print(f"{C} RF LORD{N} {time.strftime('%H:%M:%S')} │ Up {uh:02d}:{um:02d}:{us:02d} │ "
              f"{Y}Alerts {alert_count}{N} │ Tracked {len(known_freqs)} │ Sig {len(unique)} │ {D}Ihor Kolodyuk{N}")
        
        # Column titles
        mid = 42
        print(f"{R} {'SUSPICIOUS':^{mid-2}}{N}{G} {'KNOWN SIGNALS':^{38}}{N}")
        
        # Sub-headers
        print(f"{D} Freq    Pwr   Std  Dist Type         {N}{D} Freq    Pwr   Std  Dist Bnd  Identification    {N}")
        
        # Separator
        print(f"{D} {'─'*(mid-2)} {'─'*38}{N}")
        
        # Data rows — STRICT limit to 24 lines total
        # Total lines: header(1) + titles(1) + sub(1) + sep(1) + data + footer(1) = 5 + data
        # For 24-line terminal: data = 19 rows max
        max_rows = 19
        for i in range(max_rows):
            left = ""
            right = ""
            
            if i < len(suspicious):
                s = suspicious[i]
                f = s['freq'] / 1e6
                dist = est_distance(f, s['peak'])
                sig_type = get_signal_type(f, 0, 0, s['std'])
                c = R if i < 3 else Y
                left = f"{c}{f:>6.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {sig_type:<13}{N}"
            
            if i < len(ok):
                s = ok[i]
                f = s['freq'] / 1e6
                dist = est_distance(f, s['peak'])
                band = get_band(f)
                art = identify_signal(f, artemis_db) if artemis_db else None
                art_str = art[:18] if art else ""
                right = f"{G}{f:>6.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {band:>4} {art_str}{N}"
            
            if left or right:
                left_pad = f" {left:<{mid - 1 + len(R) + len(N)}}"
                print(f"{left_pad} {right}")
        
        # Footer
        extra = ""
        if len(suspicious) > max_rows: extra += f" +{len(suspicious)-max_rows} sus"
        if len(ok) > max_rows: extra += f" +{len(ok)-max_rows} ok"
        print(f"{D} {'─'*(mid-2)} {'─'*38}{N}")
        print(f"{D} Ctrl+C{extra}{N}")
        sys.stdout.write("\033[J")
        sys.stdout.flush()
        
        if scan_num % 10 == 0:
            cleanup_old_decoded()
        
        # Voice alert (same as curses version)
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            above_threshold = [s for s in new_suspicious if s['peak'] > VOICE_THRESHOLD]
            if above_threshold:
                announcements = []
                for s in above_threshold[:4]:
                    f = s['freq'] / 1e6
                    dist = est_distance(f, s['peak'])
                    sig_type = get_signal_type(f, 0, 0, s['std'])
                    artemis_name = identify_signal(f, artemis_db)
                    if artemis_name:
                        announcements.append(f"{f:.0f} megahertz, identified as {artemis_name}, about {dist}")
                    else:
                        announcements.append(f"{f:.0f} megahertz, {sig_type}, about {dist}")
                voice_result = None
                for s in above_threshold:
                    if s['std'] < 6:
                        voice_result = try_voice_decode(s['freq'] / 1e6)
                        break
                for s in above_threshold:
                    f = s['freq'] / 1e6
                    sig_type = get_signal_type(f, 0, 0, s['std'])
                    if sig_type == "Analog" and s['std'] < 4:
                        play_voice_sample(f)
                        if not voice_result:
                            voice_result = "analog voice sample, saved to decoded folder"
                        break
                count = len(above_threshold)
                if count == 1:
                    msg = f"Alert. New signal at {announcements[0]}."
                elif count == 2:
                    msg = f"Alert. Two new signals. First at {announcements[0]}. Second at {announcements[1]}."
                else:
                    msg = f"Alert. {count} new signals above threshold. Strongest at {announcements[0]}."
                    if count > 2:
                        msg += f" Also at {announcements[1]}."
                if voice_result:
                    msg += f" Detected {voice_result}."
                speak(msg)
            else:
                s0 = new_suspicious[0]
                f0 = s0['freq'] / 1e6
                dist = est_distance(f0, s0['peak'])
                speak(f"{len(new_suspicious)} new weak signals. Strongest at {f0:.0f} megahertz, below threshold.")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
