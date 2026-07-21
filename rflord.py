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
import numpy as np
import tempfile
import signal
import shutil
import glob
import curses
import select
from spy_db import identify_spy_device, get_signal_icon, get_threat_icon, pad_icon

# Config
VERSION = "v0.5.65"
INTERVAL = 30
TTS_VOICE = "en-US-SteffanNeural"
HAL_EFFECT = os.path.expanduser("~/.local/bin/hal-effect.sh")
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
CP_FRESH = 7
CP_DANGER = 8

def run_cmd(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except:
        return ""

def detect_device():
    lsusb = run_cmd("lsusb")
    if "1d50:6018" in lsusb:
        print("PortaPack detected, switching to HackRF mode...", flush=True)
        import serial
        switched = False
        for port in ["/dev/ttyACM0", "/dev/ttyACM1"]:
            try:
                print(f"  Trying {port}...", flush=True)
                s = serial.Serial(port, 115200, timeout=2)
                time.sleep(0.5)
                s.write(b'restore\r\n')
                time.sleep(1.5)
                s.read(s.in_waiting or 500)
                s.write(b'hackrf\r\n')
                time.sleep(3)
                s.read(s.in_waiting or 500)
                s.close()
                switched = True
                print(f"  Sent mode switch on {port}", flush=True)
                break
            except Exception as e:
                print(f"  {port} failed: {e}", flush=True)
        # Wait for USB re-enumeration (no usbreset — device does it itself)
        time.sleep(5)
        lsusb = run_cmd("lsusb")
        if "1d50:6089" in lsusb:
            print("  Switched to HackRF mode!", flush=True)
        elif switched:
            print("  Mode switch sent but HackRF not detected, retrying...", flush=True)
            time.sleep(5)
            lsusb = run_cmd("lsusb")
    if "1d50:6089" in lsusb:
        return "hackrf"
    if "0bda:2838" in lsusb:
        return "rtlsdr"
    print(f"SDR not found. USB devices: {lsusb[:200]}", flush=True)
    return None

def hackrf_sweep(f_lo, f_hi, bw=2000000, n=3):
    cmd = f"/usr/bin/hackrf_sweep -f {f_lo}:{f_hi} -w {bw} -l 32 -g 40 -a 1 -N {n} 2>/dev/null | grep '^[0-9]'"
    return run_cmd(cmd, timeout=45)

def rtlsdr_sweep(f_lo, f_hi, gain=40, n=1):
    """RTL-SDR sweep using rtl_power. f_lo/f_hi in MHz (same as hackrf_sweep)."""
    cmd = f"/usr/local/bin/rtl_power -f {f_lo}M:{f_hi}M:2.4M -g {gain} -e {n*5}s 2>/dev/null | grep '^[0-9]'"
    return run_cmd(cmd, timeout=60)

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
    # Known normal signals
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
    
    # DVB-T2 band (470-790 MHz): narrowband = DANGER (possible camera)
    if 470 <= f <= 790 and std < 2:
        return "danger"
    # Wideband DVB-T2 is ok
    if 470 <= f <= 790 and std > 3:
        return "ok"
    
    if 400 <= f <= 510:
        return "ok"
    if 510 <= f <= 610:
        return "ok"
    
    # SUSPICIOUS — military, spy, FPV, unknown transmitters
    if 255 <= f <= 267:  # Link-11 UHF, Gonets
        return "sus"
    if 270 <= f <= 285:  # Link-11 UHF
        return "sus"
    if 243 <= f <= 244:  # Milstar
        return "sus"
    if 140 <= f <= 150:  # Military CW
        return "sus"
    if 300 <= f <= 330:  # Military
        return "sus"
    if 900 <= f <= 928 and std < 2:  # Possible hidden camera
        return "sus"
    if 1080 <= f <= 1300 and std < 2:  # Spy camera
        return "sus"
    if 1200 <= f <= 1400 and std < 2:  # Spy camera
        return "sus"
    if 5725 <= f <= 5875 and std < 2:  # FPV video
        return "sus"
    if 2410 <= f <= 2483 and std < 2 and power > -25:  # Possible camera
        return "sus"
    
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
    meters = d * 1000
    if meters < 1000:
        return f"{meters:.0f}m"
    elif meters < 10000:
        return f"{meters/1000:.1f}km"
    else:
        return f"{meters/1000:.0f}km"

def est_distance_m(freq_mhz, power_dbfs):
    """Return distance in meters as a number (for sorting)."""
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
    return max(1, min(500000, d * 1000))

def speak_distance(dist_str):
    """Convert distance string to spoken text: '284m' -> '284 meters'."""
    if dist_str.endswith('km'):
        return dist_str.replace('km', ' kilometers')
    elif dist_str.endswith('m'):
        return dist_str.replace('m', ' meters')
    return dist_str

def estimate_noise_floor(signals):
    """Estimate noise floor using 10th percentile of signal powers.
    From sec0ps/rf_surveillance — dynamic noise floor adapts to environment."""
    if not signals:
        return -70  # Default
    powers = [s['peak'] for s in signals]
    return float(np.percentile(powers, 10)) if len(powers) > 5 else -70

def detect_active_probes(signals, noise_floor):
    """Detect strong brief signals that might be direction-finding probes.
    From sec0ps/rf_surveillance — threshold: >-20 dBm, >30 dB above noise floor."""
    probes = []
    threshold = max(-20, noise_floor + 30)  # -20 dBm OR 30 dB above noise
    for s in signals:
        if s['peak'] > threshold:
            probes.append(s)
    return probes

# Legitimate bands to skip for probe detection (reduce false positives)
LEGITIMATE_BANDS = [
    (88, 108),    # FM Broadcast
    (118, 137),   # Aviation
    (162, 174),   # Weather/Emergency
    (470, 890),   # TV/Cellular
]

def in_legitimate_band(freq_mhz):
    """Check if frequency is in a known legitimate band."""
    for lo, hi in LEGITIMATE_BANDS:
        if lo <= freq_mhz <= hi:
            return True
    return False

def speak(text):
    """Speak text via edge-tts with HAL 9000 effect. No timeout — let it play fully."""
    try:
        raw = tempfile.mktemp(suffix='.mp3', prefix='tts_')
        out = tempfile.mktemp(suffix='.wav', prefix='hal_')
        subprocess.run(["edge-tts", "--voice", TTS_VOICE, "--rate=-15%",
                        "--text", text, "--write-media", raw],
                       capture_output=True, timeout=60)
        if os.path.exists(raw):
            subprocess.run([HAL_EFFECT, raw, out], capture_output=True, timeout=30)
            os.unlink(raw)
            if os.path.exists(out):
                subprocess.run(["paplay", out], capture_output=True, timeout=120)
                os.unlink(out)
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
                        'modulation': parts[3] if len(parts) > 3 else '',
                        'bandwidth': parts[4] if len(parts) > 4 else '',
                        'country': parts[6] if len(parts) > 6 else '',
                        'description': parts[8][:100] if len(parts) > 8 else '',
                    })
    except:
        pass
    return db

def identify_signal(freq_mhz, artemis_db):
    """Return best Artemis match. Returns dict with name, description, etc."""
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
    # Override overly broad entries
    if best and 'toyota' in best.get('name', '').lower():
        return {'name': 'Link-11 Personal Beacon', 'description': 'Link-11 Personal Beacon'}
    return best

def get_signal_type(freq_mhz, bw, pmr, std, artemis_db=None):
    """Classify signal type. Check Artemis first, then hardcoded rules."""
    # Check Artemis database FIRST
    if artemis_db:
        art_entry = identify_signal(freq_mhz, artemis_db)
        if art_entry:
            return art_entry['name'][:18]
    
    # Known real signals
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
    elif 225 <= freq_mhz <= 400 and std > 3:
        return "Link-11"
    elif 243 <= freq_mhz <= 244:
        return "Milstar"
    elif 264 <= freq_mhz <= 266:
        return "Gonets"
    elif 174 <= freq_mhz <= 230:
        return "DAB+"
    elif 230 <= freq_mhz <= 285:
        return "Display Port"
    elif 470 <= freq_mhz <= 790:
        if std > 3:
            return "DVB-T2"
        elif std < 2:
            return "CAM-DTV?"  # Narrowband in TV band = possible camera
        else:
            return "DVB-T2"
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
        sig_type = get_signal_type(freq_mhz, 0, 0, 0, None)
        log.info(f"DECODED: {freq_mhz:.1f} MHz, type={sig_type}")
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

def signal_priority(freq_mhz, std):
    """Lower number = higher priority. Military/spy/FPV get priority."""
    f = freq_mhz
    # Priority 0: Military/encrypted — specific frequencies only
    if 140 <= f <= 150: return 0   # Kiwi, military CW
    if 243 <= f <= 244: return 0   # Milstar
    if 255 <= f <= 267: return 0   # Link-11 UHF, Gonets
    if 270 <= f <= 285: return 0   # Link-11 UHF
    if 300 <= f <= 330: return 0   # Military UHF
    if 380 <= f <= 400: return 0   # Tetrapol, TETRA
    # Priority 1: Spy cameras / FPV
    if 900 <= f <= 928 and std < 2: return 1
    if 1080 <= f <= 1300 and std < 2: return 1
    if 1200 <= f <= 1400 and std < 2: return 1
    if 5725 <= f <= 5875 and std < 2: return 1
    if 2410 <= f <= 2483 and std < 2: return 1
    # Priority 2: Other suspicious (USB noise, Display Port, etc.)
    return 2

def draw_splash(stdscr, device, status_lines=None):
    """Show loading splash with version info and status."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    
    lines = [
        "",
        "  ██████╗  ███████╗██╗      ██████╗ ██████╗ ██████╗ ",
        "  ██╔══██╗██╔════╝██║     ██╔═══██╗██╔══██╗██╔══██╗",
        "  ██████╔╝█████╗  ██║     ██║   ██║██████╔╝██║  ██║",
        "  ██╔══██╗██╔══╝  ██║     ██║   ██║██╔══██╗██║  ██║",
        "  ██║  ██║██║    ███████╗╚██████╔╝██║  ██║██████╔╝",
        "  ╚═╝  ╚═╝╚═╝    ╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═════╝ ",
        "",
        f"  RF SPECTRUM MONITOR  {VERSION}",
        f"  Author: Ihor Kolodyuk",
        "",
        f"  Device: {device.upper() if device else 'NOT FOUND'}",
    ]
    
    if status_lines:
        lines.append("")
        lines.extend(status_lines)
    
    lines.append("")
    lines.append("github.com/ihorman/rflord")
    
    start_row = max(0, (h - len(lines)) // 2)
    
    for i, line in enumerate(lines):
        row = start_row + i
        if row >= h - 1:
            break
        try:
            if "████" in line:
                color = CP_SUS_RED
                stdscr.addstr(row, max(0, (w - len(line)) // 2), line, curses.color_pair(color) | curses.A_BOLD)
            elif ": OK" in line:
                color = CP_OK
                col = max(0, (w - len(line)) // 2)
                stdscr.addstr(row, col, line[:w-1-col], curses.color_pair(color))
            elif "in progress" in line:
                color = CP_SUS_RED
                col = max(0, (w - len(line)) // 2)
                stdscr.addstr(row, col, line[:w-1-col], curses.color_pair(color) | curses.A_BOLD)
            elif "SPECTRUM" in line:
                color = CP_HEADER
                col = max(0, (w - len(line)) // 2)
                stdscr.addstr(row, col, line[:w-1-col], curses.color_pair(color) | curses.A_BOLD)
            else:
                color = CP_DIM
                col = max(0, (w - len(line)) // 2)
                stdscr.addstr(row, col, line[:w-1-col], curses.color_pair(color))
        except:
            pass
    
    stdscr.clrtobot()
    stdscr.refresh()

def draw_table(stdscr, signals, start_time, last_seen, alert_count, artemis_db, known_freqs=None, voice_enabled=True):
    """Draw split-screen table: suspicious left, known right. NO SCROLL."""
    if known_freqs is None:
        known_freqs = {}
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    
    suspicious = sorted([s for s in signals if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")],
                        key=lambda x: (signal_priority(x["freq"]/1e6, x["std"]), est_distance_m(x["freq"]/1e6, x["peak"]), -x["peak"]))


    ok = sorted([s for s in signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger')],
                key=lambda x: x['peak'], reverse=True)
    
    elapsed = int(time.time() - start_time)
    uh, um, us = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    
    mid = int(w * 0.6)
    row = 0
    
    # Header
    header = f" RfLord {VERSION} {time.strftime('%H:%M:%S')} │ Up {uh:02d}:{um:02d}:{us:02d} │ Alerts {alert_count} │ Tracked {len(known_freqs)} │ Sig {len(signals)} │ Author: Ihor Kolodyuk"
    try:
        stdscr.addstr(row, 0, (header[:w-1]).ljust(w-1), curses.color_pair(CP_HEADER) | curses.A_BOLD)
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
        stdscr.addstr(row, 0, "   Freq    Pwr   Std   Dist Type               Last Seen Remark"[:mid-1], curses.color_pair(CP_DIM))
        rhdr = f" {'Freq':>6} {'Pwr':>5} {'Std':>4} {'Dist':>5} {'Bnd':>4} {'Identification':<15}"
        stdscr.addstr(row, mid, rhdr[:w-mid-1], curses.color_pair(CP_DIM))
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
            sig_type = get_signal_type(f, 0, 0, s["std"], artemis_db)
            icon = pad_icon(get_signal_icon(sig_type, f, s["std"]))
            # Remark: prefer Artemis identification over spy_db
            # Only use spy_db if signal type is unknown/suspicious
            known_types = {"DAB", "DAB+", "TETRA", "Keyfob", "GSM", "WiFi/BT", "WiFi/FPV",
                           "Link-11", "Milstar", "Gonets", "Display Port", "USB-noise",
                           "USB-burst", "CDMA2000", "3G WCDMA", "LTE", "FM", "AIR"}
            art = identify_signal(f, artemis_db) if artemis_db else None
            if art:
                remark = art.get('description', '') or art.get('name', '')
            elif sig_type not in known_types:
                # Signal type unknown — check spy_db
                spy_name, spy_icon, threat = identify_spy_device(f, s["std"])
                if spy_name:
                    log.critical(f"SPY: {spy_name} at {f:.1f} MHz")
                    remark = spy_name
                else:
                    remark = ""
            else:
                remark = ""
            # Fixed fields: icon(2)+sp+freq(5)+sp+pwr(6)+sp+std(5)+sp+dist(5)+sp+type(18)+sp+ago(5)+sp = 53 visible cells
            remark_w = max(12, mid - 55)
            remark = remark[:remark_w]
            # Red if within 1000m, yellow otherwise; danger signals get special color
            dist_m = s['peak']  # we'll calculate actual meters
            try:
                d_val = float(dist.rstrip('mk'))
                d_m = d_val * 1000 if dist.endswith('km') else d_val
            except:
                d_m = 9999
            cls = classify(f, s['peak'], s['std'])
            if cls == "danger":
                cp = CP_DANGER  # Red on yellow for DVB-T2 band narrowband
            elif d_m < 1000:
                cp = CP_SUS_RED
            else:
                cp = CP_SUS_YEL
            seen_time = last_seen.get(round(f), time.time())
            ago = time_ago(seen_time)
            # Fresh detection blink: first seen < 30s ago
            first_time = known_freqs.get(round(f), time.time())
            age = time.time() - first_time
            is_fresh = age < 30
            blink_on = is_fresh and int(time.time() * 2) % 2 == 0  # 0.5s on, 0.5s off
            line = f"{icon} {f:>5.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {sig_type:<18} {ago:>5} {remark}"
            try:
                if is_fresh:
                    attr = curses.color_pair(CP_SUS_RED if blink_on else CP_SUS_YEL) | curses.A_BOLD
                else:
                    attr = curses.color_pair(cp) | curses.A_BOLD
                stdscr.addstr(row, 0, line[:mid-1], attr)
            except: pass
        
        # Right — known
        if i < len(ok):
            s = ok[i]
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            art = identify_signal(f, artemis_db) if artemis_db else None
            # Right table: freq(6)+sp+pwr(5)+sp+std(4)+sp+dist(5)+sp+band(4)+sp = 27 fixed
            id_w = max(15, (w - mid) - 28)
            art_str = (art['name'][:id_w] if art else "")
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
        voice_str = "ON" if voice_enabled else "OFF"
        keys = f" q:Quit  r:Rescan  v:Voice({voice_str})  m:Mute  +/-:Interval({INTERVAL}s){extra}"
        stdscr.addstr(row, 0, (keys[:w-1]).ljust(w-1), curses.color_pair(CP_DIM))
    except: pass
    
    # Clear any remaining rows below (leftover from previous draw)
    stdscr.clrtobot()
    
    stdscr.refresh()

def main_curses(stdscr, device):
    global INTERVAL, VOICE_THRESHOLD
    
    # Setup curses
    curses.cbreak()
    curses.noecho()
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    stdscr.keypad(True)  # Enable arrow/function keys
    # Bright colors — use bold attribute for maximum brightness
    curses.init_pair(CP_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_SUS_RED, curses.COLOR_RED, -1)
    curses.init_pair(CP_SUS_YEL, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_OK, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(CP_SEP, curses.COLOR_WHITE, -1)
    curses.init_pair(CP_FRESH, curses.COLOR_WHITE, -1)  # Blink effect for fresh detections
    curses.init_pair(CP_DANGER, curses.COLOR_RED, curses.COLOR_YELLOW)  # Danger: red on yellow
    
    stdscr.nodelay(False)
    stdscr.timeout(-1)
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--interval" and i + 2 <= len(sys.argv):
            INTERVAL = int(sys.argv[i + 2])
        if arg == "--threshold" and i + 2 <= len(sys.argv):
            VOICE_THRESHOLD = int(sys.argv[i + 2])
    
    status = ["SDR Initialized: OK"]
    draw_splash(stdscr, device, status)
    
    ensure_sink()
    artemis_db = load_artemis()
    
    db_count = len(artemis_db) if artemis_db else 0
    try:
        from spy_db import SPY_DEVICES
        spy_count = len(SPY_DEVICES)
    except: spy_count = 0
    try:
        from drone_rf_db import DRONE_SIGNATURES
        drone_count = len(DRONE_SIGNATURES)
    except: drone_count = 0
    total = db_count + spy_count + drone_count
    status.append(f"Signatures databases loaded: OK ({total} total: {db_count} Artemis, {spy_count} spy, {drone_count} drone)")
    status.append("Initial scan & analysis: in progress")
    draw_splash(stdscr, device, status)
    
    bands = [
        (88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3),
        (1000, 1700, 2000000, 3), (1700, 2500, 1000000, 3), (2500, 3500, 1000000, 3),
        (5150, 5900, 500000, 3),
    ]
    
    scan_num = 0
    known_freqs = {}   # freq -> first seen time (for new signal detection)
    last_seen = {}     # freq -> last seen time (for "Last Seen" display)
    alert_count = 0
    voice_enabled = True
    start_time = time.time()
    
    # Reset HackRF once at startup
    if device == "hackrf":
        subprocess.run(["sudo", "usbreset", "1d50:6089"], capture_output=True, timeout=5)
        time.sleep(3)

    first_scan_done = False
    while True:
        scan_num += 1
        log.info(f"=== Scan #{scan_num} started ===")
        
        
        all_signals = []
        h, w = stdscr.getmaxyx()
        for bi, (f_lo, f_hi, bw, n) in enumerate(bands):
            # Show scanning progress
            try:
                status_line = f" Scanning {f_lo}-{f_hi} MHz ({bi+1}/{len(bands)})... "
                stdscr.addstr(0, 0, status_line.ljust(w-1), curses.color_pair(CP_HEADER) | curses.A_BOLD)
                stdscr.refresh()
            except: pass
            # Check for quit between bands
            stdscr.nodelay(True)
            stdscr.timeout(0)
            k = stdscr.getch()
            if k == ord('q') or k == ord('Q'):
                return
            if device == "rtlsdr":
                output = rtlsdr_sweep(f_lo, f_hi)
            else:
                output = hackrf_sweep(f_lo, f_hi, bw, n)
            all_signals.extend(parse_sweep(output))
        
        seen = {}
        unique = []
        for s in all_signals:
            key = round(s['freq'] / 1e6)
            if key not in seen or s['peak'] > seen[key]['peak']:
                seen[key] = s
        unique = list(seen.values())
        
        # Detect active probes (direction-finding signals)
        noise_floor = estimate_noise_floor(unique)
        probes = detect_active_probes(unique, noise_floor)
        for p in probes:
            f = p['freq'] / 1e6
            if not in_legitimate_band(f):
                log.warning(f"ACTIVE PROBE: {f:.1f} MHz, peak={p['peak']:.1f} dBFS, noise_floor={noise_floor:.1f}")
                if round(f) not in known_freqs:
                    known_freqs[round(f)] = time.time()
                    alert_count += 1
        
        new_suspicious = []
        for s in unique:
            f = s['freq'] / 1e6
            if classify(f, s['peak'], s['std']) in ("sus", "danger"):
                if round(f) not in known_freqs:
                    known_freqs[round(f)] = time.time()
                    new_suspicious.append(s)
                    log.warning("SUSPICIOUS: %.1f MHz, peak=%.1f dBFS, std=%.1f" % (f, s["peak"], s["std"]))
                    alert_count += 1
        
        # Update "last seen" for ALL signals
        now = time.time()
        for s in unique:
            key = round(s['freq'] / 1e6)
            last_seen[key] = now  # Update every scan
        
        draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled)
        
        # Update splash status after first scan
        if not first_scan_done:
            first_scan_done = True
            # Replace "in progress" with "OK"
            for i, s in enumerate(status):
                if "in progress" in s:
                    status[i] = s.replace("in progress", "OK")
                    break
        
        if scan_num % 10 == 0:
            cleanup_old_decoded()
            cleanup_old_logs()
        
        # Voice alert
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            above_threshold = [s for s in new_suspicious if s['peak'] > VOICE_THRESHOLD]
            
            if above_threshold:
                announcements = []
                for s in above_threshold[:4]:
                    f = s['freq'] / 1e6
                    dist = est_distance(f, s['peak'])
                    sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                    artemis_entry = identify_signal(f, artemis_db) if artemis_db else None
                    if artemis_entry:
                        # Use description first (matches table), fall back to name
                        name = artemis_entry.get('description', '') or artemis_entry.get('name', '')
                        announcements.append(f"{f:.0f} megahertz, identified as {name}, about {speak_distance(dist)}")
                    else:
                        spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                        if spy_name:
                            announcements.append(f"WARNING! {spy_name} detected at {f:.0f} megahertz, about {speak_distance(dist)}")
                        else:
                            announcements.append(f"{f:.0f} megahertz, {sig_type}, about {speak_distance(dist)}")
                
                voice_result = None
                for s in above_threshold:
                    if s['std'] < 6:
                        voice_result = try_voice_decode(s['freq'] / 1e6)
                        break
                
                for s in above_threshold:
                    f = s['freq'] / 1e6
                    sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
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
        
        # Refresh table after voice (speak() blocks and curses screen goes stale)
        draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled)
        
        # Wait with key handling — curses getch() proven to work
        stdscr.nodelay(True)
        stdscr.timeout(500)
        wait_end = time.time() + INTERVAL
        while time.time() < wait_end:
            key = stdscr.getch()
            if key == ord('q') or key == ord('Q'):
                return
            elif key == ord('+') or key == ord('='):
                INTERVAL = min(600, INTERVAL + 30)
            elif key == ord('-'):
                INTERVAL = max(30, INTERVAL - 30)
            elif key == ord('r') or key == ord('R'):
                break
            elif key == ord('m') or key == ord('M'):
                voice_enabled = not voice_enabled
            elif key == ord('v') or key == ord('V'):
                sus_count = len([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')])
                if voice_enabled:
                    speak(f"Scan complete. {len(unique)} signals found. {sus_count} suspicious.")
        stdscr.nodelay(False)
        stdscr.timeout(-1)

# === LOGGING WITH WEEKLY ROTATION ===
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = "/home/ihorman/sdr_captures/rflord_logs"
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger():
    """Setup rotating logger — 1MB per file, 4 files max (~1 week)."""
    logger = logging.getLogger('rflord')
    logger.setLevel(logging.INFO)
    
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, 'rflord.log'),
        maxBytes=1024*1024,
        backupCount=4,
        encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(handler)
    return logger

def cleanup_old_logs():
    """Delete log files older than 7 days."""
    import glob
    cutoff = time.time() - (7 * 86400)
    for f in glob.glob(os.path.join(LOG_DIR, '*')):
        try:
            if os.path.getmtime(f) < cutoff:
                os.unlink(f)
        except:
            pass

# Initialize logger
log = setup_logger()

def time_ago(timestamp):
    """Format timestamp as human-readable time ago."""
    diff = time.time() - timestamp
    if diff < 60:
        return f"{int(diff)}s"
    elif diff < 3600:
        m = int(diff / 60)
        s = int(diff % 60)
        return f"{m}m{s:02d}s" if s else f"{m}m"
    elif diff < 86400:
        h = int(diff / 3600)
        m = int((diff % 3600) / 60)
        return f"{h}h{m:02d}m" if m else f"{h}h"
    else:
        d = int(diff / 86400)
        h = int((diff % 86400) / 3600)
        return f"{d}d{h:02d}h" if h else f"{d}d"

def main():
    # Detect device BEFORE curses takes over terminal
    device = detect_device()
    if not device:
        print("No SDR device found.")
        sys.exit(1)
    
    # Try curses first (proper terminal), fallback to ANSI
    try:
        if sys.stdout.isatty():
            curses.wrapper(main_curses, device)
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
    known_freqs = {}
    alert_count = 0
    start_time = time.time()
    
    # ANSI colors
    R = "\033[1;31m"; Y = "\033[1;33m"; G = "\033[1;32m"; C = "\033[1;36m"; D = "\033[2m"; N = "\033[0m"; W = "\033[1;37m"
    DR = "\033[1;31;43m"  # Danger: red on yellow background
    
    signal.signal(signal.SIGINT, lambda *_: (sys.stdout.write("\033[?25h\033[H\033[J"), print(f"\n{C}Stopped.{N}"), sys.exit(0)))
    print("\033[2J\033[H\033[?25l", end="")
    # Reset HackRF once at startup
    if device == "hackrf":
        subprocess.run(["sudo", "usbreset", "1d50:6089"], capture_output=True, timeout=5)
        time.sleep(3)

    
    while True:
        scan_num += 1
        log.info(f"=== Scan #{scan_num} started ===")
        
        
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
        sus_count = len([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")])
        log.info(f"Scan #{scan_num}: {len(unique)} signals, {sus_count} suspicious")
        suspicious = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")],
                            key=lambda x: (signal_priority(x["freq"]/1e6, x["std"]), est_distance_m(x["freq"]/1e6, x["peak"]), -x["peak"]))
        ok = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) not in ("sus", "danger")],
                    key=lambda x: x["peak"], reverse=True)


        
        new_suspicious = []
        for s in unique:
            f = s['freq'] / 1e6
            if classify(f, s['peak'], s['std']) in ("sus", "danger"):
                if round(f) not in known_freqs:
                    known_freqs[round(f)] = time.time()
                    new_suspicious.append(s)
                    alert_count += 1
        
        elapsed = int(time.time() - start_time)
        uh, um, us = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        
        sys.stdout.write("\033[H")
        
        # Header
        print(f"{C} RfLord {VERSION}{N} {time.strftime('%H:%M:%S')} │ Up {uh:02d}:{um:02d}:{us:02d} │ "
              f"{Y}Alerts {alert_count}{N} │ Tracked {len(known_freqs)} │ Sig {len(unique)} │ {D}Author: Ihor Kolodyuk{N}")
        
        # Column titles
        mid = 60
        print(f"{R} {'SUSPICIOUS':^{mid-2}}{N}{G} {'KNOWN SIGNALS':^{38}}{N}")
        
        # Sub-headers
        print(f"{D}   Freq    Pwr   Std   Dist Type               Last Seen Remark {N}{D} {'Freq':>6} {'Pwr':>5} {'Std':>4} {'Dist':>5} {'Bnd':>4} {'Identification':<25}{N}")
        
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
                sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                icon = pad_icon(get_signal_icon(sig_type, f, s['std']))
                # Remark: prefer Artemis identification over spy_db
                known_types = {"DAB", "DAB+", "TETRA", "Keyfob", "GSM", "WiFi/BT", "WiFi/FPV",
                               "Link-11", "Milstar", "Gonets", "Display Port", "USB-noise",
                               "USB-burst", "CDMA2000", "3G WCDMA", "LTE", "FM", "AIR"}
                art = identify_signal(f, artemis_db) if artemis_db else None
                if art:
                    remark = art.get('description', '') or art.get('name', '')
                elif sig_type not in known_types:
                    spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                    remark = spy_name if spy_name else ""
                else:
                    remark = ""
                remark_w = max(12, mid - 55)
                remark = remark[:remark_w]
                c = R if i < 3 else Y
                seen_time = known_freqs.get(round(f), time.time())
                ago = time_ago(seen_time)
                # Fresh detection blink for ANSI
                age = time.time() - seen_time
                is_fresh = age < 30
                blink_on = is_fresh and int(time.time() * 2) % 2 == 0
                if is_fresh:
                    c = R if blink_on else Y  # Red/Yellow blink
                # Danger signals (narrowband in DVB-T2 band)
                if classify(f, s['peak'], s['std']) == "danger":
                    c = DR  # Red on yellow background
                left = f"{c}{icon} {f:>5.1f} {s['peak']:>+5.1f} {s['std']:>4.1f} {dist:>5} {sig_type:<18} {ago:>5} {remark}{N}"
            
            if i < len(ok):
                s = ok[i]
                f = s['freq'] / 1e6
                dist = est_distance(f, s['peak'])
                band = get_band(f)
                art = identify_signal(f, artemis_db) if artemis_db else None
                art_str = art['name'][:25] if art else ""
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
            cleanup_old_logs()
        
        # Voice alert (same as curses version)
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            above_threshold = [s for s in new_suspicious if s['peak'] > VOICE_THRESHOLD]
            if above_threshold:
                announcements = []
                for s in above_threshold[:4]:
                    f = s['freq'] / 1e6
                    dist = est_distance(f, s['peak'])
                    sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                    artemis_entry = identify_signal(f, artemis_db) if artemis_db else None
                    if artemis_entry:
                        # Use description first (matches table), fall back to name
                        name = artemis_entry.get('description', '') or artemis_entry.get('name', '')
                        announcements.append(f"{f:.0f} megahertz, identified as {name}, about {speak_distance(dist)}")
                    else:
                        spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                        if spy_name:
                            announcements.append(f"WARNING! {spy_name} detected at {f:.0f} megahertz, about {speak_distance(dist)}")
                        else:
                            announcements.append(f"{f:.0f} megahertz, {sig_type}, about {speak_distance(dist)}")
                voice_result = None
                for s in above_threshold:
                    if s['std'] < 6:
                        voice_result = try_voice_decode(s['freq'] / 1e6)
                        break
                for s in above_threshold:
                    f = s['freq'] / 1e6
                    sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
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
