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
import threading
from datetime import datetime
from spy_db import identify_spy_device, get_signal_icon, get_threat_icon, pad_icon
from config import load_config
from history import SignalHistory
from export import export_csv, export_json
from blacklist import load_blacklist, filter_blacklisted
from scan_accel import ScanAccelerator
from rf_protocols import identify_by_freq as rfproto_identify, get_protocol_count as rfproto_count
from rule_engine import RuleEngine

# Load config
_cfg = load_config()
VERSION = "v0.7.0"
_key_cmd = None
INTERVAL = _cfg['scan']['interval']

# Suppress mode
def _build_suppress_targets(cfg_targets):
    """Convert config list format to dict format for suppress targets."""
    if isinstance(cfg_targets, dict):
        return cfg_targets  # Already in old format
    # Convert list format: [{name, freq, bw}, ...] -> {Name: {freqs: [...], bw: N}}
    groups = {}
    for t in cfg_targets:
        name = t['name']
        # Capitalize known names nicely
        name_map = {'cellular': 'Cellular', 'bluetooth': 'Bluetooth', 'gps': 'GPS'}
        name = name_map.get(name, name.capitalize())
        if name not in groups:
            groups[name] = {'freqs': [], 'bw': t.get('bw', 20e6)}
        groups[name]['freqs'].append(t['freq'])
        # Use largest bw seen for the group
        if t.get('bw', 0) > groups[name]['bw']:
            groups[name]['bw'] = t['bw']
    return groups

_suppress_active = False
_suppress_targets = {}
SUPPRESS_TARGETS = _build_suppress_targets(_cfg['suppress']['targets'])
_suppress_procs = []
_menu_active = False
_cursor_pos = 0
_cursor_active = False
_cursor_panel = 'sus'  # 'sus' or 'ok' — which panel the cursor is in
_scan_status = ""  # Status line from scan workers (written by threads, read by main thread)

TTS_VOICE = _cfg['voice']['voice_name']
HAL_EFFECT = os.path.expanduser(_cfg['voice']['hal_effect'])
VOICE_THRESHOLD = _cfg['voice']['threshold']
ARTEMIS_DB = "/opt/artemis/Data/db.csv"
DECODED_DIR = os.path.expanduser('~/.flord')
MAX_AGE_DAYS = _cfg['history']['max_days']

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

def _find_binary(name):
    """Find binary in PATH. Returns full path or None."""
    from shutil import which
    return which(name)

# Auto-detect binary paths once at import time
HACKRF_SWEEP_BIN = _find_binary("hackrf_sweep") or "/usr/bin/hackrf_sweep"
HACKRF_INFO_BIN = _find_binary("hackrf_info") or "hackrf_info"
RTL_POWER_BIN = _find_binary("rtl_power") or "/usr/local/bin/rtl_power"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

def _get_usb_devices():
    """Get USB device list, cross-platform."""
    if IS_MACOS:
        out = run_cmd("system_profiler SPUSBDataType 2>/dev/null")
        return out
    else:
        return run_cmd("lsusb")

def _has_hackrf_in_usb(usb_output):
    """Check if HackRF is present in USB output."""
    if IS_MACOS:
        return "HackRF" in usb_output or "1d50:6089" in usb_output or "Product ID: 0x6089" in usb_output
    else:
        return "1d50:6089" in usb_output

def _has_portapack_in_usb(usb_output):
    """Check if PortaPack (HackRF in PortaPack mode) is present."""
    if IS_MACOS:
        return "PortaPack" in usb_output or "1d50:6018" in usb_output or "Product ID: 0x6018" in usb_output
    else:
        return "1d50:6018" in usb_output

def _has_rtlsdr_in_usb(usb_output):
    """Check if RTL-SDR is present in USB output."""
    if IS_MACOS:
        return "RTL" in usb_output or "0bda:2838" in usb_output or "Product ID: 0x2838" in usb_output
    else:
        return "0bda:2838" in usb_output

def detect_device():
    """Detect available SDR devices. Returns list of device names.

    Cross-platform: uses hackrf_info (all), system_profiler (macOS), lsusb (Linux).
    """
    devices = []

    # Method 1: Try hackrf_info directly (most reliable, works on all platforms)
    try:
        r = subprocess.run([HACKRF_INFO_BIN], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and "Found HackRF" in r.stdout:
            print("HackRF detected via hackrf_info", flush=True)
            devices.append("hackrf")
    except Exception as e:
        log.debug(f"hackrf_info failed: {e}")

    # Method 2: USB enumeration for PortaPack mode switch and RTL-SDR
    usb_output = _get_usb_devices()

    # PortaPack mode switch (PortaPack shows as 1d50:6018, need to switch to HackRF mode 1d50:6089)
    if not devices and _has_portapack_in_usb(usb_output):
        print("PortaPack detected, switching to HackRF mode...", flush=True)
        if IS_LINUX:
            import serial
            for port in ["/dev/ttyACM1", "/dev/ttyACM0"]:
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
                    print(f"  Sent mode switch on {port}", flush=True)
                    time.sleep(2)
                    # Re-check
                    try:
                        r = subprocess.run([HACKRF_INFO_BIN], capture_output=True, text=True, timeout=10)
                        if r.returncode == 0 and "Found HackRF" in r.stdout:
                            print("  Switched to HackRF mode!", flush=True)
                            devices.append("hackrf")
                            break
                    except: pass
                except Exception as e:
                    print(f"  {port} failed: {e}", flush=True)
            else:
                # Try usbreset as last resort
                try:
                    subprocess.run(["sudo", "usbreset", "1d50:6018"], capture_output=True, timeout=5)
                    time.sleep(3)
                    r = subprocess.run([HACKRF_INFO_BIN], capture_output=True, text=True, timeout=10)
                    if r.returncode == 0 and "Found HackRF" in r.stdout:
                        devices.append("hackrf")
                except: pass
        else:
            print("  PortaPack mode switch not supported on macOS (reconnect in HackRF mode)", flush=True)

    # RTL-SDR detection
    if _has_rtlsdr_in_usb(usb_output):
        devices.append("rtlsdr")
    elif IS_LINUX:
        # Fallback: try rtl_test
        try:
            r = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=5)
            if "Found" in r.stdout:
                devices.append("rtlsdr")
        except: pass

    if not devices:
        print(f"SDR not found. USB devices: {usb_output[:300]}", flush=True)
    return devices

def hackrf_sweep(f_lo, f_hi, bw=2000000, n=3):
    cmd = f"{HACKRF_SWEEP_BIN} -f {f_lo}:{f_hi} -w {bw} -l 32 -g 40 -a 1 -N {n}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=45)
        if r.returncode != 0:
            err = r.stderr.strip()
            if err:
                log.warning(f"hackrf_sweep {f_lo}-{f_hi} MHz failed: {err[:200]}")
            return ""
        # Filter to data lines only
        lines = [l for l in r.stdout.strip().split('\n') if l and l[0].isdigit()]
        return '\n'.join(lines)
    except subprocess.TimeoutExpired:
        log.warning(f"hackrf_sweep {f_lo}-{f_hi} MHz timeout")
        return ""
    except Exception as e:
        log.warning(f"hackrf_sweep {f_lo}-{f_hi} MHz error: {e}")
        return ""

def rtlsdr_sweep(f_lo, f_hi, gain=40, n=1):
    """RTL-SDR sweep using rtl_power. f_lo/f_hi in MHz (same as hackrf_sweep)."""
    cmd = f"{RTL_POWER_BIN} -f {f_lo}M:{f_hi}M:2.4M -g {gain} -e 2s 2>/dev/null | grep '^[0-9]'"
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

def group_signals_by_type(signals, artemis_db=None):
    """Group signals by type. Returns list of dicts with representative signal info."""
    groups = {}
    for s in signals:
        f = s['freq'] / 1e6
        sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
        # Use Artemis name as group key if available, skip for military UHF
        if 225 <= f <= 400:
            art = None
        else:
            art = identify_signal(f, artemis_db) if artemis_db else None
        key = art['name'] if art else sig_type
        if key not in groups:
            groups[key] = []
        groups[key].append(s)
    
    result = []
    for key, sigs in groups.items():
        # Pick strongest signal for power
        strongest = max(sigs, key=lambda x: x['peak'])
        # Pick nearest signal for distance
        nearest = min(sigs, key=lambda x: est_distance_m(x['freq']/1e6, x['peak']))
        f_strong = strongest['freq'] / 1e6
        f_near = nearest['freq'] / 1e6
        result.append({
            'type': key,
            'count': len(sigs),
            'freq': f_strong,
            'peak': strongest['peak'],
            'std': strongest['std'],
            'dist': est_distance(f_near, nearest['peak']),
            'band': get_band(f_strong),
            'nearest_freq': f_near,
            '_strongest': strongest,  # Keep reference for detail popup
        })
    # Sort by count descending, then by peak
    result.sort(key=lambda x: (-x['count'], -x['peak']))
    return result

def group_suspicious(signals, artemis_db=None):
    """Group suspicious signals by identification (artemis name or signal type)."""
    groups = {}
    for s in signals:
        f = s['freq'] / 1e6
        sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
        # Skip Artemis for military UHF band
        if 225 <= f <= 400:
            art = None
        else:
            art = identify_signal(f, artemis_db) if artemis_db else None
        key = art['name'] if art else sig_type
        if key not in groups:
            groups[key] = []
        groups[key].append(s)
    
    result = []
    for key, sigs in groups.items():
        strongest = max(sigs, key=lambda x: x['peak'])
        nearest = min(sigs, key=lambda x: est_distance_m(x['freq']/1e6, x['peak']))
        f_strong = strongest['freq'] / 1e6
        f_near = nearest['freq'] / 1e6
        # Skip Artemis for military UHF band
        if 225 <= f_strong <= 400:
            art = None
        else:
            art = identify_signal(f_strong, artemis_db) if artemis_db else None
        remark = art.get('description', '') if art else ''
        result.append({
            'type': key,
            'count': len(sigs),
            'freq': f_strong,
            'peak': strongest['peak'],
            'std': strongest['std'],
            'dist': est_distance(f_near, nearest['peak']),
            'remark': remark,
            'classify': classify(f_strong, strongest['peak'], strongest['std']),
            '_strongest': strongest,  # Keep reference for detail popup
        })
    result.sort(key=lambda x: (-x['count'], -x['peak']))
    return result

def classify(f, power, std):
    """Classify signal as ok/sus/danger.
    
    Conservative approach: only flag as suspicious if signal is:
    1. In a known surveillance frequency band AND
    2. Narrowband (std < 2) AND
    3. Strong (power > -30 dBFS) AND
    4. NOT in a known legitimate band
    """
    
    # === KNOWN LEGITIMATE SIGNALS ===
    
    # WiFi 2.4 GHz
    if 2400 <= f <= 2500:
        return "ok"
    
    # WiFi 5 GHz
    if 5150 <= f <= 5875:
        return "ok"
    
    # GSM/LTE cellular
    if 925 <= f <= 960:
        return "ok"
    if 1805 <= f <= 1880:
        return "ok"
    if 1700 <= f <= 2000:
        return "ok"
    if 2000 <= f <= 2200:
        return "ok"
    if 2300 <= f <= 2700:
        return "ok"
    if 791 <= f <= 862:
        return "ok"
    if 2620 <= f <= 2690:
        return "ok"
    
    # FM Radio
    if 88 <= f <= 108:
        return "ok"
    
    # DAB/DVB-T
    if 174 <= f <= 230:
        return "ok"
    if 470 <= f <= 790:
        return "ok"
    
    # Aviation
    if 108 <= f <= 137:
        return "ok"
    if 1089 <= f <= 1091:
        return "ok"
    if 1574 <= f <= 1576:
        return "ok"
    
    # Amateur radio
    if 144 <= f <= 148:
        return "ok"
    if 430 <= f <= 470:
        return "ok"
    
    # PMR/FRS/GMRS
    if 446 <= f <= 447:
        return "ok"
    if 462 <= f <= 468:
        return "ok"
    
    # ISM bands
    if 433 <= f <= 435:
        return "ok"
    if 868 <= f <= 870:
        return "ok"
    if 915 <= f <= 928:
        return "ok"
    
    # UHF TV
    if 400 <= f <= 470:
        return "ok"
    if 510 <= f <= 610:
        return "ok"
    
    # Military (known frequencies)
    if 225 <= f <= 400:
        return "ok"
    
    # === POTENTIALLY SUSPICIOUS ===
    # Only flag if narrowband AND strong AND in known surveillance band
    
    # Spy camera900 MHz band
    if 900 <= f <= 928 and std < 2 and power > -30:
        return "sus"
    
    # Spy camera1.2 GHz band
    if 1080 <= f <= 1300 and std < 2 and power > -30:
        return "sus"
    
    # FPV video5.8 GHz
    if 5725 <= f <= 5875 and std < 2 and power > -30:
        return "sus"
    
    # Unknown strong signal
    if power > -15:
        return "sus"
    
    return "ok"

def est_distance(freq_mhz, power_dbfs):
    """Estimate distance from signal power using FSPL model.

    HackRF with -l 32 -g 40 -a 1 = 83 dB total gain.
    hackrf_sweep output is in dBFS relative to ADC full scale.
    With83 dB gain,0 dBFS at ADC = -83 dBm at antenna.
    But hackrf_sweep partially normalizes, so effective offset is different.
    Calibrated: WiFi AP 100mW at 10m reads ~-50 dBFS → SDR_GAIN ≈ 10.
    """
    SDR_GAIN = 10
    rx_dbm = power_dbfs - SDR_GAIN

    # Estimated transmit power (dBm) — realistic for typical sources
    if 88 <= freq_mhz <= 108:    tx = 60    # FM broadcast tower (1 kW)
    elif 174 <= freq_mhz <= 230: tx = 40    # DVB-T / DAB tower
    elif 470 <= freq_mhz <= 790: tx = 40    # DVB-T tower
    elif 800 <= freq_mhz <= 960: tx = 43    # GSM base station (20W)
    elif 1805 <= freq_mhz <= 1880: tx = 43  # GSM1800 base station (20W)
    elif 2110 <= freq_mhz <= 2170: tx = 43  # 3G/LTE base station (20W)
    elif 108 <= freq_mhz <= 137: tx = 37    # Air band (aircraft 5W)
    elif 144 <= freq_mhz <= 148: tx = 37    # 2m ham (5W)
    elif 430 <= freq_mhz <= 470: tx = 30    # PMR / UHF handheld (1W)
    elif 2400 <= freq_mhz <= 2500: tx = 20  # WiFi / Bluetooth (100mW)
    elif 5150 <= freq_mhz <= 5900: tx = 23  # WiFi 5 GHz (200mW)
    elif 5725 <= freq_mhz <= 5875: tx = 14  # FPV / spy camera (25mW)
    elif 900 <= freq_mhz <= 928:  tx = 14   # Spy camera 900 MHz (25mW)
    elif 1080 <= freq_mhz <= 1300: tx = 14  # Spy camera 1.2 GHz (25mW)
    else: tx = 20                            # Default: 100 mW

    # Free-space path loss from link budget
    fspl = tx - rx_dbm  # dB
    fspl = max(20, min(160, fspl))
    # FSPL(dB) = 20*log10(d_km) + 20*log10(f_MHz) + 32.44
    # Solve for d_km:
    d_km = 10 ** ((fspl - 32.44 - 20 * math.log10(max(freq_mhz, 1))) / 20)
    d_km = max(0.001, min(500, d_km))
    meters = d_km * 1000
    if meters < 1000:
        return f"{meters:.0f}m"
    elif meters < 10000:
        return f"{meters/1000:.1f}km"
    else:
        return f"{meters/1000:.0f}km"

def est_distance_m(freq_mhz, power_dbfs):
    """Return distance in meters as a number (for sorting)."""
    SDR_GAIN = 10
    rx_dbm = power_dbfs - SDR_GAIN
    if 88 <= freq_mhz <= 108:    tx = 60
    elif 174 <= freq_mhz <= 230: tx = 40
    elif 470 <= freq_mhz <= 790: tx = 40
    elif 800 <= freq_mhz <= 960: tx = 43
    elif 1805 <= freq_mhz <= 1880: tx = 43
    elif 2110 <= freq_mhz <= 2170: tx = 43
    elif 108 <= freq_mhz <= 137: tx = 37
    elif 144 <= freq_mhz <= 148: tx = 37
    elif 430 <= freq_mhz <= 470: tx = 30
    elif 2400 <= freq_mhz <= 2500: tx = 20
    elif 5150 <= freq_mhz <= 5900: tx = 23
    elif 5725 <= freq_mhz <= 5875: tx = 14
    elif 900 <= freq_mhz <= 928:  tx = 14
    elif 1080 <= freq_mhz <= 1300: tx = 14
    else: tx = 20
    fspl = tx - rx_dbm
    fspl = max(20, min(160, fspl))
    d_km = 10 ** ((fspl - 32.44 - 20 * math.log10(max(freq_mhz, 1))) / 20)
    d_km = max(0.001, min(500, d_km))
    return d_km * 1000

def identify_signal_type(freq_mhz, power_dbfs, std):
    """Identify what a signal likely is based on frequency and characteristics."""
    # Cellular
    if 935 <= freq_mhz <= 960:
        return "GSM900"
    if 1805 <= freq_mhz <= 1880:
        return "GSM1800"
    if 2110 <= freq_mhz <= 2170:
        return "3G/LTE"
    if 791 <= freq_mhz <= 862:
        return "LTE800"
    if 2620 <= freq_mhz <= 2690:
        return "LTE2600"
    
    # WiFi 2.4 GHz
    wifi_ch = {1:2412, 2:2417, 3:2422, 4:2427, 5:2432, 6:2437, 7:2442, 8:2447, 9:2452, 10:2457, 11:2462, 12:2467, 13:2472}
    for ch, center in wifi_ch.items():
        if abs(freq_mhz - center) < 3:
            return f"WiFi Ch{ch}"
    if 2400 <= freq_mhz <= 2500:
        return "WiFi 2.4GHz"
    
    # WiFi 5 GHz (non-overlapping with FPV)
    if 5150 <= freq_mhz <= 5725:
        return "WiFi 5GHz"
    
    # FPV band (5725-5875 MHz) — shared with WiFi UNII-3
    # FPV is narrowband analog video, WiFi is wideband OFDM
    if 5725 <= freq_mhz <= 5875:
        if std < 2 and power_dbfs > -30:
            return "FPV Video"
        elif std < 3:
            return "FPV/WiFi"
        else:
            return "WiFi 5GHz"
    
    # Broadcast
    if 88 <= freq_mhz <= 108:
        return "FM Radio"
    if 174 <= freq_mhz <= 230:
        return "DAB/DVB-T"
    if 470 <= freq_mhz <= 790:
        return "DVB-T"
    
    # Aviation
    if 108 <= freq_mhz <= 137:
        return "Air Band"
    if 1089 <= freq_mhz <= 1091:
        return "ADS-B"
    if 1574 <= freq_mhz <= 1576:
        return "GPS L1"
    
    # Amateur
    if 144 <= freq_mhz <= 148:
        return "2m Ham"
    if 430 <= freq_mhz <= 470:
        return "70cm Ham/PMR"
    
    # ISM
    if 433 <= freq_mhz <= 435:
        return "ISM433"
    if 868 <= freq_mhz <= 870:
        return "ISM868"
    if 915 <= freq_mhz <= 928:
        return "ISM915"
    
    # Surveillance (only if narrowband and strong)
    if 900 <= freq_mhz <= 928 and std < 2 and power_dbfs > -30:
        return "Possible Camera"
    if 1080 <= freq_mhz <= 1300 and std < 2 and power_dbfs > -30:
        return "Possible Camera"
    
    return "Unknown"

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

_speaking = False

def speak(text):
    """Speak text via edge-tts with HAL 9000 effect. Non-blocking — runs in daemon thread."""
    global _speaking
    if _speaking:
        log.info(f"SPEAK SKIP (already speaking): {text[:60]}")
        return
    log.info(f"SPEAK: {text[:100]}")
    def _speak_thread():
        global _speaking
        _speaking = True
        try:
            raw = tempfile.mktemp(suffix='.mp3', prefix='tts_')
            out = tempfile.mktemp(suffix='.wav', prefix='hal_')
            # Use edge-tts as module (more reliable than CLI)
            import edge_tts
            import asyncio
            communicate = edge_tts.Communicate(text, TTS_VOICE, rate="-15%")
            asyncio.run(communicate.save(raw))
            if os.path.exists(raw):
                r2 = subprocess.run([HAL_EFFECT, raw, out], capture_output=True, timeout=30)
                os.unlink(raw)
                if r2.returncode != 0:
                    log.warning(f"TTS hal-effect failed: rc={r2.returncode} stderr={r2.stderr[:200]}")
                    return
                if os.path.exists(out):
                    # Cross-platform audio playback
                    if IS_MACOS:
                        r3 = subprocess.run(["afplay", out], capture_output=True, timeout=120)
                    else:
                        r3 = subprocess.run(["paplay", out], capture_output=True, timeout=120)
                    os.unlink(out)
                    if r3.returncode != 0:
                        log.warning(f"TTS playback failed: rc={r3.returncode}")
            else:
                log.warning(f"TTS raw file not created: {raw}")
        except Exception as e:
            log.warning(f"TTS exception: {e}")
        finally:
            _speaking = False
    t = threading.Thread(target=_speak_thread, daemon=True, name="rflord-speak")
    t.start()

def _generate_noise(path, duration_s=10, rate=2000000):
    """Generate random IQ noise file for HackRF TX."""
    n = int(rate * duration_s)
    samples = np.random.randint(-127, 128, n, dtype=np.int8)
    samples.tofile(path)

def _suppress_start():
    """Start transmitting noise on selected frequencies."""
    global _suppress_procs
    _suppress_stop()  # Kill any existing
    noise = '/tmp/rflord_noise.bin'
    if not os.path.exists(noise):
        _generate_noise(noise, duration_s=60)
    for name, active in _suppress_targets.items():
        if not active:
            continue
        info = SUPPRESS_TARGETS[name]
        for freq in info['freqs']:
            cmd = ["hackrf_transfer", "-t", noise, "-f", str(int(freq)),
                   "-s", "2000000", "-a", "1", "-x", "40", "-n", "120000000"]
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _suppress_procs.append(p)
            except:
                pass

def _suppress_stop():
    """Stop all suppress transmissions."""
    global _suppress_procs
    for p in _suppress_procs:
        try:
            p.terminate()
            p.wait(timeout=2)
        except:
            try: p.kill()
            except: pass
    _suppress_procs = []

def _show_suppress_menu(stdscr):
    """Show suppress target selection popup. Returns True if changed."""
    global _suppress_targets, _menu_active
    # Save screen state
    stdscr.nodelay(False)
    stdscr.timeout(-1)
    h, w = stdscr.getmaxyx()
    options = list(SUPPRESS_TARGETS.keys())
    cursor = 0
    
    while True:
        # Draw popup
        pw, ph = 42, len(options) + 6
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        
        # Border
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        
        try:
            stdscr.addstr(py, px + 2, " SUPPRESS TARGETS ", curses.color_pair(CP_SUS_RED) | curses.A_BOLD)
            stdscr.addstr(py + 1, px + 1, " Arrows+Enter/Space=toggle, q/Esc=close", curses.color_pair(CP_DIM))
            stdscr.addstr(py + 2, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
        except: pass
        
        for i, name in enumerate(options):
            active = _suppress_targets.get(name, False)
            marker = "[X]" if active else "[ ]"
            sel = i == cursor
            attr = curses.color_pair(CP_SUS_RED if active else CP_OK)
            if sel:
                attr |= curses.A_REVERSE
            try:
                stdscr.addstr(py + 3 + i, px + 2, f" {marker} {name:<20}", attr)
            except: pass
        
        try:
            any_active = any(_suppress_targets.get(n, False) for n in options)
            status = "S:Suppress ON" if any_active else "S:Suppress OFF"
            stdscr.addstr(py + ph - 2, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
            stdscr.addstr(py + ph - 1, px + 2, f" {status:<30}", curses.color_pair(CP_DIM))
        except: pass
        
        stdscr.refresh()
        
        # Input
        stdscr.nodelay(False)
        stdscr.timeout(-1)
        key = stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)

        if key == curses.KEY_UP and cursor > 0:
            cursor -= 1
        elif key == curses.KEY_DOWN and cursor < len(options) - 1:
            cursor += 1
        elif key == ord(' ') or key in (10, 13):
            # Space or Enter toggles the current target
            name = options[cursor]
            _suppress_targets[name] = not _suppress_targets.get(name, False)
        elif key == ord('q') or key == ord('Q') or key == 27:  # ESC/q to close
            stdscr.nodelay(True)
            stdscr.timeout(200)
            return True

def _wrap_text(text, width):
    """Word-wrap text to fit within width."""
    words = text.split()
    lines = []
    current = ""
    for w in words:
        if current and len(current) + 1 + len(w) > width:
            lines.append(current)
            current = w
        else:
            current = current + " " + w if current else w
    if current:
        lines.append(current)
    return lines

def _show_signal_detail(stdscr, signal, artemis_db):
    """Show signal detail popup with full description text."""
    h, w = stdscr.getmaxyx()
    f = signal['freq'] / 1e6
    sig_type = get_signal_type(f, 0, 0, signal['std'], artemis_db)
    dist = est_distance(f, signal['peak'])
    cls = classify(f, signal['peak'], signal['std'])
    band = get_band(f)
    spy_name, spy_icon, threat = identify_spy_device(f, signal['std'])
    
    # Artemis lookup (skip for military UHF)
    art = None
    if not (225 <= f <= 400):
        art = identify_signal(f, artemis_db) if artemis_db else None
    
    # Determine popup width (use most of screen)
    pw = min(max(60, w - 4), 100)
    text_w = pw - 4  # Usable text width inside popup
    
    lines = []
    lines.append(("label", f"  Frequency:    {f:.3f} MHz"))
    lines.append(("label", f"  Type:         {sig_type}"))
    lines.append(("label", f"  Band:         {band}"))
    lines.append(("label", f"  Power:        {signal['peak']:+.1f} dBFS"))
    lines.append(("label", f"  Std Dev:      {signal['std']:.1f}"))
    lines.append(("label", f"  Distance:     {dist}"))
    lines.append(("label", f"  Class:        {cls}"))
    
    if art:
        lines.append(("art", f"  Artemis:      {art['name']}"))
        if art.get('description'):
            desc = art['description']
            wrapped = _wrap_text(desc, text_w - 16)
            for j, wl in enumerate(wrapped):
                prefix = "  Description:  " if j == 0 else "                "
                lines.append(("desc", f"{prefix}{wl}"))
        if art.get('modulation'):
            lines.append(("art", f"  Modulation:   {art['modulation']}"))
        if art.get('country'):
            lines.append(("art", f"  Country:      {art['country']}"))
        if art.get('url'):
            lines.append(("dim", f"  URL:          {art['url'][:text_w-16]}"))

    # RF Protocol Database lookup
    protos = rfproto_identify(f, tolerance_mhz=0.5)
    if protos:
        lines.append(("label", f"  RF Protocols: {len(protos)} match(es)"))
        for p in protos[:5]:
            lines.append(("label", f"    {p['name']} ({p['category']})"))
    
    if spy_name:
        lines.append(("danger", f"  Spy Device:   {spy_icon} {spy_name}"))
        lines.append(("danger", f"  Threat Level: {threat}"))
    
    # Signature DB lookup
    try:
        from signatures_db import SignaturesDB
        sdb = SignaturesDB()
        sig_matches = sdb.identify_freq(f, tolerance_mhz=0.5)
        if sig_matches:
            lines.append(("label", f"  Signature DB: {len(sig_matches)} match(es)"))
            for sm in sig_matches[:3]:
                name = sm.get('name', '?')
                src = sm.get('source', '?')
                lines.append(("label", f"    {name} [{src}]"))
    except: pass
    
    # Flatten lines to strings
    display_lines = [(t, l) for t, l in lines]
    text_lines = [l for _, l in display_lines]
    
    # Build popup
    ph = min(len(display_lines) + 6, h - 2)
    py = max(0, (h - ph) // 2)
    px = max(0, (w - pw) // 2)
    
    # Scroll offset for long content
    scroll = 0
    visible_rows = ph - 6  # rows available for content
    
    while True:
        # Draw popup background
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        
        try:
            cp = CP_DANGER if cls == "danger" else (CP_SUS_RED if cls == "sus" else CP_OK)
            title = f" SIGNAL DETAIL — {f:.1f} MHz "
            stdscr.addstr(py, px + max(0, (pw - len(title)) // 2), title, curses.color_pair(cp) | curses.A_BOLD)
            stdscr.addstr(py + 1, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
        except: pass
        
        # Draw visible content lines
        for i in range(visible_rows):
            li = scroll + i
            if li >= len(display_lines):
                break
            t, line = display_lines[li]
            try:
                if t == "danger":
                    attr = curses.color_pair(CP_SUS_RED) | curses.A_BOLD
                elif t == "desc" or t == "art":
                    attr = curses.color_pair(CP_SUS_YEL)
                elif t == "dim":
                    attr = curses.color_pair(CP_DIM)
                else:
                    attr = curses.color_pair(CP_OK)
                stdscr.addstr(py + 2 + i, px + 1, line[:pw-2], attr)
            except: pass
        
        try:
            stdscr.addstr(py + ph - 3, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
            nav = " ↑↓:Scroll" if len(display_lines) > visible_rows else ""
            stdscr.addstr(py + ph - 2, px + 2, f"{nav}  e:Export  ESC/d:Close ", curses.color_pair(CP_DIM))
        except: pass
        
        stdscr.refresh()
        
        # Input
        stdscr.nodelay(False)
        stdscr.timeout(-1)
        key = stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)

        if key == curses.KEY_UP and scroll > 0:
            scroll -= 1
        elif key == curses.KEY_DOWN and scroll + visible_rows < len(display_lines):
            scroll += 1
        elif key in (ord('d'), ord('D'), 27, 10, 13):  # d, ESC, Enter
            return
        elif key == ord('e') or key == ord('E'):
            # Export to file
            try:
                export_dir = os.path.expanduser(_cfg['export']['path'])
                os.makedirs(export_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                freq_label = f"{f:.1f}".replace('.', 'p')
                filename = f"{ts}_{freq_label}MHz_signal.txt"
                filepath = os.path.join(export_dir, filename)
                with open(filepath, 'w') as ef:
                    ef.write(f"RfLord Signal Export — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    ef.write(f"{'=' * 50}\n\n")
                    for _, line in display_lines:
                        ef.write(line.strip() + "\n")
                    ef.write(f"\nRaw data: freq={signal['freq']}, peak={signal['peak']}, std={signal['std']}\n")
                # Flash confirmation
                try:
                    stdscr.addstr(py + ph - 2, px + 2, f" Exported: {filename} ", curses.color_pair(CP_OK) | curses.A_BOLD)
                    stdscr.refresh()
                    time.sleep(1.5)
                except: pass
            except Exception as ex:
                try:
                    stdscr.addstr(py + ph - 2, px + 2, f" Export failed: {ex} ", curses.color_pair(CP_SUS_RED))
                    stdscr.refresh()
                    time.sleep(1.5)
                except: pass

def _show_log_view(stdscr):
    """Show recent log entries in a popup."""
    h, w = stdscr.getmaxyx()
    log_path = os.path.join(LOG_DIR, 'rflord.log')
    lines = []
    try:
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            # Show last 30 lines, fit to screen width
            for ln in all_lines[-30:]:
                lines.append(ln.rstrip()[:w-4])
    except FileNotFoundError:
        lines = ["No log file found."]
    except Exception as ex:
        lines = [f"Error reading log: {ex}"]

    if not lines:
        lines = ["Log is empty."]

    # Build popup
    pw = min(w - 2, max(60, max(len(l) for l in lines) + 4))
    ph = min(h - 2, len(lines) + 5)
    py = max(0, (h - ph) // 2)
    px = max(0, (w - pw) // 2)
    scroll = max(0, len(lines) - (ph - 4))  # Start at bottom

    while True:
        # Draw popup background
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        try:
            title = " SYSTEM LOG (last 30 entries) "
            stdscr.addstr(py, px + max(0, (pw - len(title)) // 2), title, curses.color_pair(CP_SUS_YEL) | curses.A_BOLD)
            stdscr.addstr(py + 1, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
        except: pass

        # Show visible lines
        visible = ph - 4
        for i in range(visible):
            idx = scroll + i
            if idx < len(lines):
                try:
                    stdscr.addstr(py + 2 + i, px + 1, lines[idx][:pw-2], curses.color_pair(CP_DIM))
                except: pass

        try:
            stdscr.addstr(py + ph - 2, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
            stdscr.addstr(py + ph - 1, px + 2, " ↑↓:Scroll  ESC/l:Close ", curses.color_pair(CP_DIM))
        except: pass

        stdscr.refresh()

        # Input
        stdscr.nodelay(False)
        stdscr.timeout(-1)
        key = stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)

        if key in (ord('l'), ord('L'), 27, 10, 13):
            return
        elif key == curses.KEY_UP and scroll > 0:
            scroll -= 1
        elif key == curses.KEY_DOWN and scroll < max(0, len(lines) - visible):
            scroll += 1

def _show_history_view(stdscr, cursor_pos, signals, artemis_db, history):
    """Show signal history for the currently selected suspicious signal."""
    h, w = stdscr.getmaxyx()

    if not history:
        # Show "no history" popup
        msg = "Signal history is disabled in config."
        pw = len(msg) + 6
        ph = 5
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        try:
            stdscr.addstr(py + 1, px + 2, msg, curses.color_pair(CP_SUS_YEL))
            stdscr.addstr(py + 3, px + 2, " Press any key ", curses.color_pair(CP_DIM))
        except: pass
        stdscr.refresh()
        stdscr.nodelay(False)
        stdscr.timeout(-1)
        stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)
        return

    # Find the signal at cursor position
    sus_list = sorted([s for s in signals if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')],
                      key=lambda x: -severity_score(x['freq']/1e6, x['peak'], x['std'], classify(x['freq']/1e6, x['peak'], x['std'])))
    sus_grp = group_suspicious(sus_list, artemis_db)

    if not (0 <= cursor_pos < len(sus_grp)):
        msg = "No suspicious signal selected."
        pw = len(msg) + 6
        ph = 5
        py = max(0, (h - ph) // 2)
        px = max(0, (w - pw) // 2)
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        try:
            stdscr.addstr(py + 1, px + 2, msg, curses.color_pair(CP_SUS_YEL))
            stdscr.addstr(py + 3, px + 2, " Press any key ", curses.color_pair(CP_DIM))
        except: pass
        stdscr.refresh()
        stdscr.nodelay(False)
        stdscr.timeout(-1)
        stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)
        return

    g = sus_grp[cursor_pos]
    freq_mhz = g['freq'] / 1e6
    history_rows = history.get_history(freq_mhz, days=7)

    lines = [f"  History for {freq_mhz:.1f} MHz (last 7 days)", ""]
    if not history_rows:
        lines.append("  No history recorded yet.")
    else:
        lines.append(f"  {'Time':20s} {'Peak':>8s} {'Avg':>8s} {'Std':>6s} {'Class':8s}")
        lines.append(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*6} {'─'*8}")
        for r in history_rows[:25]:
            ts = datetime.fromtimestamp(r.get('scan_time', r.get('last_seen', 0)))
            time_str = ts.strftime('%Y-%m-%d %H:%M')
            peak = r.get('peak_dbfs', 0)
            avg = r.get('avg_dbfs', 0)
            std = r.get('std', 0)
            cls = r.get('classification', '?')
            lines.append(f"  {time_str:20s} {peak:>+7.1f} {avg:>+7.1f} {std:>5.1f} {cls:8s}")

    # Build popup
    pw = min(w - 2, max(60, max(len(l) for l in lines) + 4))
    ph = min(h - 2, len(lines) + 5)
    py = max(0, (h - ph) // 2)
    px = max(0, (w - pw) // 2)

    while True:
        for y in range(ph):
            try:
                stdscr.addstr(py + y, px, " " * pw, curses.color_pair(CP_HEADER))
            except: pass
        try:
            title = f" SIGNAL HISTORY — {freq_mhz:.1f} MHz "
            stdscr.addstr(py, px + max(0, (pw - len(title)) // 2), title, curses.color_pair(CP_SUS_YEL) | curses.A_BOLD)
            stdscr.addstr(py + 1, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
        except: pass

        for i, line in enumerate(lines[1:]):
            try:
                stdscr.addstr(py + 2 + i, px + 1, line[:pw-2], curses.color_pair(CP_DIM))
            except: pass

        try:
            stdscr.addstr(py + ph - 2, px + 1, "─" * (pw - 2), curses.color_pair(CP_SEP))
            stdscr.addstr(py + ph - 1, px + 2, " ESC/h:Close ", curses.color_pair(CP_DIM))
        except: pass

        stdscr.refresh()

        stdscr.nodelay(False)
        stdscr.timeout(-1)
        key = stdscr.getch()
        stdscr.nodelay(True)
        stdscr.timeout(200)

        if key in (ord('h'), ord('H'), 27, 10, 13):
            return

def _read_key(stdscr):
    """Read one key from curses. Returns command string or None."""
    global _cursor_active
    try:
        key = stdscr.getch()
        if key == -1:
            return None
        
        if key == ord('q') or key == ord('Q'):
            return 'quit'
        elif key == ord('r') or key == ord('R'):
            return 'rescan'
        elif key == ord('m') or key == ord('M'):
            return 'mute'
        elif key == ord('v') or key == ord('V'):
            return 'voice'
        elif key == ord('+') or key == ord('='):
            return 'interval_up'
        elif key == ord('-'):
            return 'interval_down'
        elif key == ord('s') or key == ord('S'):
            return 'suppress'
        elif key == curses.KEY_UP:
            _cursor_active = True
            return 'cursor_up'
        elif key == curses.KEY_DOWN:
            _cursor_active = True
            return 'cursor_down'
        elif key == curses.KEY_LEFT:
            _cursor_active = True
            return 'cursor_left'
        elif key == curses.KEY_RIGHT:
            _cursor_active = True
            return 'cursor_right'
        elif key == ord('d') or key == ord('D'):
            return 'details'
        elif key == ord('c') or key == ord('C'):
            return 'capture'
        elif key == ord('e') or key == ord('E'):
            return 'export'
        elif key == ord('l') or key == ord('L'):
            return 'log'
        elif key == ord('h') or key == ord('H'):
            return 'history'
        elif key == 27:  # ESC — deactivate cursor
            _cursor_active = False
            return 'cursor_off'
    except Exception as e:
        log.warning(f"_read_key exception: {e}")
        # Recover curses state WITHOUT clearing screen
        try:
            curses.cbreak()
            curses.noecho()
            stdscr.keypad(True)
            stdscr.nodelay(True)
            stdscr.timeout(200)
            stdscr.refresh()
        except: pass
    return None

def ensure_sink():
    try:
        r = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=3)
        sinks = r.stdout.strip()
        # If we have a real audio sink, make it default
        if "auto_null" in sinks:
            # Find the real sink name
            for line in sinks.split('\n'):
                parts = line.split('\t')
                if len(parts) >= 2 and 'auto_null' not in parts[1]:
                    sink_name = parts[1]
                    subprocess.run(["pactl", "set-default-sink", sink_name],
                                   capture_output=True, timeout=3)
                    log.info(f"Audio: set default sink to {sink_name}")
                    return
            # No real sink found — try to load ALSA
            subprocess.run(["pactl", "load-module", "module-alsa-sink", "device=hw:0,0"],
                           capture_output=True, timeout=3)
        # Verify current default
        r2 = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3)
        log.info(f"Audio: default sink = {r2.stdout.strip()}")
    except Exception as e:
        log.warning(f"ensure_sink exception: {e}")

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
    return best

def get_signal_type(freq_mhz, bw, pmr, std, artemis_db=None):
    """Classify signal type. Military rules first, then Artemis, then fallback."""
    
    # Military UHF band — hardcoded rules take priority over Artemis
    if 225 <= freq_mhz <= 400:
        if 255 <= freq_mhz <= 267: return "Link-11"
        if 270 <= freq_mhz <= 285: return "Link-11"
        if 300 <= freq_mhz <= 330: return "Mil/Enc"
        if 243 <= freq_mhz <= 244: return "Milstar"
        if 264 <= freq_mhz <= 266: return "Gonets"
        if std > 3: return "Link-11"
        if std < 2: return "Mil/Enc"
        return "Mil/Enc"
    
    # Use new identify_signal_type for common signals
    sig_type = identify_signal_type(freq_mhz, -50, std)  # Use dummy power
    
    # Override with more specific classifications
    if 240 <= freq_mhz <= 242: return "DAB"
    elif 235 <= freq_mhz <= 238: return "DAB+"
    elif 390 <= freq_mhz <= 400: return "TETRA"
    elif 337 <= freq_mhz <= 362: return "Keyfob"
    
    # Check Artemis database for non-military bands
    if artemis_db:
        art_entry = identify_signal(freq_mhz, artemis_db)
        if art_entry:
            return art_entry['name'][:18]
    
    # Known real signals (continued)
    if 140 <= freq_mhz <= 150 and std < 2:
        return "Mil/Enc"
    elif 150 <= freq_mhz <= 174 and std < 2:
        return "Mil/Enc"
    elif 174 <= freq_mhz <= 230:
        return "DAB+"
    elif 230 <= freq_mhz <= 285:
        return "Display Port"
    elif 470 <= freq_mhz <= 790:
        if std > 3:
            return "DVB-T2"
        elif std < 2:
            return "CAM-DTV?"
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
        return "FPV"
    elif 5150 <= freq_mhz <= 5725:
        return "WiFi 5GHz"
    elif 5725 <= freq_mhz <= 5875:
        if std < 3:
            return "FPV/WiFi"
        else:
            return "WiFi 5GHz"
    elif 2400 <= freq_mhz <= 2500:
        return "WiFi/BT"
    elif 1200 <= freq_mhz <= 1400 and std < 2:
        return "SPY-CAM"
    elif std < 2: return "CW"
    elif pmr > 8: return "Digital"
    elif pmr > 4: return "Bursty"
    else: return "Analog"

def ensure_decoded_dir():
    os.makedirs(os.path.join(DECODED_DIR, "screenshots"), exist_ok=True)
    os.makedirs(os.path.join(DECODED_DIR, "audio"), exist_ok=True)

def cleanup_old_decoded():
    cutoff = time.time() - (MAX_AGE_DAYS * 86400)
    for subdir in ["screenshots", "audio"]:
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
        log.info(f"VOICE CAPTURE: {freq_mhz:.1f} MHz, capturing IQ...")
        r = subprocess.run(["hackrf_transfer", "-r", raw, "-f", str(freq_hz),
                        "-s", "2000000", "-n", "4000000", "-l", "32", "-g", "40", "-a", "1"],
                       capture_output=True, timeout=10)
        if r.returncode != 0:
            log.warning(f"VOICE CAPTURE FAIL: hackrf_transfer rc={r.returncode} stderr={r.stderr[:200]}")
            return
        if not os.path.exists(raw) or os.path.getsize(raw) < 1000:
            log.warning(f"VOICE CAPTURE FAIL: raw file missing or too small ({os.path.getsize(raw) if os.path.exists(raw) else 0} bytes)")
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
        log.info(f"DECODED: {freq_mhz:.1f} MHz, type={sig_type}, playing audio...")
        save_decoded_audio(freq_mhz, wav, sig_type)
        # Cross-platform audio playback
        if IS_MACOS:
            r2 = subprocess.run(["afplay", wav], capture_output=True, timeout=10)
        else:
            ensure_sink()
            r2 = subprocess.run(["paplay", wav], capture_output=True, timeout=10)
        if r2.returncode != 0:
            log.warning(f"VOICE PLAY FAIL: rc={r2.returncode}")
        else:
            log.info(f"VOICE PLAY OK: {freq_mhz:.1f} MHz")
        os.unlink(wav)
    except Exception as e:
        log.warning(f"VOICE EXCEPTION: {freq_mhz:.1f} MHz: {e}")

def is_camera_signal(freq_mhz, std, sig_type=""):
    """Check if signal is a hidden camera or FPV video transmitter."""
    # By signal type label
    if sig_type in ("CAM?", "SPY-CAM", "CAM-DTV?", "FPV?", "WiFi/FPV",
                    "Hidden Camera 900MHz", "Hidden Camera 1.2GHz",
                    "Hidden Camera 5.8GHz", "WiFi 6E Camera 6GHz",
                    "FPV Video TX 5.8GHz", "FPV Video TX 70cm",
                    "FPV Video TX 1.2GHz", "WiFi Spy Camera",
                    "WiFi 5GHz Spy Camera", "WiFi 6E Spy Camera"):
        return True
    # By frequency + low std (narrowband continuous carrier = video TX)
    if std < 3:
        if 900 <= freq_mhz <= 928: return True
        if 1080 <= freq_mhz <= 1300: return True
        if 1200 <= freq_mhz <= 1400: return True
        if 470 <= freq_mhz <= 790: return True
        if 5725 <= freq_mhz <= 5875: return True
        if 2410 <= freq_mhz <= 2483: return True
    return False

def try_fpv_decode(freq_mhz):
    """Try to decode FPV/spy camera video signal and save a screenshot."""
    try:
        # Only try for known camera/FPV bands
        is_camera = (900 <= freq_mhz <= 928) or (1080 <= freq_mhz <= 1300) or \
                    (1200 <= freq_mhz <= 1400) or (2400 <= freq_mhz <= 2483) or \
                    (470 <= freq_mhz <= 790) or (5725 <= freq_mhz <= 5875)
        if not is_camera:
            return None
        ensure_decoded_dir()
        ts = time.strftime("%Y%m%d_%H%M%S")
        freq_label = f"{freq_mhz:.1f}".replace('.', 'p')
        out_file = os.path.join(DECODED_DIR, "screenshots", f"{ts}_{freq_label}MHz_cam.png")
        log.info(f"CAMERA SCREENSHOT: attempting capture at {freq_mhz:.1f} MHz")
        # Use fpv_decode.py to capture and decode a video frame
        r = run_cmd(f"python3 {os.path.dirname(__file__)}/fpv_decode.py capture "
                    f"--freq {freq_mhz} --auto --output {out_file} --duration 2", timeout=20)
        if r:
            log.info(f"CAMERA SCREENSHOT: result={r[:200]}")
        if os.path.exists(out_file):
            log.info(f"CAMERA SCREENSHOT: saved {out_file}")
            return out_file
        # Check for green variant
        green_file = out_file.replace('.png', '_green.png')
        if os.path.exists(green_file):
            log.info(f"CAMERA SCREENSHOT: saved {green_file}")
            return green_file
        log.info(f"CAMERA SCREENSHOT: no frame at {freq_mhz:.1f} MHz")
    except Exception as e:
        log.warning(f"CAMERA SCREENSHOT EXCEPTION: {freq_mhz:.1f} MHz: {e}")
    return None

def try_voice_decode(freq_mhz):
    try:
        voice_script = os.path.join(os.path.dirname(__file__), "voice_decode.py")
        if not os.path.exists(voice_script):
            log.debug(f"VOICE DECODE: voice_decode.py not found at {voice_script}")
            return None
        cmd = f"python3 {voice_script} scan {freq_mhz} --duration 3 2>&1"
        log.info(f"VOICE DECODE: running {cmd}")
        r = run_cmd(cmd, timeout=15)
        log.info(f"VOICE DECODE: result={r[:200] if r else '(empty)'}")
        # Play voice audio whenever a voice signal is detected
        has_voice = False
        if r:
            voice_indicators = ["DMR", "D-STAR", "NFM", "AM", "POCSAG", "DTMF", "Morse",
                                "Analog", "voice", "Power"]
            has_voice = any(ind in r for ind in voice_indicators)
        if has_voice:
            log.info(f"VOICE DECODE: {freq_mhz:.1f} MHz has voice signal, playing sample")
            play_voice_sample(freq_mhz)
        else:
            log.info(f"VOICE DECODE: {freq_mhz:.1f} MHz no voice detected (skip play)")
        if r and "DMR" in r: return "DMR digital voice"
        if r and "D-STAR" in r: return "D-STAR ham radio"
        if r and "NFM" in r and "Power" in r:
            if "Analog NFM" in r: return "FM voice radio"
        if r and "AM" in r and "Air band" in r: return "AM aviation radio"
        if r and "POCSAG" in r: return "POCSAG pager"
        if r and "DTMF" in r: return "DTMF tones"
        if r and "Morse" in r: return "Morse code"
    except Exception as e:
        log.warning(f"VOICE DECODE EXCEPTION: {freq_mhz:.1f} MHz: {e}")
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

def severity_score(freq_mhz, peak_dbfs, std, classify_result):
    """Higher score = more severe/important. Combines priority, strength, classification."""
    # Defensive: ensure numeric types (guards against dict/None from corrupted data)
    try:
        freq_mhz = float(freq_mhz)
        peak_dbfs = float(peak_dbfs)
        std = float(std)
    except (TypeError, ValueError):
        return 0
    pri = signal_priority(freq_mhz, std)
    # Priority weight: military(0) -> 100, spy(1) -> 50, other(2) -> 10
    pri_weight = {0: 100, 1: 50, 2: 10}.get(pri, 10)
    # Strength weight: normalize power (stronger = higher)
    strength_weight = max(0, peak_dbfs + 80)
    # Classification bonus
    cls_bonus = {"danger": 30, "sus": 10, "ok": 0}.get(classify_result, 0)
    return pri_weight + strength_weight + cls_bonus

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

def draw_table(stdscr, signals, start_time, last_seen, alert_count, artemis_db, known_freqs=None, voice_enabled=True, history=None, web_url=None, assessment=None):
    """Draw split-screen table: suspicious left, known right. Fully dynamic layout."""
    global _cursor_pos, _cursor_active, _cursor_panel
    if known_freqs is None:
        known_freqs = {}
    
    # Get CURRENT terminal size (resizes dynamically)
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    
    # Full width, no caps
    W = w
    
    suspicious = sorted([s for s in signals if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")],
                        key=lambda x: -severity_score(x["freq"]/1e6, x["peak"], x["std"], classify(x["freq"]/1e6, x["peak"], x["std"])))
    sus_grouped = group_suspicious(suspicious, artemis_db)
    
    ok = sorted([s for s in signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger') and s['peak'] > -65],
                key=lambda x: x['peak'], reverse=True)
    ok_grouped = group_signals_by_type(ok, artemis_db)
    
    # Clamp cursor
    if _cursor_active:
        active_list = sus_grouped if _cursor_panel == 'sus' else ok_grouped
        if len(active_list) > 0:
            _cursor_pos = max(0, min(_cursor_pos, len(active_list) - 1))
        else:
            _cursor_pos = 0
    else:
        _cursor_pos = 0

    elapsed = int(time.time() - start_time)
    uh, um, us = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    
    # Panel split: 60% left, 40% right
    L = max(50, int(W * 0.6))  # Left panel width
    R = W - L                   # Right panel width
    row = 0
    
    # === HEADER (full width) ===
    header = f" RfLord {VERSION} {time.strftime('%H:%M:%S')} │ Up {uh:02d}:{um:02d}:{us:02d} │ Alerts {alert_count} │ Tracked {len(known_freqs)} │ Sig {len(signals)} │ Author: Ihor Kolodyuk"
    try:
        stdscr.addstr(row, 0, header[:W].ljust(W), curses.color_pair(CP_HEADER) | curses.A_BOLD)
    except: pass
    row += 1

    if web_url:
        try:
            stdscr.addstr(row, 0, f" Web Dashboard: {web_url}"[:W].ljust(W), curses.color_pair(CP_OK) | curses.A_BOLD)
        except: pass
        row += 1

    if assessment and assessment.has_threats:
        icon = {0: "🔴", 1: "🟠", 2: "🟡", 3: "🟢"}.get(assessment.max_threat_level, "🟢")
        try:
            color = CP_DANGER if assessment.max_threat_level <= 1 else CP_SUS_YEL
            stdscr.addstr(row, 0, f" {icon} {assessment.summary}"[:W].ljust(W), curses.color_pair(color) | curses.A_BOLD)
        except: pass
        row += 1

    # === COLUMN TITLES (full width) ===
    try:
        stdscr.addstr(row, 0, f" {'SUSPICIOUS':^{L-2}}"[:L].ljust(L), curses.color_pair(CP_SUS_RED) | curses.A_BOLD)
        stdscr.addstr(row, L, f" {'KNOWN SIGNALS':^{R-2}}"[:R].ljust(R), curses.color_pair(CP_OK) | curses.A_BOLD)
    except: pass
    row += 1
    
    # === SUB-HEADERS (full width, dynamic columns) ===
    try:
        left_hdr = f" !  {'Freq':>7}  {'Pwr':>6}  {'Std':>5}  {'Dist':>6}  {'Type':<18} Description"
        stdscr.addstr(row, 0, left_hdr[:L].ljust(L), curses.color_pair(CP_DIM))
        right_hdr = f" {'Cnt':>4}  {'Pwr':>6}  {'Dist':>6}  {'Bnd':>5}  Type"
        stdscr.addstr(row, L, right_hdr[:R].ljust(R), curses.color_pair(CP_DIM))
    except: pass
    row += 1
    
    # === SEPARATOR (full width) ===
    try:
        stdscr.addstr(row, 0, ("─" * L)[:L], curses.color_pair(CP_SEP))
        stdscr.addstr(row, L, ("─" * R)[:R], curses.color_pair(CP_SEP))
    except: pass
    row += 1
    
    # === DATA ROWS ===
    avail = h - row - 2
    
    for i in range(avail):
        if row >= h - 2: break
        
        # Left panel — suspicious
        if i < len(sus_grouped):
            g = sus_grouped[i]
            cls = g['classify']
            cp = CP_DANGER if cls == "danger" else CP_SUS_RED
            sev = '!!!' if cls == 'danger' else ('!!' if cls == 'sus' else '! ')
            
            # Description fills remaining space
            fixed = 50  # chars for fixed columns
            desc_w = max(5, L - fixed)
            desc = g.get('remark', '')[:desc_w]
            
            cursor = '▸' if (_cursor_active and _cursor_panel == 'sus' and i == _cursor_pos) else ' '
            line = f"{cursor}{sev} {g['freq']:>7.1f}  {g['peak']:>+6.1f}  {g['std']:>5.1f}  {g['dist']:>6}  {g['type']:<18} {desc}"
            
            try:
                attr = curses.color_pair(cp) | curses.A_BOLD
                if _cursor_active and _cursor_panel == 'sus' and i == _cursor_pos:
                    attr |= curses.A_REVERSE
                stdscr.addstr(row, 0, line[:L].ljust(L), attr)
            except: pass
        
        # Right panel — known
        if i < len(ok_grouped):
            g = ok_grouped[i]
            cnt = f"x{g['count']}" if g['count'] > 1 else ""
            
            # Type fills remaining space
            fixed = 24
            type_w = max(5, R - fixed)
            type_str = g['type'][:type_w]
            
            cursor = '▸' if (_cursor_active and _cursor_panel == 'ok' and i == _cursor_pos) else ' '
            line = f"{cursor}{cnt:>4}  {g['peak']:>+6.1f}  {g['dist']:>6}  {g['band']:>5}  {type_str}"
            
            try:
                attr = curses.color_pair(CP_OK)
                if _cursor_active and _cursor_panel == 'ok' and i == _cursor_pos:
                    attr |= curses.A_REVERSE
                stdscr.addstr(row, L, line[:R].ljust(R), attr)
            except: pass
        
        row += 1
    
    # === FOOTER SEPARATOR (full width) ===
    try:
        stdscr.addstr(row, 0, ("─" * L)[:L], curses.color_pair(CP_SEP))
        stdscr.addstr(row, L, ("─" * R)[:R], curses.color_pair(CP_SEP))
    except: pass
    row += 1
    
    # === HOTKEY BAR (full width) ===
    extra = ""
    if len(sus_grouped) > avail: extra += f" | +{len(sus_grouped)-avail} sus"
    if len(ok_grouped) > avail: extra += f" | +{len(ok_grouped)-avail} ok"
    
    voice_str = "ON" if voice_enabled else "OFF"
    sup_str = "ON" if _suppress_active else "OFF"
    cur_str = ""
    if _cursor_active:
        if _cursor_panel == 'sus' and sus_grouped:
            cur_str = f" [SUS {_cursor_pos+1}/{len(sus_grouped)}]"
        elif _cursor_panel == 'ok' and ok_grouped:
            cur_str = f" [OK {_cursor_pos+1}/{len(ok_grouped)}]"
    
    keys = f" q:Quit r:Rescan c:Capture v:Voice({voice_str}) m:Mute s:Suppress({sup_str}) +/-:Interval({INTERVAL}s) ←→:Panel ↑↓:Nav d:Detail e:Export l:Log h:History{cur_str}{extra}"
    try:
        stdscr.addstr(row, 0, keys[:W].ljust(W), curses.color_pair(CP_DIM))
    except: pass
    
    stdscr.clrtobot()
    stdscr.refresh()

def main_curses(stdscr, devices):
    global INTERVAL, VOICE_THRESHOLD, _cursor_pos
    if isinstance(devices, str):
        devices = [devices]  # Backward compat
    device = devices[0]  # Primary device
    has_hackrf = "hackrf" in devices
    has_rtlsdr = "rtlsdr" in devices
    
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
    
    status = [f"SDR Initialized: {', '.join(devices).upper()}"]
    draw_splash(stdscr, device, status)
    
    ensure_sink()
    artemis_db = load_artemis()

    # Initialize new modules
    blacklist = load_blacklist(_cfg.get('blacklist', {}).get('file'))
    history = SignalHistory(_cfg['history']['db_path']) if _cfg['history']['enabled'] else None
    if history:
        history.init_db()
    accel = ScanAccelerator(_cfg.get('scan_acceleration', {}).get('skip_after_empty', 3)) if _cfg.get('scan_acceleration', {}).get('enabled', False) else None
    web_dash = None
    web_url = None
    if _cfg.get('web', {}).get('enabled', False):
        try:
            from web import WebDashboard
            web_port = _cfg['web']['port']
            web_dash = WebDashboard(port=web_port)
            web_dash.start()
            # Get device IP for URL display
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                web_url = f"http://{ip}:{web_port}"
            except:
                web_url = f"http://localhost:{web_port}"
            log.info(f"Web dashboard: {web_url}")
        except Exception as e:
            log.warning(f"Web dashboard failed: {e}")
    # State persistence
    state_file = os.path.expanduser('~/.local/share/rflord/state.json')
    os.makedirs(os.path.dirname(state_file), exist_ok=True)

    # SIGTERM handler for state save
    import json
    def save_state(*_):
        state = {'known_freqs': {str(k): v for k, v in known_freqs.items()},
                 'last_seen': {str(k): v for k, v in last_seen.items()},
                 'alert_count': alert_count}
        with open(state_file, 'w') as f:
            json.dump(state, f)
        if web_dash: web_dash.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, save_state)
    signal.signal(signal.SIGINT, save_state)

    db_count = len(artemis_db) if artemis_db else 0
    try:
        from spy_db import SPY_DEVICES
        spy_count = len(SPY_DEVICES)
    except: spy_count = 0
    try:
        from drone_rf_db import DRONE_SIGNATURES
        drone_count = len(DRONE_SIGNATURES)
    except: drone_count = 0
    proto_count = rfproto_count()
    total = db_count + spy_count + drone_count + proto_count
    status.append(f"Signatures databases loaded: OK ({total} total: {db_count} Artemis, {spy_count} spy, {drone_count} drone, {proto_count} RF protocols)")
    status.append("Initial scan & analysis: in progress")
    draw_splash(stdscr, device, status)

    # Initialize rule engine for unified threat assessment
    rule_engine = RuleEngine()
    wifi_iface = "wlan0"  # Default WiFi interface
    # Detect WiFi interface (cross-platform)
    try:
        if IS_MACOS:
            # macOS: en0 is typically WiFi
            wifi_iface = "en0"
        else:
            import glob as _glob
            wifi_ifaces = _glob.glob("/sys/class/net/wl*")
            if wifi_ifaces:
                wifi_iface = os.path.basename(wifi_ifaces[0])
    except: pass

    def _wifi_ble_scan_worker():
        """Background WiFi + BLE scan. Runs periodically."""
        try:
            wifi_events = RuleEngine.scan_wifi(wifi_iface, timeout=5)
            for e in wifi_events:
                rule_engine.add_wifi_device(mac=e.mac, ssid=e.ssid, rssi=e.rssi)
            if wifi_events:
                log.info(f"WiFi scan: {len(wifi_events)} devices")
        except Exception as ex:
            log.debug(f"WiFi scan skip: {ex}")
        try:
            ble_events = RuleEngine.scan_ble(timeout=3)
            for e in ble_events:
                rule_engine.add_ble_device(name=e.ble_name, uuid=e.ble_uuid, mfr_id=e.ble_mfr_id)
            if ble_events:
                log.info(f"BLE scan: {len(ble_events)} devices")
        except Exception as ex:
            log.debug(f"BLE scan skip: {ex}")

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

    # Try to restore state from previous session
    if os.path.exists(state_file):
        try:
            import json
            with open(state_file) as f:
                state = json.load(f)
            known_freqs = {int(k): v for k, v in state.get('known_freqs', {}).items()}
            last_seen = {int(k): v for k, v in state.get('last_seen', {}).items()}
            alert_count = state.get('alert_count', 0)
            os.unlink(state_file)
        except: pass

    # Kill any stale hackrf processes from previous runs
    if has_hackrf:
        if IS_MACOS:
            subprocess.run(["killall", "hackrf_sweep", "hackrf_transfer"],
                           capture_output=True, timeout=5)
        else:
            subprocess.run(["sudo", "killall", "hackrf_sweep", "hackrf_transfer"],
                           capture_output=True, timeout=5)
        time.sleep(1)

    # Reset HackRF once at startup
    if has_hackrf:
        if IS_LINUX:
            try:
                subprocess.run(["sudo", "usbreset", "1d50:6089"], capture_output=True, timeout=5)
            except: pass
        time.sleep(3)

    # Enable non-blocking getch for hotkeys
    _key_cmd = None
    stdscr.nodelay(True)
    stdscr.timeout(200)  # getch() returns -1 after 200ms

    # Determine scan assignment: which device scans which bands
    def _assign_bands(bands, devices):
        """Split bands between devices. HackRF takes high bands, RTL-SDR takes low bands."""
        if len(devices) < 2:
            return {devices[0]: bands}
        # RTL-SDR: bands below 1000 MHz (good sensitivity at VHF/UHF)
        # HackRF: bands above 1000 MHz (better at microwave)
        rtlsdr_bands = [(f_lo, f_hi, bw, n) for f_lo, f_hi, bw, n in bands if f_hi <= 1000]
        hackrf_bands = [(f_lo, f_hi, bw, n) for f_lo, f_hi, bw, n in bands if f_lo >= 1000]
        # If no clean split, give HackRF everything and RTL-SDR the low half
        if not hackrf_bands:
            mid = len(bands) // 2
            rtlsdr_bands = bands[:mid]
            hackrf_bands = bands[mid:]
        if not rtlsdr_bands:
            rtlsdr_bands = hackrf_bands[:len(hackrf_bands)//2]
            hackrf_bands = hackrf_bands[len(hackrf_bands)//2:]
        assignment = {}
        if "hackrf" in devices and hackrf_bands:
            assignment["hackrf"] = hackrf_bands
        if "rtlsdr" in devices and rtlsdr_bands:
            assignment["rtlsdr"] = rtlsdr_bands
        return assignment

    first_scan_done = False
    while True:
      try:
        scan_num += 1
        log.info(f"=== Scan #{scan_num} started ===")

        # Periodic WiFi + BLE scan (every 5 scans, in background)
        if scan_num % 5 == 1:
            threading.Thread(target=_wifi_ble_scan_worker, daemon=True, name="rflord-wifi-ble").start()

        all_signals = []
        h, w = stdscr.getmaxyx()
        scan_bands = accel.get_active_bands(bands) if accel else bands

        # Dual-SDR: scan in parallel with threads
        band_assignment = _assign_bands(scan_bands, devices)

        if len(band_assignment) > 1:
            # Parallel scan with both SDRs
            total_bands = sum(len(b) for b in band_assignment.values())
            done = [0]
            results = {}

            def _scan_worker(dev, dev_bands, result_key):
                global _scan_status
                sigs = []
                for f_lo, f_hi, bw, n in dev_bands:
                    _scan_status = f" [{dev.upper()}] {f_lo}-{f_hi} MHz "
                    if dev == "rtlsdr":
                        output = rtlsdr_sweep(f_lo, f_hi)
                    else:
                        output = hackrf_sweep(f_lo, f_hi, bw, n)
                    sigs.extend(parse_sweep(output))
                    done[0] += 1
                results[result_key] = sigs

            threads = []
            for dev_i, (dev, dev_bands) in enumerate(band_assignment.items()):
                t = threading.Thread(target=_scan_worker, args=(dev, dev_bands, dev_i), daemon=True)
                threads.append(t)
                t.start()
            # Poll threads — draw status and read keys while scanning
            while any(t.is_alive() for t in threads):
                if _scan_status:
                    try:
                        stdscr.addstr(0, 0, _scan_status.ljust(w-1), curses.color_pair(CP_HEADER) | curses.A_BOLD)
                        stdscr.refresh()
                    except: pass
                key = _read_key(stdscr)
                if key == 'quit':
                    _suppress_stop()
                    return
                time.sleep(0.05)
            for sigs in results.values():
                all_signals.extend(sigs)
        else:
            # Single SDR — sequential scan
            for bi, (f_lo, f_hi, bw, n) in enumerate(scan_bands):
                try:
                    status_line = f" Scanning {f_lo}-{f_hi} MHz ({bi+1}/{len(scan_bands)})... "
                    stdscr.addstr(0, 0, status_line.ljust(w-1), curses.color_pair(CP_HEADER) | curses.A_BOLD)
                    stdscr.refresh()
                except: pass
                key = _read_key(stdscr)
                if key == 'quit':
                    _suppress_stop()
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

        # Blacklist filtering
        unique = filter_blacklisted(unique, blacklist)

        sus_count = len([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")])
        log.info(f"Scan #{scan_num}: {len(unique)} signals, {sus_count} suspicious")

        # Scan acceleration recording
        if accel:
            for f_lo, f_hi, bw, n in bands:
                band_signals = [s for s in unique if f_lo*1e6 <= s['freq'] <= f_hi*1e6]
                accel.record_band_result(f_lo, f_hi, len(band_signals))

        # History recording
        if history:
            history.record_scan(unique, device)

        # Web dashboard update — pass grouped data same as curses
        if web_dash:
            sus_list = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")],
                              key=lambda x: -severity_score(x["freq"]/1e6, x["peak"], x["std"], classify(x["freq"]/1e6, x["peak"], x["std"])))
            ok_list = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) not in ("sus", "danger") and s["peak"] > -65],
                             key=lambda x: x["peak"], reverse=True)
            sus_grouped = group_suspicious(sus_list, artemis_db)
            ok_grouped = group_signals_by_type(ok_list, artemis_db)
            # Format for web JS: {freq, power, std, distance, type, identification, count, band, category}
            web_sus = [{'freq': g['freq'] * 1e6, 'power': g['peak'], 'std': g['std'],
                        'distance': g['dist'], 'type': g['type'], 'identification': g.get('remark', ''),
                        'count': g['count'], 'category': 'suspicious'} for g in sus_grouped]
            web_ok = [{'freq': g['freq'] * 1e6, 'power': g['peak'], 'std': g['std'],
                       'distance': g['dist'], 'type': g['type'], 'identification': g.get('remark', ''),
                       'count': g['count'], 'band': g.get('band', '?'), 'category': 'known'} for g in ok_grouped]
            web_dash.update_signals(web_sus + web_ok, {
                'version': VERSION, 'alerts': alert_count, 'device': device,
                'sus_count': len(sus_grouped), 'ok_count': len(ok_grouped),
                'uptime': str(int(time.time() - start_time)),
            })

        # Rule engine: unified threat assessment (RF + WiFi + BLE)
        assessment = rule_engine.process_rf_scan(unique)
        if assessment.has_threats:
            for rm in assessment.rules_matched:
                sources = '+'.join(sorted(rm.source_types))
                log.warning(f"THREAT: {rm.rule_name} [{sources}] ({rm.confidence:.0%}) — {rm.description}")
                if rm.rule_name not in known_freqs:
                    known_freqs[rm.rule_name] = time.time()
                    alert_count += 1
            # Voice alert for threats (threaded — non-blocking)
            voice_msg = rule_engine.format_voice_alert(assessment)
            if voice_msg and voice_enabled:
                speak(voice_msg)

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
        
        # First scan: clear screen completely to fix splash-to-table transition
        if not first_scan_done:
            stdscr.clear()
            stdscr.refresh()
        draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
        
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
        
        # Camera screenshot: capture for ALL detected hidden camera/FPV signals
        # with cooldown to avoid capturing same frequency too often
        if not hasattr(main_curses, '_last_capture'):
            main_curses._last_capture = {}
        CAMERA_COOLDOWN = 120  # seconds between captures of same frequency
        
        all_camera_signals = [s for s in unique
                              if is_camera_signal(s['freq']/1e6, s['std'],
                                                  get_signal_type(s['freq']/1e6, 0, 0, s['std'], artemis_db))]
        # Filter by cooldown
        now_ts = time.time()
        camera_signals = []
        for s in all_camera_signals:
            freq_key = round(s['freq'] / 1e6)
            last = main_curses._last_capture.get(freq_key, 0)
            if now_ts - last > CAMERA_COOLDOWN:
                camera_signals.append(s)
                main_curses._last_capture[freq_key] = now_ts
        
        if camera_signals:
            def _camera_worker(sigs):
                for s in sigs:
                    f = s['freq'] / 1e6
                    spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                    label = spy_name or get_signal_type(f, 0, 0, s['std'], artemis_db)
                    log.warning(f"HIDDEN CAMERA: {label} at {f:.1f} MHz, peak={s['peak']:.1f} dBFS — capturing screenshot")
                    screenshot = try_fpv_decode(f)
                    if screenshot:
                        log.warning(f"HIDDEN CAMERA: screenshot saved {screenshot}")
                    else:
                        log.info(f"HIDDEN CAMERA: no video frame at {f:.1f} MHz")
            threading.Thread(target=_camera_worker, args=(camera_signals,),
                             daemon=True, name="rflord-camera").start()
        
        # Voice alert
        if new_suspicious and voice_enabled:
            log.info(f"Voice alert: {len(new_suspicious)} new suspicious, threshold={VOICE_THRESHOLD}")
            new_suspicious.sort(key=lambda x: x['peak'], reverse=True)
            above_threshold = [s for s in new_suspicious if s['peak'] > VOICE_THRESHOLD]
            log.info(f"Voice: {len(above_threshold)} above threshold {VOICE_THRESHOLD} dBFS")
            
            if above_threshold:
                announcements = []
                for s in above_threshold[:4]:
                    f = s['freq'] / 1e6
                    dist = est_distance(f, s['peak'])
                    sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                    # Military UHF band — skip Artemis (overly broad entries)
                    if 225 <= f <= 400:
                        spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                        if spy_name:
                            announcements.append(f"WARNING! {spy_name} detected at {f:.0f} megahertz, about {speak_distance(dist)}")
                        else:
                            announcements.append(f"{f:.0f} megahertz, {sig_type}, about {speak_distance(dist)}")
                    else:
                        artemis_entry = identify_signal(f, artemis_db) if artemis_db else None
                        if artemis_entry:
                            name = artemis_entry.get('description', '') or artemis_entry.get('name', '')
                            announcements.append(f"{f:.0f} megahertz, identified as {name}, about {speak_distance(dist)}")
                        else:
                            spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                            if spy_name:
                                announcements.append(f"WARNING! {spy_name} detected at {f:.0f} megahertz, about {speak_distance(dist)}")
                            else:
                                announcements.append(f"{f:.0f} megahertz, {sig_type}, about {speak_distance(dist)}")
                
                # Voice decode + play in background thread (blocks 15+ seconds)
                def _voice_worker():
                    vr = None
                    for s in above_threshold:
                        f = s['freq'] / 1e6
                        sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                        # Skip camera signals — handled by camera worker
                        if is_camera_signal(f, s['std'], sig_type):
                            continue
                        if s['std'] < 6:
                            vr = try_voice_decode(f)
                            break
                    # If no voice found, try analog playback for non-camera signals
                    if not vr:
                        for s in above_threshold:
                            f = s['freq'] / 1e6
                            sig_type = get_signal_type(f, 0, 0, s['std'], artemis_db)
                            if is_camera_signal(f, s['std'], sig_type):
                                continue
                            if sig_type == "Analog" and s['std'] < 4:
                                play_voice_sample(f)
                                vr = "analog voice sample, saved to decoded folder"
                                break
                threading.Thread(target=_voice_worker, daemon=True, name="rflord-voice").start()
                
                count = len(above_threshold)
                if count == 1:
                    msg = f"Alert. New signal at {announcements[0]}."
                elif count == 2:
                    msg = f"Alert. Two new signals. First at {announcements[0]}. Second at {announcements[1]}."
                else:
                    msg = f"Alert. {count} new signals above threshold. Strongest at {announcements[0]}."
                    if count > 2:
                        msg += f" Also at {announcements[1]}."
                
                speak(msg)
            else:
                s0 = new_suspicious[0]
                f0 = s0['freq'] / 1e6
                dist = est_distance(f0, s0['peak'])
                speak(f"{len(new_suspicious)} new weak signals. Strongest at {f0:.0f} megahertz, about {speak_distance(dist)}, below threshold.")
        
        # Startup voice + periodic summary every 5 scans
        elif voice_enabled and (scan_num == 1 or scan_num % 5 == 0):
            sus_count = len([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')])
            if sus_count > 0:
                speak(f"Status update. Scan {scan_num}. {len(unique)} signals tracked. {sus_count} suspicious.")
        
        # Refresh table after voice alerts
        draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
        
        # Wait with non-blocking key reads (200ms timeout per getch)
        wait_end = time.time() + INTERVAL
        while time.time() < wait_end:
            key = _read_key(stdscr)
            if key == 'quit':
                _suppress_stop()
                return
            elif key == 'rescan':
                break
            elif key == 'mute':
                voice_enabled = not voice_enabled
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'voice':
                sus_count = len([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')])
                if voice_enabled:
                    speak(f"Scan complete. {len(unique)} signals found. {sus_count} suspicious.")
            elif key == 'interval_up':
                INTERVAL = min(600, INTERVAL + 30)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'interval_down':
                INTERVAL = max(30, INTERVAL - 30)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'suppress':
                _show_suppress_menu(stdscr)
                any_active = any(_suppress_targets.get(n, False) for n in SUPPRESS_TARGETS)
                if any_active and has_hackrf:
                    _suppress_active = True
                    _suppress_start()
                else:
                    _suppress_active = False
                    _suppress_stop()
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'cursor_up':
                _cursor_pos = max(0, _cursor_pos - 1)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'cursor_down':
                # Get max count for active panel
                sus_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')],
                                  key=lambda x: -severity_score(x['freq']/1e6, x['peak'], x['std'], classify(x['freq']/1e6, x['peak'], x['std'])))
                sus_grp = group_suspicious(sus_list, artemis_db)
                ok_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger') and s['peak'] > -65],
                                 key=lambda x: x['peak'], reverse=True)
                ok_grp = group_signals_by_type(ok_list, artemis_db)
                max_pos = len(sus_grp) - 1 if _cursor_panel == 'sus' else len(ok_grp) - 1
                _cursor_pos = min(max_pos, _cursor_pos + 1)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'cursor_left':
                _cursor_panel = 'sus'
                _cursor_pos = 0
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'cursor_right':
                _cursor_panel = 'ok'
                _cursor_pos = 0
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'cursor_off':
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'details':
                # Get the selected signal from active panel
                sus_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')],
                                  key=lambda x: -severity_score(x['freq']/1e6, x['peak'], x['std'], classify(x['freq']/1e6, x['peak'], x['std'])))
                sus_grp = group_suspicious(sus_list, artemis_db)
                ok_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger') and s['peak'] > -65],
                                 key=lambda x: x['peak'], reverse=True)
                ok_grp = group_signals_by_type(ok_list, artemis_db)
                active_grp = sus_grp if _cursor_panel == 'sus' else ok_grp
                if 0 <= _cursor_pos < len(active_grp):
                    g = active_grp[_cursor_pos]
                    signal = g.get('_strongest', g)
                    _show_signal_detail(stdscr, signal, artemis_db)
                    draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'capture':
                # Capture screenshot from selected signal (camera/FPV)
                sus_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')],
                                  key=lambda x: -severity_score(x['freq']/1e6, x['peak'], x['std'], classify(x['freq']/1e6, x['peak'], x['std'])))
                sus_grp = group_suspicious(sus_list, artemis_db)
                ok_list = sorted([s for s in unique if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger') and s['peak'] > -65],
                                 key=lambda x: x['peak'], reverse=True)
                ok_grp = group_signals_by_type(ok_list, artemis_db)
                active_grp = sus_grp if _cursor_panel == 'sus' else ok_grp
                if 0 <= _cursor_pos < len(active_grp):
                    g = active_grp[_cursor_pos]
                    signal = g.get('_strongest', g)
                    f = signal['freq'] / 1e6
                    # Show status
                    try:
                        h, w = stdscr.getmaxyx()
                        status = f" Capturing screenshot at {f:.1f} MHz... "
                        stdscr.addstr(0, 0, status.ljust(w-1), curses.color_pair(CP_SUS_RED) | curses.A_BOLD)
                        stdscr.refresh()
                    except: pass
                    # Capture in background thread
                    def _capture_worker(freq):
                        try:
                            screenshot = try_fpv_decode(freq)
                            if screenshot:
                                log.warning(f"CAPTURE: screenshot saved {screenshot}")
                                # Show result
                                try:
                                    h, w = stdscr.getmaxyx()
                                    msg = f" Screenshot saved: {os.path.basename(screenshot)} "
                                    stdscr.addstr(0, 0, msg.ljust(w-1), curses.color_pair(CP_OK) | curses.A_BOLD)
                                    stdscr.refresh()
                                except: pass
                            else:
                                log.info(f"CAPTURE: no video frame at {freq:.1f} MHz")
                                try:
                                    h, w = stdscr.getmaxyx()
                                    msg = f" No video signal found at {freq:.1f} MHz "
                                    stdscr.addstr(0, 0, msg.ljust(w-1), curses.color_pair(CP_SUS_YEL) | curses.A_BOLD)
                                    stdscr.refresh()
                                except: pass
                        except Exception as e:
                            log.warning(f"CAPTURE exception: {e}")
                    threading.Thread(target=_capture_worker, args=(f,), daemon=True, name="rflord-capture").start()
            elif key == 'export':
                export_dir = os.path.expanduser(_cfg['export']['path'])
                os.makedirs(export_dir, exist_ok=True)
                ts = time.strftime('%Y%m%d_%H%M%S')
                fmt = _cfg['export']['format']
                filepath = os.path.join(export_dir, f'scan_{ts}.{fmt}')
                if fmt == 'csv':
                    export_csv(filepath, unique)
                else:
                    export_json(filepath, unique)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'log':
                _show_log_view(stdscr)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
            elif key == 'history':
                _show_history_view(stdscr, _cursor_pos, unique, artemis_db, history)
                draw_table(stdscr, unique, start_time, last_seen, alert_count, artemis_db, known_freqs, voice_enabled, history, web_url, assessment)
      except Exception as e:
        log.warning(f"Main loop exception: {e}")
        try:
            _, ww = stdscr.getmaxyx()
            stdscr.addstr(0, 0, f"ERROR: {e}  Press 'q' to quit".ljust(ww-1), curses.color_pair(CP_SUS_RED) | curses.A_BOLD)
            stdscr.refresh()
        except: pass
        time.sleep(2)

# === LOGGING WITH WEEKLY ROTATION ===
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = "/Users/ihorman/sdr_captures/rflord_logs"
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
    devices = detect_device()
    if not devices:
        print("No SDR device found.")
        sys.exit(1)
    device = devices[0]  # Primary device for backward compat
    print(f"SDR devices: {', '.join(devices)}", flush=True)

    # Set ESCDELAY for faster escape sequence handling (default is 1000ms!)
    os.environ['ESCDELAY'] = '0'
    # Also try to set it via curses API if available
    try:
        curses.set_escdelay(0)
    except:
        pass

    # Try curses first (proper terminal), fallback to ANSI
    try:
        if sys.stdout.isatty():
            curses.wrapper(main_curses, devices)
        else:
            # Non-TTY: use ANSI mode
            main_ansi(devices)
    except Exception as e:
        print(f"Curses failed: {e}", flush=True)
        main_ansi(devices)

def main_ansi(device=None):
    """ANSI fallback mode for non-TTY or when curses fails."""
    global INTERVAL, VOICE_THRESHOLD
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--interval" and i + 2 <= len(sys.argv):
            INTERVAL = int(sys.argv[i + 2])
        if arg == "--threshold" and i + 2 <= len(sys.argv):
            VOICE_THRESHOLD = int(sys.argv[i + 2])
    
    if not device:
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
    # Kill stale hackrf processes + reset HackRF
    if device == "hackrf":
        if IS_MACOS:
            subprocess.run(["killall", "hackrf_sweep", "hackrf_transfer"],
                           capture_output=True, timeout=5)
        else:
            subprocess.run(["sudo", "killall", "hackrf_sweep", "hackrf_transfer"],
                           capture_output=True, timeout=5)
        time.sleep(1)
        if IS_LINUX:
            try:
                subprocess.run(["sudo", "usbreset", "1d50:6089"], capture_output=True, timeout=5)
            except: pass
        time.sleep(3)

    
    while True:
        scan_num += 1
        log.info(f"=== Scan #{scan_num} started ===")
        
        
        all_signals = []
        for f_lo, f_hi, bw, n in bands:
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
        sus_count = len([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")])
        log.info(f"Scan #{scan_num}: {len(unique)} signals, {sus_count} suspicious")
        ok = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) not in ("sus", "danger") and s["peak"] > -65],
                    key=lambda x: x["peak"], reverse=True)
        ok_grouped = group_signals_by_type(ok, artemis_db)
        
        suspicious = sorted([s for s in unique if classify(s["freq"]/1e6, s["peak"], s["std"]) in ("sus", "danger")],
                           key=lambda x: (-x['peak'],))
        sus_grouped = group_suspicious(suspicious, artemis_db)

        
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
        
        # Sub-headers — use same format specifiers as data
        left_hdr = f"{'Cnt':>4} {'Freq':>5} {'Pwr':>5} {'Std':>4} {'Dist':>5} {'Type':<18} Remark"
        right_hdr = f"{'Cnt':>4} {'Pwr':>6} {'Dist':>5} {'Bnd':>4} {'Type':<25}"
        print(f"{D} {left_hdr}  {right_hdr}{N}")
        
        # Separator
        print(f"{D} {'─'*(mid-2)} {'─'*38}{N}")
        
        # Data rows — STRICT limit to 24 lines total
        # Total lines: header(1) + titles(1) + sub(1) + sep(1) + data + footer(1) = 5 + data
        # For 24-line terminal: data = 19 rows max
        max_rows = 19
        for i in range(max_rows):
            left = ""
            right = ""
            
            if i < len(sus_grouped):
                g = sus_grouped[i]
                cnt = f"x{g['count']}" if g['count'] > 1 else ""
                c = R if g['classify'] == 'danger' else Y
                remark_w = max(12, mid - 42)
                remark = g['remark'][:remark_w]
                left = f"{c}{cnt:>4} {g['freq']:>5.1f} {g['peak']:>+5.1f} {g['std']:>4.1f} {g['dist']:>5} {g['type']:<18} {remark}{N}"
            
            if i < len(ok_grouped):
                g = ok_grouped[i]
                cnt = f"x{g['count']}" if g['count'] > 1 else ""
                type_str = g['type'][:25]
                right = f"{G}{cnt:>4} {g['peak']:>+6.1f} {g['dist']:>5} {g['band']:>4} {type_str}{N}"
            
            if left or right:
                left_pad = f" {left:<{mid - 1 + len(R) + len(N)}}"
                print(f"{left_pad} {right}")
        
        # Footer
        extra = ""
        if len(sus_grouped) > max_rows: extra += f" +{len(sus_grouped)-max_rows} sus"
        if len(ok_grouped) > max_rows: extra += f" +{len(ok_grouped)-max_rows} ok"
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
                    # Military UHF band — skip Artemis (overly broad entries)
                    if 225 <= f <= 400:
                        spy_name, spy_icon, threat = identify_spy_device(f, s['std'])
                        if spy_name:
                            announcements.append(f"WARNING! {spy_name} detected at {f:.0f} megahertz, about {speak_distance(dist)}")
                        else:
                            announcements.append(f"{f:.0f} megahertz, {sig_type}, about {speak_distance(dist)}")
                    else:
                        artemis_entry = identify_signal(f, artemis_db) if artemis_db else None
                        if artemis_entry:
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
                speak(f"{len(new_suspicious)} new weak signals. Strongest at {f0:.0f} megahertz, about {speak_distance(dist)}, below threshold.")
        
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
