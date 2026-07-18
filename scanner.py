#!/usr/bin/env python3
"""
scan-radio: RF Spectrum Scanner with Artemis 3 Signal Identification
Auto-detects HackRF One or RTL-SDR, scans bands, matches against
Artemis 3 database (432 signatures), produces colorful terminal report.
"""

import subprocess
import sys
import os
import re
import math
from datetime import datetime

# Drone RF signature database
try:
    _drone_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drone_rf_db.py")
    import importlib.util
    _spec = importlib.util.spec_from_file_location("drone_rf_db", _drone_db_path)
    _drone_db = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_drone_db)
    DRONE_DB_AVAILABLE = True
except Exception:
    DRONE_DB_AVAILABLE = False

# RF analysis (distance estimation, territory classification)
try:
    _rf_analysis_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rf_analysis.py")
    _spec2 = importlib.util.spec_from_file_location("rf_analysis", _rf_analysis_path)
    _rf_analysis = importlib.util.module_from_spec(_spec2)
    _spec2.loader.exec_module(_rf_analysis)
    RF_ANALYSIS_AVAILABLE = True
except Exception:
    RF_ANALYSIS_AVAILABLE = False

# Sound alerts — voice announcements via edge-tts
import tempfile
TTS_VOICE = "en-US-SteffanNeural"
TTS_RATE = "+10%"

def speak(text):
    """Speak text using edge-tts and play via paplay."""
    try:
        wav = tempfile.mktemp(suffix='.mp3', prefix='scan_tts_')
        subprocess.run(["edge-tts", "--voice", TTS_VOICE, "--rate", TTS_RATE,
                        "--text", text, "--write-media", wav],
                       capture_output=True, timeout=10)
        if os.path.exists(wav):
            _ensure_audio_sink()
            subprocess.run(["paplay", wav], capture_output=True, timeout=15)
            os.unlink(wav)
    except Exception:
        pass

def play_alert(alert_type="suspicious", details=None):
    """Play voice alert. Types: suspicious, drone, clear."""
    try:
        if alert_type == "drone" and details:
            speak(f"Drone alert! {details}")
        elif alert_type == "suspicious" and details:
            speak(f"Warning. {details}")
        elif alert_type == "new" and details:
            speak(f"New signal detected. {details}")
        elif alert_type == "clear":
            speak("All clear. Environment clean.")
        elif alert_type == "drone":
            speak("Drone activity detected nearby!")
        elif alert_type == "suspicious":
            speak("Suspicious signals detected. Check report.")
    except Exception:
        pass

_audio_sink_ready = False
def _ensure_audio_sink():
    """Ensure PipeWire has a real ALSA audio sink, not auto_null."""
    global _audio_sink_ready
    if _audio_sink_ready:
        return
    try:
        result = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True, timeout=3)
        if "auto_null" in result.stdout and "alsa_output" not in result.stdout:
            subprocess.run(["pactl", "load-module", "module-alsa-sink", "device=hw:0,0"],
                           capture_output=True, timeout=3)
            subprocess.run(["pactl", "set-default-sink", "alsa_output.hw:0,0"],
                           capture_output=True, timeout=3)
        _audio_sink_ready = True
    except Exception:
        pass

# ANSI colors
RED = "\033[1;31m"
YEL = "\033[1;33m"
GRN = "\033[1;32m"
CYN = "\033[1;36m"
BLD = "\033[1m"
DIM = "\033[2m"
RST = "\033[0m"
MAG = "\033[1;35m"

ARTEMIS_DB = "/opt/artemis/Data/db.csv"

def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return ""

def detect_hardware():
    lsusb = run("lsusb")
    hackrf = "1d50:6089" in lsusb
    portapack = "1d50:6018" in lsusb
    rtlsdr = "0bda:2838" in lsusb

    if portapack and not hackrf:
        # Switch PortaPack to HackRF mode — must restore session first
        for port in ["/dev/ttyACM0", "/dev/ttyACM1"]:
            try:
                import serial, time
                s = serial.Serial(port, 115200, timeout=2)
                time.sleep(0.5)
                # Restore session first to get back to main menu
                s.write(b'restore\r\n')
                time.sleep(1.5)
                s.read(s.in_waiting or 500)
                # Now switch to HackRF mode
                s.write(b'hackrf\r\n')
                time.sleep(3)
                s.read(s.in_waiting or 500)
                s.close()
                break
            except Exception as e:
                pass
        lsusb = run("lsusb")
        hackrf = "1d50:6089" in lsusb

    if hackrf:
        return "hackrf", "HackRF One (1 MHz - 6 GHz)"
    elif rtlsdr:
        return "rtlsdr", "RTL-SDR (24 - 1766 MHz)"
    else:
        return None, "No SDR device found"

def parse_rtl_power(output):
    signals = []
    for line in output.strip().split('\n'):
        if not line.startswith('202'):
            continue
        parts = line.split(', ')
        if len(parts) < 7:
            continue
        try:
            freq_low = int(parts[2])
            freq_high = int(parts[3])
        except ValueError:
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

def parse_hackrf_sweep(output):
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
        except ValueError:
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

def load_artemis():
    db = []
    if not os.path.exists(ARTEMIS_DB):
        return db
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
                    'modulation': parts[3],
                    'bandwidth': parts[4],
                    'country': parts[6],
                    'description': parts[8][:100] if len(parts) > 8 else "",
                })
    return db

def match_artemis(freq_mhz, artemis_db):
    freq_hz = freq_mhz * 1e6
    matches = []
    for entry in artemis_db:
        tol = max((entry['freq_high'] - entry['freq_low']) * 0.1, 2_000_000)
        if (entry['freq_low'] - tol) <= freq_hz <= (entry['freq_high'] + tol):
            matches.append(entry)
    matches.sort(key=lambda x: x['freq_high'] - x['freq_low'])
    return matches[:3]

def get_band(freq_mhz):
    bands = [
        (88, 108, "FM Broadcast"),
        (108, 137, "Air Band"),
        (137, 144, "Military/Aerospace"),
        (144, 148, "2m Amateur"),
        (150, 174, "VHF Business"),
        (174, 230, "DVB-T Digital TV"),
        (300, 330, "UHF Military"),
        (400, 430, "UHF Public Safety"),
        (430, 450, "70cm Amateur"),
        (450, 470, "UHF Business/PMR"),
        (470, 608, "UHF TV"),
        (698, 806, "LTE/Cellular"),
        (806, 960, "Cellular/GSM"),
        (960, 1215, "ADS-B/GPS/L-Band"),
        (1215, 1700, "S-Band/Military"),
        (1700, 2000, "GSM 1800/3G"),
        (2000, 2200, "3G/4G"),
        (2400, 2500, "2.4 GHz WiFi/BT"),
        (2300, 2700, "4G LTE"),
        (5150, 5350, "5 GHz WiFi (low)"),
        (5470, 5730, "5 GHz WiFi (high)"),
        (5725, 5875, "5.8 GHz FPV/WiFi"),
    ]
    for lo, hi, name in bands:
        if lo <= freq_mhz <= hi:
            return name
    return "Unknown"

def classify_signal(freq_mhz, power, std, device):
    """Classify a signal as normal, notable, or suspicious"""
    band = get_band(freq_mhz)

    # Known normal signals
    # WiFi channels
    wifi_24 = [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472]
    for ch in wifi_24:
        if abs(freq_mhz - ch) < 3:
            return "normal", f"WiFi Ch {wifi_24.index(ch)+1}"

    # Bluetooth
    if 2402 <= freq_mhz <= 2480 and std > 3:
        return "normal", "Bluetooth"

    # GSM
    if 935 <= freq_mhz <= 960:
        return "normal", "GSM 900 downlink"
    if 1805 <= freq_mhz <= 1880:
        return "normal", "GSM 1800 downlink"

    # FM broadcast
    if 88 <= freq_mhz <= 108:
        return "normal", "FM Radio"

    # DVB-T
    if 174 <= freq_mhz <= 230:
        return "normal", "DVB-T TV"

    # ADS-B
    if 1089 <= freq_mhz <= 1091:
        return "normal", "ADS-B aircraft"

    # GPS
    if 1574 <= freq_mhz <= 1576:
        return "normal", "GPS L1"

    # Suspicious checks
    thresh = -25 if device == "hackrf" else -10

    # Continuous carrier in video bands
    if 900 <= freq_mhz <= 928 and std < 2 and power > (thresh - 10):
        return "suspicious", "Possible analog camera (900 MHz)"
    if 1080 <= freq_mhz <= 1300 and std < 2 and power > (thresh - 10):
        return "suspicious", "Possible spy camera / FPV (1.2 GHz)"
    if 2410 <= freq_mhz <= 2483 and std < 2 and power > (thresh - 10):
        return "suspicious", "Possible analog video TX (2.4 GHz)"
    if 5725 <= freq_mhz <= 5875 and std < 2 and power > (thresh - 10):
        return "suspicious", "Possible FPV video TX (5.8 GHz)"

    # Strong unknown signal
    if power > thresh and band == "Unknown":
        return "suspicious", "Unknown strong signal"

    # Drone detection
    if DRONE_DB_AVAILABLE:
        # Exclude known non-drone signals from drone detection
        is_known = False
        # GSM cellular
        if 935 <= freq_mhz <= 960 or 1805 <= freq_mhz <= 1880:
            is_known = True
        # ADS-B
        if 1089 <= freq_mhz <= 1091:
            is_known = True
        # GPS
        if 1574 <= freq_mhz <= 1576:
            is_known = True
        # WiFi channels (already classified)
        wifi_24 = [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472]
        for ch in wifi_24:
            if abs(freq_mhz - ch) < 3:
                is_known = True
                break
        # 5 GHz WiFi channels
        wifi_5 = [5180, 5200, 5220, 5240, 5260, 5280, 5300, 5320,
                  5500, 5520, 5540, 5560, 5580, 5600, 5620, 5640, 5660, 5680, 5700, 5720,
                  5745, 5765, 5785, 5805, 5825, 5845]
        for ch in wifi_5:
            if abs(freq_mhz - ch) < 10:
                is_known = True
                break
        # Bluetooth
        if 2402 <= freq_mhz <= 2480 and std > 3:
            is_known = True
        # LTE/Cellular bands
        if 700 <= freq_mhz <= 960 and std > 3:
            is_known = True
        if 1700 <= freq_mhz <= 2000:
            is_known = True
        if 2300 <= freq_mhz <= 2700:
            is_known = True
        # FM broadcast
        if 88 <= freq_mhz <= 108:
            is_known = True
        # DVB-T
        if 174 <= freq_mhz <= 230:
            is_known = True

        if not is_known:
            is_drone, confidence, drone_name, drone_desc = _drone_db.classify_as_drone(freq_mhz, power, std)
            if is_drone and confidence > 1.5:
                return "drone", f"{drone_name} (conf={confidence:.1f})"

    # Notable
    if power > (thresh - 5):
        return "notable", band

    return "normal", band

def scan_bands(device, focus_bands=None):
    """Run scans and return all detected signals"""
    all_signals = []

    if device == "hackrf":
        bands_to_scan = [
            (88, 250, 2000000, 3),
            (250, 600, 2000000, 3),
            (600, 1000, 2000000, 3),
            (1000, 1700, 2000000, 3),
            (1700, 2500, 1000000, 3),
            (2500, 3500, 1000000, 3),
            (5150, 5900, 500000, 3),
        ]
        if focus_bands:
            bands_to_scan = focus_bands

        for f_lo, f_hi, bw, n_sweeps in bands_to_scan:
            cmd = f"/usr/bin/hackrf_sweep -f {f_lo}:{f_hi} -w {bw} -l 32 -g 40 -a 1 -N {n_sweeps} 2>/dev/null | grep '^[0-9]'"
            output = run(cmd, timeout=45)
            sigs = parse_hackrf_sweep(output)
            all_signals.extend(sigs)

    else:  # rtlsdr
        cmd = "rtl_power -f 88M:1700M:2M -e 10s -i 3 - 2>/dev/null | grep '^20'"
        output = run(cmd, timeout=60)
        all_signals = parse_rtl_power(output)

    return all_signals

def print_report(signals, device, device_name, artemis_db):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Classify all signals
    classified = []
    for sig in signals:
        freq_mhz = sig['freq'] / 1e6
        level, detail = classify_signal(freq_mhz, sig['peak'], sig['std'], device)
        artemis_matches = match_artemis(freq_mhz, artemis_db)
        classified.append({
            **sig,
            'freq_mhz': freq_mhz,
            'level': level,
            'detail': detail,
            'artemis': artemis_matches,
        })

    # Sort by power
    classified.sort(key=lambda x: x['peak'], reverse=True)

    # Deduplicate by freq (keep strongest)
    seen_freqs = {}
    unique = []
    for c in classified:
        key = round(c['freq_mhz'], 0)
        if key not in seen_freqs or c['peak'] > seen_freqs[key]['peak']:
            seen_freqs[key] = c
    unique = list(seen_freqs.values())
    unique.sort(key=lambda x: x['peak'], reverse=True)

    suspicious = [c for c in unique if c['level'] == 'suspicious']
    drones = [c for c in unique if c['level'] == 'drone']
    notable = [c for c in unique if c['level'] == 'notable']
    normal = [c for c in unique if c['level'] == 'normal']
    total = len(unique)

    # Header
    print()
    print(f"{CYN}{'═' * 62}{RST}")
    print(f"{CYN}  ╔══════════════════════════════════════════════════════╗{RST}")
    print(f"{CYN}  ║{RST}  {BLD}SCAN-RADIO — RF ENVIRONMENT REPORT{RST}               {CYN}║{RST}")
    print(f"{CYN}  ║{RST}  Device: {device_name:<42} {CYN}║{RST}")
    print(f"{CYN}  ║{RST}  Date:   {now:<42} {CYN}║{RST}")
    print(f"{CYN}  ║{RST}  Artemis: {len(artemis_db)} signal signatures loaded        {CYN}║{RST}")
    if DRONE_DB_AVAILABLE:
        print(f"{CYN}  ║{RST}  DroneDB:  {len(_drone_db.DRONE_SIGNATURES)} drone signatures loaded            {CYN}║{RST}")
    print(f"{CYN}  ╚══════════════════════════════════════════════════════╝{RST}")
    print()

    # Territory classification
    if RF_ANALYSIS_AVAILABLE:
        territory = _rf_analysis.classify_territory(classified)
        terr_icon = {"CITY": "🏙️", "SUBURBAN": "🏘️", "TOWNSHIP": "🏡", "COUNTRYSIDE": "🌾"}.get(territory["territory"], "📍")
        print(f"  {CYN}{'━' * 56}{RST}")
        print(f"  {CYN}{terr_icon} TERRITORY: {territory['territory']}{RST}")
        print(f"  {CYN}   {territory['description']}{RST}")
        print(f"  {CYN}   Cellular bands: {', '.join(territory['cellular_bands']) or 'none'}{RST}")
        print(f"  {CYN}   WiFi networks: {territory['wifi_count']} | Broadcast: {territory['broadcast_count']}{RST}")
        print(f"  {CYN}   Strong signals (>−20 dBFS): {territory['total_strong']}{RST}")
        print()

    # Suspicious signals — red with extended details
    if suspicious:
        # Build voice alert with details
        s0 = suspicious[0]
        dist0 = _rf_analysis.estimate_distance_km(s0['freq_mhz'], s0['peak'], s0['detail']) if RF_ANALYSIS_AVAILABLE else None
        dist_str = f", about {dist0['distance_m']} meters away" if dist0 else ""
        s_detail = f"{len(suspicious)} suspicious signals. Strongest is {s0['freq_mhz']:.0f} megahertz at {s0['peak']:.0f} decibels{dist_str}."
        play_alert("suspicious", s_detail)
        print(f"  {RED}{'━' * 56}{RST}")
        print(f"  {RED}🚨 SUSPICIOUS SIGNALS — INVESTIGATE ({len(suspicious)}){RST}")
        print(f"  {RED}{'━' * 56}{RST}")
        for c in suspicious[:20]:
            power_str = f"{c['peak']:>+7.1f} dB"
            if device == "hackrf":
                power_str += "FS"
            band = get_band(c['freq_mhz'])
            # Extended detail for suspicious
            print(f"    {RED}⚠  {c['freq_mhz']:>10.1f} MHz{RST}  {RED}{power_str}{RST}  std={c['std']:.1f}  band={band}")
            print(f"       {RED}→ {c['detail']}{RST}")
            print(f"       {RED}Signal: {c['peak']:.1f} dBFS | Std Dev: {c['std']:.1f} | Band: {band}{RST}")
            # Distance estimation
            if RF_ANALYSIS_AVAILABLE:
                dist = _rf_analysis.estimate_distance_km(c['freq_mhz'], c['peak'], c['detail'])
                print(f"       {RED}Est. distance: {_rf_analysis.format_distance(dist)} | TX: ~{dist['tx_dbm']} dBm{RST}")
            if c['std'] < 2:
                print(f"       {RED}⚠ Continuous carrier — possible hidden transmitter{RST}")
            if c['artemis']:
                for m in c['artemis'][:2]:
                    print(f"       {RED}Artemis: {m['name']} ({m['modulation']}) | {m['country']}{RST}")
                    if m['description']:
                        print(f"       {RED}  {m['description'][:120]}{RST}")
            print()
        print()
    else:
        print(f"  {GRN}✅ No suspicious signals detected.{RST}")
        print()

    # Drone detection — magenta
    if drones:
        d0 = drones[0]
        dist0 = _rf_analysis.estimate_distance_km(d0['freq_mhz'], d0['peak'], d0['detail']) if RF_ANALYSIS_AVAILABLE else None
        dist_str = f", about {dist0['distance_m']} meters away" if dist0 else ""
        drone_name = d0['detail'].split(' (conf=')[0] if 'conf=' in d0['detail'] else d0['detail']
        play_alert("drone", f"{len(drones)} drone signals detected. Closest is {drone_name} at {d0['freq_mhz']:.0f} megahertz{dist_str}.")
        print(f"  {MAG}{'━' * 56}{RST}")
        print(f"  {MAG}🛸 DRONE ACTIVITY DETECTED ({len(drones)}){RST}")
        print(f"  {MAG}{'━' * 56}{RST}")
        for c in drones[:10]:
            power_str = f"{c['peak']:>+7.1f} dB"
            if device == "hackrf":
                power_str += "FS"
            # Extract drone name from detail (format: "DroneName (conf=X.X)")
            drone_name = c['detail'].split(' (conf=')[0] if 'conf=' in c['detail'] else c['detail']
            conf_str = c['detail'].split('conf=')[1].rstrip(')') if 'conf=' in c['detail'] else '?'
            print(f"    {MAG}🛸 {c['freq_mhz']:>10.1f} MHz{RST}  {MAG}{power_str}{RST}  std={c['std']:.1f}")
            print(f"       {MAG}→ {drone_name}{RST}  confidence={conf_str}")
            print(f"       {MAG}Signal: {c['peak']:.1f} dBFS | Std Dev: {c['std']:.1f}{RST}")
            # Distance estimation
            if RF_ANALYSIS_AVAILABLE:
                dist = _rf_analysis.estimate_distance_km(c['freq_mhz'], c['peak'], drone_name)
                print(f"       {MAG}Est. distance: {_rf_analysis.format_distance(dist)} | TX: ~{dist['tx_dbm']} dBm{RST}")
            if c['artemis']:
                for m in c['artemis'][:1]:
                    print(f"       {MAG}Artemis: {m['name']} ({m['modulation']}) | {m['country']}{RST}")
            print()
        print()

    # Notable signals — yellow, with band info
    if notable:
        print(f"  {YEL}{'━' * 56}{RST}")
        print(f"  {YEL}⚡ NOTABLE — VERIFY IF UNEXPECTED ({len(notable)}){RST}")
        print(f"  {YEL}{'━' * 56}{RST}")
        for c in notable[:15]:
            power_str = f"{c['peak']:>+7.1f} dB"
            if device == "hackrf":
                power_str += "FS"
            band = get_band(c['freq_mhz'])
            print(f"    {YEL}▸ {c['freq_mhz']:>10.1f} MHz{RST}  {power_str}  {band}  std={c['std']:.1f}")
            if c['artemis']:
                m = c['artemis'][0]
                print(f"      {DIM}Artemis: {m['name']} ({m['modulation']}) | {m['country']}{RST}")
        print()

    # Normal signals (summary) — GREEN
    print(f"  {GRN}{'━' * 56}{RST}")
    print(f"  {GRN}📻 IDENTIFIED / KNOWN SIGNALS ({len(normal)}){RST}")
    print(f"  {GRN}{'━' * 56}{RST}")

    # Group normal signals by type
    normal_types = {}
    for c in normal:
        key = c['detail']
        if key not in normal_types:
            normal_types[key] = []
        normal_types[key].append(c)

    for sig_type, sigs in sorted(normal_types.items(), key=lambda x: -len(x[1])):
        freq_range = f"{min(s['freq_mhz'] for s in sigs):.0f}-{max(s['freq_mhz'] for s in sigs):.0f}"
        peak = max(s['peak'] for s in sigs)
        power_str = f"{peak:>+7.1f} dB"
        if device == "hackrf":
            power_str += "FS"
        count_str = f"({len(sigs)} signals)" if len(sigs) > 1 else ""
        print(f"    {GRN}✓ {freq_range:>12} MHz{RST}  {GRN}{power_str}{RST}  {GRN}{sig_type}{RST} {DIM}{count_str}{RST}")

    print()

    # Top Artemis matches
    all_artemis = []
    for c in unique[:30]:
        for m in c['artemis']:
            all_artemis.append((c['freq_mhz'], m))
    if all_artemis:
        print(f"  {MAG}{'━' * 56}{RST}")
        print(f"  {MAG}📖 TOP ARTEMIS DATABASE MATCHES{RST}")
        print(f"  {MAG}{'━' * 56}{RST}")
        seen_names = set()
        for freq, m in all_artemis[:15]:
            if m['name'] in seen_names:
                continue
            seen_names.add(m['name'])
            print(f"    {MAG}◈ {freq:>8.1f} MHz{RST} → {BLD}{m['name']}{RST}")
            print(f"      {DIM}{m['modulation']} | {m['country']} | {m['description'][:70]}{RST}")
        print()

    # Summary bar
    print(f"  {CYN}{'━' * 56}{RST}")
    print(f"  {CYN}SUMMARY{RST}")
    print(f"  {CYN}{'━' * 56}{RST}")
    print(f"    Total signals scanned:  {BLD}{total}{RST}")
    print(f"    {GRN}Identified (normal):     {len(normal)}{RST}")
    print(f"    {YEL}Notable (verify):        {len(notable)}{RST}")
    print(f"    {RED}Suspicious (investigate): {len(suspicious)}{RST}")
    if drones:
        print(f"    {MAG}Drone activity:          {len(drones)}{RST}")
    print()

    if drones:
        print(f"  {MAG}🛸 {len(drones)} DRONE SIGNAL(S) DETECTED — see above for details{RST}")
    if suspicious:
        print(f"  {RED}⚠  ACTION REQUIRED: {len(suspicious)} suspicious signal(s) found!{RST}")
        print(f"  {RED}   Capturing IQ samples for analysis...{RST}")
        # Auto-capture and analyze top suspicious signals
        capture_dir = "/tmp/scan_captures"
        os.makedirs(capture_dir, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        for s in suspicious[:5]:  # Analyze top 5
            freq_int = int(round(s['freq_mhz'] * 1e6))
            freq_label = f"{s['freq_mhz']:.1f}".replace('.', 'p')
            raw_file = os.path.join(capture_dir, f"suspicious_{freq_label}MHz_{timestamp}.raw")
            try:
                # Capture IQ
                cmd = ["hackrf_transfer", "-r", raw_file, "-f", str(freq_int),
                       "-s", "2000000", "-n", "4000000", "-l", "32", "-g", "40", "-a", "1"]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if os.path.exists(raw_file) and os.path.getsize(raw_file) > 1000:
                    # Analyze with drone DSP
                    try:
                        import importlib.util
                        drone_dsp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drone_dsp.py")
                        spec = importlib.util.spec_from_file_location("drone_dsp", drone_dsp_path)
                        drone_dsp = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(drone_dsp)
                        import numpy as np
                        data = np.fromfile(raw_file, dtype=np.int8)
                        iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
                        iq /= 128.0
                        result = drone_dsp.analyze_drone_signal(iq, 2000000, s['freq_mhz'])
                        if result['detected']:
                            print(f"    {MAG}🛸 {s['freq_mhz']:.1f} MHz: DRONE VIDEO — {result['protocol']} "
                                  f"(conf={result['confidence']:.0%}, BW={result['bandwidth_mhz']:.1f}MHz){RST}")
                        else:
                            # Check signal type and try voice decode
                            fft_size = 4096
                            psd = np.zeros(fft_size)
                            n = min(len(iq) // fft_size, 100)
                            for i in range(n):
                                chunk = iq[i*fft_size:(i+1)*fft_size]
                                fft = np.fft.fftshift(np.fft.fft(chunk * np.hanning(fft_size)))
                                psd += np.abs(fft)**2
                            psd /= max(n, 1)
                            psd_db = 10 * np.log10(psd + 1e-10)
                            peak = np.max(psd_db)
                            above = psd_db > (peak - 10)
                            bw = np.sum(above) * 2000000 / fft_size
                            env = np.abs(iq)
                            pmr = np.max(env) / (np.mean(env) + 1e-10)
                            sig_type = "WIDEBAND" if bw > 500000 else "NARROWBAND"
                            # Try voice decode on narrowband signals
                            voice_result = ""
                            if bw < 200000:  # Narrowband — could be voice
                                try:
                                    voice_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_decode.py")
                                    vspec = importlib.util.spec_from_file_location("voice_decode", voice_path)
                                    vmod = importlib.util.module_from_spec(vspec)
                                    vspec.loader.exec_module(vmod)
                                    vr = vmod.decode_signal(s['freq_mhz'], mode='auto', raw_file=raw_file)
                                    # Only report if DSD actually decoded real content
                                    decoded = vr.get('decoded_text', '')
                                    has_real_decode = False
                                    if decoded and 'DSDDstar' not in decoded and 'resetFrameSync' not in decoded:
                                        # Check for actual decoded content (not just DSD noise)
                                        lines = [l.strip() for l in decoded.split('\n') if l.strip()
                                                 and 'DSD' not in l and 'Opened' not in l
                                                 and 'symbol' not in l and 'Digital Speech' not in l
                                                 and 'errorbars' not in l and 'No' not in l]
                                        if lines:
                                            has_real_decode = True
                                    if has_real_decode:
                                        voice_result = f" → {vr.get('protocol', 'VOICE')} [{decoded[:60]}]"
                                    elif vr.get('audio_file'):
                                        voice_result = f" (audio: {os.path.basename(vr['audio_file'])})"
                                except Exception:
                                    pass
                            print(f"    {DIM}  {s['freq_mhz']:.1f} MHz: {sig_type} ({bw/1e3:.0f}kHz) "
                                  f"peak/mean={pmr:.1f}x{voice_result} — saved {raw_file}{RST}")
                    except Exception as e:
                        print(f"    {DIM}  {s['freq_mhz']:.1f} MHz: captured — analysis failed ({e}){RST}")
                else:
                    print(f"    {DIM}  {s['freq_mhz']:.1f} MHz: capture failed{RST}")
            except Exception as e:
                print(f"    {DIM}  {s['freq_mhz']:.1f} MHz: capture error ({e}){RST}")
        print()
    else:
        print(f"  {GRN}✅ Environment appears clean. No hidden transmitters detected.{RST}")
    print(f"  {DIM}   Note: Cameras recording locally or using wired connections{RST}")
    print(f"  {DIM}   have NO RF emission and cannot be detected by RF scanning.{RST}")
    print()

    # Auto-decode FPV for ANY signal found in camera bands (suspicious, notable, or normal)
    fpv_bands = [
        (900, 928, "NTSC", "900 MHz"),
        (1080, 1300, "PAL", "1.2 GHz"),
        (2410, 2483, "NTSC", "2.4 GHz"),
        (5725, 5875, "NTSC", "5.8 GHz"),
    ]
    # Pick strongest signal from each FPV band
    fpv_by_band = {}
    for c in unique:  # Check ALL signals, not just suspicious
        for lo, hi, std, label in fpv_bands:
            if lo <= c['freq_mhz'] <= hi:
                if label not in fpv_by_band or c['peak'] > fpv_by_band[label][3]:
                    fpv_by_band[label] = (c['freq_mhz'], std, label, c['peak'], c['std'], c['level'])
                break
    fpv_targets = list(fpv_by_band.values())

    if fpv_targets:
        print(f"  {MAG}{'━' * 56}{RST}")
        print(f"  {MAG}📹 FPV/CAMERA SIGNAL DETECTED — DECODING...{RST}")
        print(f"  {MAG}{'━' * 56}{RST}")
        import tempfile
        script_dir = os.path.dirname(os.path.abspath(__file__))
        fpv_script = os.path.join(script_dir, "fpv_decode.py")
        tv_script = os.path.join(script_dir, "tv_capture.py")
        for freq_mhz, standard, label, power, std, level in fpv_targets:
            freq_int = int(round(freq_mhz))
            print(f"    {MAG}▸ Attempting FPV decode at {label} ({freq_int} MHz) [{level}]{RST}")
            print(f"      Power: {power:.1f} dBFS | Std: {std:.1f} | Standard: {standard}")
            tmpdir = tempfile.mkdtemp(prefix=f"fpv_{freq_int}_")
            out_png = os.path.join(tmpdir, "frame.png")
            cmd = f"python3 {fpv_script} capture --freq {freq_int} --standard {standard} --output {out_png} 2>&1"
            result = run(cmd, timeout=30)
            if os.path.exists(out_png) and os.path.getsize(out_png) > 1000:
                # Check if frame has video content (not just noise)
                try:
                    from PIL import Image
                    import numpy as np
                    img = Image.open(out_png).convert('L')
                    arr = np.array(img)
                    # Real video has structure: high edge energy, non-uniform distribution
                    edges = np.abs(np.diff(arr.astype(float), axis=1)).mean()
                    hist, _ = np.histogram(arr, bins=32)
                    hist = hist / hist.sum()
                    entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0]))
                    # Noise frames typically have edge=25-30, entropy=3.8-4.1
                    # WiFi false positives can reach edge=39, entropy=4.4
                    # Real video has much higher edge energy from actual scene content
                    if edges > 45 and entropy > 4.5:
                        print(f"      {RED}⚠ POSSIBLE VIDEO FRAME DETECTED!{RST}")
                        print(f"      {RED}  Edge energy: {edges:.1f} | Entropy: {entropy:.1f}{RST}")
                        print(f"      {RED}  Frame saved: {out_png}{RST}")
                        print(f"      {RED}  Review the image for camera content!{RST}")
                    else:
                        print(f"      {DIM}  Decoded frame is noise (edge={edges:.1f}, entropy={entropy:.1f}){RST}")
                        print(f"      {DIM}  Not a real video transmitter — likely WiFi/digital interference{RST}")
                except ImportError:
                    print(f"      {YEL}  Frame saved: {out_png} (install numpy/PIL for auto-analysis){RST}")
            else:
                print(f"      {DIM}  No frame decoded — signal may be too weak or digital{RST}")
            # Also try spectrogram
            spec_cmd = f"python3 {tv_script} raw --freq {freq_int} --duration 2 2>&1"
            run(spec_cmd, timeout=30)

            # Also run OFDM drone signal analysis on captured IQ
            try:
                import importlib.util
                drone_dsp_path = os.path.join(script_dir, "drone_dsp.py")
                spec = importlib.util.spec_from_file_location("drone_dsp", drone_dsp_path)
                drone_dsp = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(drone_dsp)

                iq_file = f"/tmp/capture_{freq_int}MHz_2000000.raw"
                if os.path.exists(iq_file):
                    data = np.fromfile(iq_file, dtype=np.int8)
                    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
                    iq /= 128.0
                    result = drone_dsp.analyze_drone_signal(iq, 2000000, freq_int)
                    if result['detected']:
                        print(f"      {MAG}🛸 DRONE VIDEO LINK: {result['protocol']}{RST}")
                        print(f"         Confidence: {result['confidence']:.0%} | BW: {result['bandwidth_mhz']:.1f} MHz | Duty: {result['duty_cycle']:.0%}")
                        print(f"         Flatness: {result['flatness']:.2f} | SNR: {result['snr_db']:.1f} dB | WiFi: {result['is_wifi']}")
                    else:
                        print(f"      {DIM}  OFDM analysis: no drone video link (flatness={result.get('flatness',0):.2f}, bursts={result.get('n_bursts',0)}){RST}")
            except Exception as e:
                pass  # Drone DSP analysis optional

            print()
        print()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RF Spectrum Scanner with Artemis 3 Identification")
    parser.add_argument("--band", help="Specific band to scan (e.g. 2400:2500)")
    parser.add_argument("--device", choices=["hackrf", "rtlsdr"], help="Force specific device")
    parser.add_argument("--focus", choices=["cameras", "full", "fm", "uhf", "wifi"], default="full")
    parser.add_argument("--duty", action="store_true", help="Continuous duty scan mode with alerts")
    parser.add_argument("--interval", type=int, default=120, help="Duty scan interval in seconds (default: 120)")
    parser.add_argument("--quiet", action="store_true", help="Suppress sound alerts")
    args = parser.parse_args()

    # Detect hardware
    if args.device:
        device = args.device
        device_name = "HackRF One" if device == "hackrf" else "RTL-SDR"
    else:
        device, device_name = detect_hardware()

    if device is None:
        print(f"{RED}ERROR: {device_name}{RST}")
        sys.exit(1)

    print(f"{CYN}  Detected: {device_name}{RST}")

    # Load Artemis database
    artemis_db = load_artemis()
    print(f"{CYN}  Artemis: {len(artemis_db)} signal signatures loaded{RST}")

    # Parse band focus
    focus_bands = None
    if args.band:
        parts = args.band.split(':')
        focus_bands = [(int(parts[0]), int(parts[1]), 1000000, 5)]
    elif args.focus == "cameras":
        if device == "hackrf":
            focus_bands = [
                (900, 960, 500000, 5),
                (1080, 1300, 500000, 5),
                (2400, 2500, 1000000, 5),
                (5725, 5875, 500000, 5),
            ]
        else:
            focus_bands = None  # RTL-SDR default scan covers 900 MHz
    elif args.focus == "fm":
        if device == "hackrf":
            focus_bands = [(88, 108, 200000, 5)]
    elif args.focus == "uhf":
        if device == "hackrf":
            focus_bands = [(400, 500, 500000, 5)]
        else:
            focus_bands = None
    elif args.focus == "wifi":
        if device == "hackrf":
            focus_bands = [(2400, 2500, 1000000, 5), (5150, 5900, 500000, 5)]

    if args.duty:
        # === DUTY SCAN MODE ===
        run_duty_scan(device, device_name, artemis_db, focus_bands, args.interval, args.quiet)
    else:
        # === SINGLE SCAN ===
        print(f"{CYN}  Scanning...{RST}")
        signals = scan_bands(device, focus_bands)
        print(f"{CYN}  Found {len(signals)} raw signal bins{RST}")
        print_report(signals, device, device_name, artemis_db)


def analyze_signal_type(freq_mhz):
    """Capture IQ at frequency and determine signal type. Returns spoken description."""
    import numpy as np
    try:
        freq_hz = int(freq_mhz * 1e6)
        raw = tempfile.mktemp(suffix='.raw', prefix='analyze_')
        subprocess.run(["hackrf_transfer", "-r", raw, "-f", str(freq_hz),
                        "-s", "2000000", "-n", "4000000", "-l", "32", "-g", "40", "-a", "1"],
                       capture_output=True, timeout=10)
        if not os.path.exists(raw) or os.path.getsize(raw) < 1000:
            return "Signal type unknown"

        data = np.fromfile(raw, dtype=np.int8)
        iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
        iq /= 128.0
        os.unlink(raw)

        # Spectrum analysis
        fft_size = 4096
        n = min(len(iq) // fft_size, 200)
        psd = np.zeros(fft_size)
        for i in range(n):
            chunk = iq[i * fft_size:(i + 1) * fft_size]
            fft = np.fft.fftshift(np.fft.fft(chunk * np.hanning(fft_size)))
            psd += np.abs(fft) ** 2
        psd /= max(n, 1)
        psd_db = 10 * np.log10(psd + 1e-10)
        peak = np.max(psd_db)
        above = psd_db > (peak - 10)
        bw = np.sum(above) * 2000000 / fft_size

        # Time domain
        env = np.abs(iq)
        pmr = np.max(env) / (np.mean(env) + 1e-10)
        power = 20 * np.log10(np.mean(env) + 1e-10)

        # Classify
        if bw < 5000:
            bw_desc = "very narrowband, under 5 kilohertz"
            sig_type = "continuous wave carrier"
        elif bw < 50000:
            bw_desc = f"narrowband, about {bw / 1000:.0f} kilohertz"
            sig_type = "narrowband signal"
        elif bw < 200000:
            bw_desc = f"medium bandwidth, about {bw / 1000:.0f} kilohertz"
            sig_type = "medium bandwidth signal"
        elif bw < 1000000:
            bw_desc = f"wideband, about {bw / 1e6:.1f} megahertz"
            sig_type = "wideband signal"
        else:
            bw_desc = f"very wideband, about {bw / 1e6:.1f} megahertz"
            sig_type = "very wideband signal"

        # Identify known patterns
        if 230 <= freq_mhz <= 285:
            if bw < 50000:
                return f"Display port or USB interference harmonic, {bw_desc}, continuous carrier"
            else:
                return f"Possible display port interference, {bw_desc}, bursty"
        elif 612 <= freq_mhz <= 700:
            if bw < 10000:
                return f"USB clock noise harmonic, {bw_desc}, continuous carrier"
            else:
                return f"USB or display interference, {bw_desc}"
        elif 240 <= freq_mhz <= 242:
            return "Digital audio broadcasting, DAB broadcast signal"
        elif 174 <= freq_mhz <= 230:
            return "Digital television broadcast signal"
        elif 88 <= freq_mhz <= 108:
            return "FM broadcast radio"
        elif 108 <= freq_mhz <= 137:
            return "Aviation air band, possibly aircraft communication"
        elif 935 <= freq_mhz <= 960:
            return "GSM cellular base station"
        elif 1805 <= freq_mhz <= 1880:
            return "GSM 1800 cellular base station"
        elif 2400 <= freq_mhz <= 2500:
            if pmr > 5:
                return f"WiFi or Bluetooth, {bw_desc}, bursty traffic"
            else:
                return f"WiFi or Bluetooth, {bw_desc}"
        elif 5150 <= freq_mhz <= 5900:
            return "Five gigahertz WiFi or FPV video transmitter"
        elif 337 <= freq_mhz <= 362:
            return "Car key fob or tire pressure sensor"
        elif 390 <= freq_mhz <= 400:
            return "TETRA public safety radio"
        else:
            if pmr > 8:
                return f"Unknown {sig_type}, {bw_desc}, highly bursty, possibly digital modulation"
            elif pmr < 3:
                return f"Unknown {sig_type}, {bw_desc}, continuous, possibly analog"
            else:
                return f"Unknown {sig_type}, {bw_desc}"

    except Exception:
        return "Signal type unknown"


def run_duty_scan(device, device_name, artemis_db, focus_bands, interval, quiet):
    """Continuous duty scanning — always shows full report, never clears signals."""
    import time

    print(f"\n{CYN}{'═' * 62}{RST}")
    print(f"{CYN}  ╔══════════════════════════════════════════════════════╗{RST}")
    print(f"{CYN}  ║{RST}  {BLD}DUTY SCAN MODE — CONTINUOUS MONITORING{RST}           {CYN}║{RST}")
    print(f"{CYN}  ║{RST}  Interval: {interval}s | Device: {device_name:<20} {CYN}║{RST}")
    print(f"{CYN}  ║{RST}  Press Ctrl+C to stop                              {CYN}║{RST}")
    print(f"{CYN}  ╚══════════════════════════════════════════════════════╝{RST}")
    print()

    scan_count = 0
    alert_count = 0
    known_freqs = set()  # Frequencies we've already alerted on — never cleared

    try:
        while True:
            scan_count += 1
            now = time.strftime("%H:%M:%S")
            print(f"\n{CYN}{'━' * 56}{RST}")
            print(f"{CYN}[{now}] Scan #{scan_count}{RST}")

            # Reset USB to prevent lockup
            if device == "hackrf":
                try:
                    subprocess.run(["sudo", "usbreset", "1d50:6089"],
                                   capture_output=True, timeout=5)
                    time.sleep(2)
                except Exception:
                    pass

            # Scan
            signals = scan_bands(device, focus_bands)

            # Classify
            classified = []
            for sig in signals:
                freq_mhz = sig['freq'] / 1e6
                level, detail = classify_signal(freq_mhz, sig['peak'], sig['std'], device)
                classified.append({**sig, 'freq_mhz': freq_mhz, 'level': level, 'detail': detail})

            # Deduplicate
            seen = {}
            unique = []
            for c in classified:
                key = round(c['freq_mhz'], 0)
                if key not in seen or c['peak'] > seen[key]['peak']:
                    seen[key] = c
            unique = list(seen.values())

            suspicious = sorted([c for c in unique if c['level'] == 'suspicious'],
                               key=lambda x: x['peak'], reverse=True)
            drones = sorted([c for c in unique if c['level'] == 'drone'],
                           key=lambda x: x['peak'], reverse=True)

            # Check for NEW signals (not seen before)
            new_suspicious = []
            for s in suspicious:
                if round(s['freq_mhz']) not in known_freqs:
                    new_suspicious.append(s)
                    known_freqs.add(round(s['freq_mhz']))

            new_drones = []
            for d in drones:
                if round(d['freq_mhz']) not in known_freqs:
                    new_drones.append(d)
                    known_freqs.add(round(d['freq_mhz']))

            # Play voice alert for NEW signals only — with signal type analysis
            if new_drones and not quiet:
                d0 = new_drones[0]
                dist0 = _rf_analysis.estimate_distance_km(d0['freq_mhz'], d0['peak'], d0['detail']) if RF_ANALYSIS_AVAILABLE else None
                dist_str = f", about {dist0['distance_m']} meters away" if dist0 else ""
                drone_name = d0['detail'].split(' (conf=')[0] if 'conf=' in d0['detail'] else d0['detail']
                play_alert("drone", f"{len(new_drones)} new drone signals. {drone_name} at {d0['freq_mhz']:.0f} megahertz{dist_str}.")
                alert_count += 1
            elif new_suspicious and not quiet:
                s0 = new_suspicious[0]
                dist0 = _rf_analysis.estimate_distance_km(s0['freq_mhz'], s0['peak'], s0['detail']) if RF_ANALYSIS_AVAILABLE else None
                dist_str = f", about {dist0['distance_m']} meters away" if dist0 else ""
                # Analyze signal type from IQ capture
                sig_desc = analyze_signal_type(s0['freq_mhz'])
                play_alert("new", f"{len(new_suspicious)} new signals. Strongest at {s0['freq_mhz']:.0f} megahertz, {s0['peak']:.0f} decibels{dist_str}. {sig_desc}.")
                alert_count += 1

            # === ALWAYS SHOW FULL REPORT ===
            if drones:
                print(f"\n  {MAG}{'━' * 56}{RST}")
                print(f"  {MAG}🛸 DRONE ACTIVITY ({len(drones)}){RST}")
                for d in drones[:10]:
                    new_tag = " NEW!" if round(d['freq_mhz']) not in known_freqs or round(d['freq_mhz']) in {round(x['freq_mhz']) for x in new_drones} else ""
                    dist = _rf_analysis.estimate_distance_km(d['freq_mhz'], d['peak'], d['detail']) if RF_ANALYSIS_AVAILABLE else None
                    dist_str = f" | {_rf_analysis.format_distance(dist)}" if dist else ""
                    drone_name = d['detail'].split(' (conf=')[0] if 'conf=' in d['detail'] else d['detail']
                    print(f"    {MAG}🛸 {d['freq_mhz']:>8.1f} MHz {d['peak']:>+6.1f} dBFS std={d['std']:.1f}{dist_str}{RST}{RED}{new_tag}{RST}")

            if suspicious:
                print(f"\n  {RED}{'━' * 56}{RST}")
                print(f"  {RED}⚠ SUSPICIOUS SIGNALS ({len(suspicious)}){RST}")
                for s in suspicious[:15]:
                    new_tag = f" {RED}NEW!{RST}" if round(s['freq_mhz']) in {round(x['freq_mhz']) for x in new_suspicious} else ""
                    dist = _rf_analysis.estimate_distance_km(s['freq_mhz'], s['peak'], s['detail']) if RF_ANALYSIS_AVAILABLE else None
                    dist_str = f" | {_rf_analysis.format_distance(dist)}" if dist else ""
                    cw = " ⚡CW" if s['std'] < 2 else ""
                    print(f"    {RED}⚠ {s['freq_mhz']:>8.1f} MHz {s['peak']:>+6.1f} dBFS std={s['std']:.1f}{cw}{dist_str}{RST}{new_tag}")
                if len(suspicious) > 15:
                    print(f"    {DIM}... and {len(suspicious) - 15} more{RST}")

            if not suspicious and not drones:
                print(f"  {GRN}✅ All clear — no suspicious signals{RST}")

            # Territory
            if RF_ANALYSIS_AVAILABLE:
                territory = _rf_analysis.classify_territory(classified)
                print(f"  {DIM}Territory: {territory['territory']} | Signals: {len(unique)} | "
                      f"WiFi: {territory['wifi_count']} | Cellular: {territory['cellular_count']}{RST}")

            # Status line
            new_count = len(new_suspicious) + len(new_drones)
            new_str = f" | {RED}{new_count} NEW{RST}" if new_count > 0 else ""
            print(f"  {DIM}Scans: {scan_count} | Alerts: {alert_count} | "
                  f"Tracked: {len(known_freqs)} freqs{new_str} | Next: {interval}s{RST}")

            # Wait
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n{CYN}Duty scan stopped after {scan_count} scans, {alert_count} alerts.{RST}")
        print(f"{CYN}Tracked {len(known_freqs)} unique suspicious frequencies.{RST}")
        if not quiet:
            play_alert("clear")

if __name__ == "__main__":
    main()
