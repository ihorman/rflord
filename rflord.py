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
    if d < 0.1: return f"{d*1000:.0f}m"
    elif d < 2: return f"{d:.1f}km"
    else: return f"{d:.0f}km"

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
    elif 5150 <= freq_mhz <= 5900: return "WiFi/FPV"
    elif 2400 <= freq_mhz <= 2500: return "WiFi/BT"
    elif std < 2: return "CW"
    elif pmr > 8: return "Digital"
    elif pmr > 4: return "Bursty"
    else: return "Analog"

def print_table(signals, scan_num, known_freqs, alert_count):
    clear()
    now = time.strftime("%H:%M:%S")
    
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
    
    # Header
    print(f"{C}┌──────────────────────────────────────────────────────────────────────────────┐{N}")
    print(f"{C}│{N}  {B}📡 RF MONITOR{N}  {C}│{N} Scan #{scan_num}  {C}│{N} {now}  {C}│{N} Alerts: {Y}{alert_count}{N}  {C}│{N} Tracked: {len(known_freqs)} {C}│{N}")
    print(f"{C}│{N}  {D}Author: Ihor Kolodyuk{N}                                              {C}│{N}")
    print(f"{C}└──────────────────────────────────────────────────────────────────────────────┘{N}")
    print()
    
    # Suspicious signals — color gradient by power
    if all_suspicious:
        # Top 3 in RED (strongest), rest in YELLOW
        top3 = all_suspicious[:3]
        rest = all_suspicious[3:15]
        
        print(f"  {W}SUSPICIOUS SIGNALS{N}")
        print(f"  {D}────────────────────────────────────────────────────────────────────────────{N}")
        print(f"  {D}  {'Freq':>9}  {'Pwr':>6}  {'Std':>4}  {'Dist':>7}  {'Band':>5}  {'Type':>8}  {'CW':>2}  {'St':>3}{N}")
        print(f"  {D}────────────────────────────────────────────────────────────────────────────{N}")
        
        for s in top3:
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            cw = " ⚡" if s['std'] < 2 else ""
            new = f"{Y}NEW{N}" if round(f) not in known_freqs else f"{D} —{N}"
            sig_type = get_signal_type(f, 0, 0, s['std'])
            print(f"  {R}  {f:>9.1f}  {s['peak']:>+5.1f}  {s['std']:>4.1f}  {dist:>7}  {band:>5}  {sig_type:>8}{cw:>3}  {new}{N}")
        
        for s in rest:
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            cw = " ⚡" if s['std'] < 2 else ""
            new = f"{Y}NEW{N}" if round(f) not in known_freqs else f"{D} —{N}"
            sig_type = get_signal_type(f, 0, 0, s['std'])
            print(f"  {Y}  {f:>9.1f}  {s['peak']:>+5.1f}  {s['std']:>4.1f}  {dist:>7}  {band:>5}  {sig_type:>8}{cw:>3}  {new}{N}")
        
        if len(all_suspicious) > 15:
            print(f"  {D}  ... and {len(all_suspicious) - 15} more{N}")
        print()
    
    # Known signals — top 10 strongest in GREEN
    if all_ok:
        print(f"  {G}STRONGEST KNOWN SIGNALS{N}")
        print(f"  {D}────────────────────────────────────────────────────────────────────────────{N}")
        print(f"  {D}  {'Freq':>9}  {'Pwr':>6}  {'Std':>4}  {'Dist':>7}  {'Band':>5}{N}")
        print(f"  {D}────────────────────────────────────────────────────────────────────────────{N}")
        for s in all_ok[:10]:
            f = s['freq'] / 1e6
            dist = est_distance(f, s['peak'])
            band = get_band(f)
            print(f"  {G}  {f:>9.1f}  {s['peak']:>+5.1f}  {s['std']:>4.1f}  {dist:>7}  {band:>5}{N}")
        print()
    
    # Summary
    total = len(signals)
    print(f"  {C}────────────────────────────────────────────────────────────────────────────{N}")
    print(f"  {D}Total: {total}  │  {G}Known: {len(all_ok)}{N}  │  {Y}Suspicious: {len(all_suspicious)}{N}  │  Next: {INTERVAL}s  │  Ctrl+C to stop{N}")
    # Clear remaining lines below (remove leftover from previous render)
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
    
    signal.signal(signal.SIGINT, lambda *_: (sys.stdout.write("\033[?25h"), clear(), print(f"\n{C}Stopped after {scan_num} scans, {alert_count} alerts.{N}"), sys.exit(0)))
    
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
        
        print_table(unique, scan_num, known_freqs, alert_count)
        
        # Voice alert with signal type and voice decode
        if new_suspicious:
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            s0 = new_suspicious[0]
            f0 = s0['freq'] / 1e6
            dist = est_distance(f0, s0['peak'])
            sig_type = get_signal_type(f0, 0, 0, s0['std'])
            
            # Try voice decode on strongest new signal
            voice_result = None
            if s0['std'] < 6:  # Narrowband — could be voice
                voice_result = try_voice_decode(f0)
            
            if voice_result:
                speak(f"{len(new_suspicious)} new signals. Strongest at {f0:.0f} megahertz, about {dist} away. Detected {voice_result}.")
            else:
                speak(f"{len(new_suspicious)} new signals. Strongest at {f0:.0f} megahertz, about {dist} away. Signal type: {sig_type}.")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
