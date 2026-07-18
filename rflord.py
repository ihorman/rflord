#!/usr/bin/env python3
"""
rflord — RF Lord: Real-time RF spectrum monitor with drone detection and voice alerts.
Run in any terminal: rflord [--interval 60]
Author: Ihor Kolodyuk
"""

import warnings
warnings.filterwarnings("ignore")
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import subprocess
import sys
import os
import time
import math
import tempfile
import signal

# ANSI colors
R = "\033[1;31m"   # red (strongest only)
Y = "\033[1;33m"   # yellow (medium)
M = "\033[1;35m"   # magenta (drone)
C = "\033[1;36m"   # cyan (header)
G = "\033[1;32m"   # green (known)
D = "\033[2m"      # dim
B = "\033[1m"      # bold
W = "\033[1;37m"   # white bold
N = "\033[0m"      # reset
BG = "\033[48;5;235m"  # dark background

INTERVAL = 120
TTS_VOICE = "en-US-SteffanNeural"

def run_cmd(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout  # Ignore stderr to prevent table corruption
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
        (960, 1215, "L-BAND"), (1700, 2000, "3G"), (2300, 2700, "LTE"),
        (2400, 2500, "WiFi"), (5150, 5900, "5GHz"),
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
    # Always show meters if < 1km for precision
    if d < 1.0:
        return f"{d*1000:.0f}m"
    elif d < 10:
        return f"{d:.1f}km"
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

def clear():
    # Hide cursor, move to top — no flash, just overwrite
    sys.stdout.write("\033[?25l\033[H")
    sys.stdout.flush()

def try_voice_decode(freq_mhz):
    """Try to decode voice at frequency. Records and plays FM/AM audio if voice band."""
    try:
        scripts = "/home/ihorman/.hermes/profiles/shared/skills/devops/scan-radio/scripts"
        cmd = f"python3 {scripts}/voice_decode.py scan {freq_mhz} --duration 3 2>&1"
        r = run_cmd(cmd, timeout=15)
        
        # If signal is in FM/AM voice band, record and play audio
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

def play_voice_sample(freq_mhz):
    """Record IQ at frequency, demodulate to audio, and play it."""
    try:
        freq_hz = int(freq_mhz * 1e6)
        raw = tempfile.mktemp(suffix='.raw', prefix='voice_')
        wav = tempfile.mktemp(suffix='.wav', prefix='voice_')
        
        # Capture IQ (2 seconds)
        subprocess.run(["hackrf_transfer", "-r", raw, "-f", str(freq_hz),
                        "-s", "2000000", "-n", "4000000", "-l", "32", "-g", "40", "-a", "1"],
                       capture_output=True, timeout=10)
        
        if not os.path.exists(raw) or os.path.getsize(raw) < 1000:
            return
        
        # Demodulate
        import numpy as np
        data = np.fromfile(raw, dtype=np.int8)
        iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
        iq /= 128.0
        os.unlink(raw)
        
        if 88 <= freq_mhz <= 108:
            # FM broadcast — wide FM with de-emphasis
            phase = np.unwrap(np.angle(iq))
            audio = np.diff(phase) * 2000000 / (2 * np.pi)
            # De-emphasis filter (75μs)
            alpha = 1.0 / (1.0 + 2000000 * 75e-6)
            for i in range(1, len(audio)):
                audio[i] = audio[i] * (1 - alpha) + audio[i-1] * alpha
        else:
            # Narrow FM (PMR, air band AM, etc.)
            phase = np.unwrap(np.angle(iq))
            audio = np.diff(phase) * 2000000 / (2 * np.pi)
        
        audio = audio / (np.max(np.abs(audio)) + 1e-10) * 0.8
        
        # Resample to 48kHz
        import wave
        target_rate = 48000
        src_rate = 2000000
        step = src_rate / target_rate
        indices = np.arange(0, len(audio), step).astype(int)
        indices = indices[indices < len(audio)]
        audio_48k = audio[indices]
        
        # Write WAV
        audio_16 = (audio_48k * 32767).astype(np.int16)
        with wave.open(wav, 'w') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(target_rate)
            w.writeframes(audio_16.tobytes())
        
        # Play
        ensure_sink()
        subprocess.run(["paplay", wav], capture_output=True, timeout=10)
        os.unlink(wav)
    except Exception:
        pass

def get_signal_type(freq_mhz, bw, pmr, std):
    """Classify signal type for display."""
    if 230 <= freq_mhz <= 285:
        if bw < 50000: return "DP/USB"
        else: return "DP-bursty"
    elif 612 <= freq_mhz <= 700:
        if bw < 10000: return "USB-noise"
        else: return "USB-bursty"
    elif 240 <= freq_mhz <= 242: return "DAB"
    elif 390 <= freq_mhz <= 400: return "TETRA"
    elif 337 <= freq_mhz <= 362: return "Keyfob"
    # Hidden camera / spy tool bands
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

def format_row(freq, power, std, dist, band, sig_type, cw, status, color):
    """Format a single table row with fixed-width columns."""
    return (f"  {color}{freq:>8.1f}  {power:>+5.1f}  {std:>4.1f}  "
            f"{dist:>6}  {band:>5}  {sig_type:>8}{cw:<2}  {status}{N}")

def print_table(signals, start_time, known_freqs, alert_count):
    # Separate signals
    all_suspicious = []
    all_ok = []
    for sig in signals:
        f = sig['freq'] / 1e6
        cls = classify(f, sig['peak'], sig['std'])
        if cls == "sus":
            all_suspicious.append(sig)
        else:
            all_ok.append(sig)
    
    # Sort by power (strongest first)
    all_suspicious.sort(key=lambda x: x['peak'], reverse=True)
    all_ok.sort(key=lambda x: x['peak'], reverse=True)
    
    # Calculate uptime
    elapsed = int(time.time() - start_time)
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    uptime = f"{h:02d}:{m:02d}:{s:02d}"
    now = time.strftime("%H:%M:%S")
    
    # Move cursor to top — don't clear header area
    sys.stdout.write("\033[H")
    sys.stdout.flush()
    
    # Compact header — only update values, not structure
    print(f"{C}  RF LORD │{N} {now} │ Uptime: {uptime} │ {Y}Alerts: {alert_count}{N} │ Tracked: {len(known_freqs)} │ Signals: {len(signals)} │ by Ihor Kolodyuk")
    print(f"{C}  {'─' * 78}{N}")
    
    # Column header (fixed position)
    print(f"  {'Freq':>8}  {'Pwr':>5}  {'Std':>4}  {'Dist':>6}  {'Band':>5}  {'Type':>8}  {'St':>2}")
    print(f"  {'─' * 78}")
    
    # Suspicious signals
    row = 0
    if all_suspicious:
        top3 = all_suspicious[:3]
        rest = all_suspicious[3:12]
        
        for s in top3:
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            cw = "⚡" if s['std'] < 2 else " "
            st = f"{Y}NEW{N}" if round(f) not in known_freqs else f"{D} —{N}"
            sig_type = get_signal_type(f, 0, 0, s['std'])
            print(format_row(f, s['peak'], s['std'], dist, band, sig_type, cw, st, R))
            row += 1
        
        for s in rest:
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            cw = "⚡" if s['std'] < 2 else " "
            st = f"{Y}NEW{N}" if round(f) not in known_freqs else f"{D} —{N}"
            sig_type = get_signal_type(f, 0, 0, s['std'])
            print(format_row(f, s['peak'], s['std'], dist, band, sig_type, cw, st, Y))
            row += 1
        
        if len(all_suspicious) > 12:
            print(f"  {D}  ... +{len(all_suspicious) - 12} more{N}")
            row += 1
    
    # Separator
    print(f"  {D}{'─' * 78}{N}")
    row += 1
    
    # Known signals — top 10
    for s in all_ok[:10]:
        f = s['freq'] / 1e6
        dist = est_distance(f, s['peak'])
        band = get_band(f)
        print(format_row(f, s['peak'], s['std'], dist, band, "", "", "", G))
        row += 1
    
    # Fill remaining rows with blank to prevent leftover
    print(f"  {D}{'─' * 78}{N}")
    print(f"  {D}Ctrl+C to stop{N}")
    
    # Clear everything below
    sys.stdout.write("\033[J")
    sys.stdout.flush()

def main():
    global INTERVAL
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--interval" and i + 2 <= len(sys.argv):
            INTERVAL = int(sys.argv[i + 2])
    
    device = detect_device()
    if not device:
        print("No SDR device found. Connect HackRF or enable RTL-SDR.")
        sys.exit(1)
    
    ensure_sink()
    
    bands = [
        (88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3),
        (1000, 1700, 2000000, 3), (1700, 2500, 1000000, 3), (2500, 3500, 1000000, 3),
        (5150, 5900, 500000, 3),
    ]
    
    scan_num = 0
    known_freqs = set()
    alert_count = 0
    start_time = time.time()
    
    signal.signal(signal.SIGINT, lambda *_: (sys.stdout.write("\033[?25h"), sys.stdout.write("\033[H\033[J"), print(f"\n{C}Stopped after {scan_num} scans, {alert_count} alerts.{N}"), sys.exit(0)))
    
    # Print initial header once
    print(f"\033[2J\033[H", end="")
    
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
        
        # Find new suspicious
        new_suspicious = []
        for s in unique:
            f = s['freq'] / 1e6
            if classify(f, s['peak'], s['std']) == "sus":
                if round(f) not in known_freqs:
                    known_freqs.add(round(f))
                    new_suspicious.append(s)
                    alert_count += 1
        
        print_table(unique, start_time, known_freqs, alert_count)
        
        # Voice alert with signal type and voice decode
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            s0 = new_suspicious[0]
            f0 = s0['freq'] / 1e6
            dist = est_distance(f0, s0['peak'])
            sig_type = get_signal_type(f0, 0, 0, s0['std'])
            
            voice_result = None
            if s0['std'] < 6:
                voice_result = try_voice_decode(f0)
            
            if voice_result:
                speak(f"{len(new_suspicious)} new signals. Strongest at {f0:.0f} megahertz, about {dist} away. Detected {voice_result}.")
            else:
                speak(f"{len(new_suspicious)} new signals. Strongest at {f0:.0f} megahertz, about {dist} away. Signal type: {sig_type}.")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
