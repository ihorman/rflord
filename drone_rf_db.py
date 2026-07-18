#!/usr/bin/env python3
"""
drone_rf_db.py — Drone RF Signature Database
Based on known drone communication protocols and RF characteristics.
Used by scan_radio.py to identify potential drone signals.

Sources:
- Al-Sad/DroneRF: RF-based drone detection (doi:10.1016/j.future.2019.05.007)
- DJI OcuSync/O3/O4 protocol analysis
- HDZero, Walksnail, ExpressLRS protocol specs
- Analog FPV channel plans (FR632, ImmersionRC, TBS)
"""

# Drone RF signatures: (freq_low_mhz, freq_high_mhz, bandwidth_khz, modulation, name, description)
DRONE_SIGNATURES = [
    # === DJI Digital Video Links ===
    # OcuSync 1.0 (Mavic Pro, Spark, Mavic Air)
    (2400, 2483, 10000, "FHSS", "DJI OcuSync 1.0", "Mavic Pro/Spark/Mavic Air — encrypted FHSS, 2.4 GHz"),
    (5725, 5850, 10000, "FHSS", "DJI OcuSync 1.0", "Mavic Pro/Spark/Mavic Air — encrypted FHSS, 5.8 GHz"),

    # OcuSync 2.0 (Mavic Air 2, Mavic Mini 2)
    (2400, 2483, 10000, "FHSS", "DJI OcuSync 2.0", "Mavic Air 2/Mini 2 — encrypted FHSS, 2.4 GHz"),
    (5725, 5850, 10000, "FHSS", "DJI OcuSync 2.0", "Mavic Air 2/Mini 2 — encrypted FHSS, 5.8 GHz"),

    # O3 (DJI Mini 3 Pro, Mavic 3)
    (2400, 2483, 20000, "OFDM", "DJI O3", "Mini 3 Pro/Mavic 3 — encrypted OFDM, 2.4 GHz"),
    (5725, 5850, 20000, "OFDM", "DJI O3", "Mini 3 Pro/Mavic 3 — encrypted OFDM, 5.8 GHz"),

    # O4 (DJI Air 3, Mini 4 Pro, Mavic 4)
    (2400, 2483, 40000, "OFDM", "DJI O4", "Air 3/Mini 4 Pro — encrypted OFDM, 2.4 GHz, 20km range"),
    (5150, 5350, 40000, "OFDM", "DJI O4", "Air 3/Mini 4 Pro — encrypted OFDM, 5 GHz low"),
    (5470, 5730, 40000, "OFDM", "DJI O4", "Air 3/Mini 4 Pro — encrypted OFDM, 5 GHz high"),
    (5725, 5850, 40000, "OFDM", "DJI O4", "Air 3/Mini 4 Pro — encrypted OFDM, 5.8 GHz"),

    # DJI Lightbridge (Phantom 3 Pro, Inspire 1)
    (2400, 2483, 10000, "OFDM", "DJI Lightbridge", "Phantom 3 Pro/Inspire 1 — 2.4 GHz OFDM"),
    (5725, 5850, 10000, "OFDM", "DJI Lightbridge", "Phantom 3 Pro/Inspire 1 — 5.8 GHz OFDM"),

    # DJI WiFi-based (Ryze Tello, Mavic Mini 1)
    (2400, 2483, 20000, "WiFi", "DJI WiFi", "Tello/Mavic Mini 1 — standard WiFi 2.4 GHz"),
    (5150, 5350, 20000, "WiFi", "DJI WiFi", "Tello/Mavic Mini 1 — standard WiFi 5 GHz"),

    # DJI FPV System (DJI FPV, Avata, O3 Air Unit)
    (5725, 5850, 20000, "OFDM", "DJI FPV/O3 Air", "DJI FPV/Avata — encrypted digital FPV, 5.8 GHz, low latency"),

    # DJI Controller RC (dedicated controller frequency)
    (2400, 2483, 5000, "FHSS", "DJI RC", "DJI Smart Controller/RC Pro — control link, 2.4 GHz"),
    (5725, 5850, 5000, "FHSS", "DJI RC", "DJI Smart Controller/RC Pro — control link, 5.8 GHz"),

    # === Other Digital FPV Systems ===
    # HDZero (formerly Byte Frost)
    (5725, 5875, 10000, "OFDM", "HDZero", "Digital FPV — low latency, 5.8 GHz, unencrypted"),

    # Walksnail (Caddx Vista)
    (5725, 5875, 20000, "OFDM", "Walksnail/Caddx", "Digital FPV — encrypted, 5.8 GHz"),

    # ExpressLRS (ELRS) — control link
    (2400, 2483, 500, "FHSS", "ExpressLRS 2.4", "ELRS control link — 2.4 GHz, long range"),
    (900, 928, 500, "FHSS", "ExpressLRS 900", "ELRS control link — 900 MHz, ultra long range"),

    # === Analog FPV Video (unencrypted NTSC/PAL) ===
    (900, 928, 6000, "FM", "Analog FPV 900", "Analog FPV video — 900 MHz, NTSC/PAL, unencrypted"),
    (1080, 1300, 6000, "FM", "Analog FPV 1.2G", "Analog FPV video — 1.2 GHz, NTSC/PAL, unencrypted"),
    (2400, 2483, 6000, "FM", "Analog FPV 2.4G", "Analog FPV video — 2.4 GHz, NTSC/PAL, unencrypted"),
    (5725, 5875, 6000, "FM", "Analog FPV 5.8G", "Analog FPV video — 5.8 GHz, NTSC/PAL, unencrypted"),

    # === Other Drone Brands ===
    # Autel Robotics (EVO series)
    (2400, 2483, 10000, "OFDM", "Autel SkyLink", "EVO II/III — encrypted OFDM, 2.4 GHz"),
    (5150, 5350, 10000, "OFDM", "Autel SkyLink", "EVO II/III — encrypted OFDM, 5 GHz"),
    (5725, 5850, 10000, "OFDM", "Autel SkyLink", "EVO II/III — encrypted OFDM, 5.8 GHz"),

    # Parrot (Anafi, Bebop)
    (2400, 2483, 20000, "WiFi", "Parrot WiFi", "Anafi/Bebop — WiFi 2.4 GHz"),
    (5150, 5350, 20000, "WiFi", "Parrot WiFi", "Anafi/Bebop — WiFi 5 GHz"),

    # Skydio (2+, X10)
    (2400, 2483, 20000, "WiFi", "Skydio WiFi", "Skydio 2+/X10 — WiFi 2.4 GHz"),
    (5150, 5350, 20000, "WiFi", "Skydio WiFi", "Skydio 2+/X10 — WiFi 5 GHz"),

    # Hubsan (Zino, H501S)
    (2400, 2483, 2000, "FHSS", "Hubsan FHSS", "Zino/H501S — FHSS 2.4 GHz"),
    (5725, 5850, 6000, "FM", "Hubsan FPV", "H501S — analog FPV 5.8 GHz"),

    # Holy Stone (HS720, HS175)
    (2400, 2483, 20000, "WiFi", "Holy Stone WiFi", "HS720/HS175 — WiFi 2.4 GHz"),
    (5725, 5850, 6000, "FM", "Holy Stone FPV", "Budget models — analog FPV 5.8 GHz"),

    # Fimi (Xiaomi) (Fimi X8)
    (2400, 2483, 10000, "OFDM", "Fimi/Xiaomi", "Fimi X8 — OFDM 2.4 GHz"),
    (5725, 5850, 10000, "OFDM", "Fimi/Xiaomi", "Fimi X8 — OFDM 5.8 GHz"),

    # === Control Links (generic) ===
    # FrSky (Taranis, X-series receivers)
    (2400, 2483, 500, "FHSS", "FrSky ACCESS", "Taranis/X-series — FHSS control link, 2.4 GHz"),

    # Spektrum (DX series)
    (2400, 2483, 1000, "DSSS", "Spektrum DSMX", "DX-series — DSSS/DSM2 control link, 2.4 GHz"),

    # FlySky (budget controllers)
    (2400, 2483, 1000, "FHSS", "FlySky AFHDS", "Budget controllers — FHSS 2.4 GHz"),

    # TBS Crossfire (long range control)
    (868, 868, 100, "FHSS", "TBS Crossfire 868", "Long range control — 868 MHz (EU)"),
    (915, 915, 100, "FHSS", "TBS Crossfire 915", "Long range control — 915 MHz (US)"),

    # TBS Tracer
    (2400, 2483, 500, "FHSS", "TBS Tracer", "Low latency control — 2.4 GHz"),

    # ImmersionRC Ghost
    (2400, 2483, 500, "FHSS", "IRC Ghost", "Low latency control — 2.4 GHz"),
]


def match_drone(freq_mhz, bandwidth_khz=None, modulation=None):
    """
    Match a frequency against known drone signatures.
    Returns list of matching drone descriptions.
    """
    matches = []
    for sig in DRONE_SIGNATURES:
        freq_lo, freq_hi, bw_khz, mod, name, desc = sig
        if freq_lo <= freq_mhz <= freq_hi:
            score = 1.0
            # Bonus for bandwidth match
            if bandwidth_khz and bw_khz > 0:
                bw_ratio = min(bandwidth_khz, bw_khz) / max(bandwidth_khz, bw_khz)
                score *= (0.5 + 0.5 * bw_ratio)
            # Bonus for modulation match
            if modulation and modulation.upper() == mod.upper():
                score *= 1.2
            matches.append((score, name, desc, mod, bw_khz))
    matches.sort(key=lambda x: -x[0])
    return matches[:3]


def get_drone_band_summary():
    """Return summary of all drone frequency bands."""
    bands = {}
    for sig in DRONE_SIGNATURES:
        freq_lo, freq_hi, bw_khz, mod, name, desc = sig
        key = f"{freq_lo}-{freq_hi}"
        if key not in bands:
            bands[key] = {'lo': freq_lo, 'hi': freq_hi, 'drones': []}
        bands[key]['drones'].append(name)
    return bands


def classify_as_drone(freq_mhz, power_dbfs, std_dev, bandwidth_khz=None):
    """
    Classify a signal as potential drone activity.
    Returns (is_drone, confidence, drone_name, description)
    """
    matches = match_drone(freq_mhz, bandwidth_khz)
    if not matches:
        return False, 0, None, None

    best_score, name, desc, mod, expected_bw = matches[0]

    # Drone signals are typically:
    # - In 2.4/5.8 GHz bands (most common) or 900/1.2 GHz (FPV/long range)
    # - Moderate to strong signal (>-40 dBFS for nearby drone)
    # - Either bursty (FHSS/WiFi) or continuous (analog FPV)
    # - Wideband (>1 MHz for video, >100 kHz for digital control)

    confidence = best_score

    # Stronger signal = more likely a drone (drones are typically nearby)
    if power_dbfs > -20:
        confidence *= 1.5
    elif power_dbfs > -30:
        confidence *= 1.2
    elif power_dbfs < -50:
        confidence *= 0.5  # Too weak for most drones

    # Bursty signals (high std dev) are common for FHSS/WiFi drones
    if std_dev > 5:
        confidence *= 1.1
    # Continuous low-std signals could be analog FPV
    elif std_dev < 2 and mod == "FM":
        confidence *= 1.3

    is_drone = confidence > 0.8
    return is_drone, min(confidence, 3.0), name, desc


# Known drone-specific frequency patterns
# These are frequencies where drones have distinctive behavior
DRONE_PATTERNS = {
    # DJI OcuSync hop pattern: jumps between channels in 2.4 GHz band
    'dji_ocusync_24': {
        'center': 2441,
        'bandwidth': 83,
        'hop_channels': [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462],
        'hop_rate_hz': 100,  # ~100 hops/sec
        'description': 'DJI OcuSync 2.4 GHz — FHSS hopping between WiFi channels'
    },
    # DJI OcuSync 5.8 GHz
    'dji_ocusync_58': {
        'center': 5800,
        'bandwidth': 125,
        'hop_channels': [5745, 5765, 5785, 5805, 5825, 5845],
        'hop_rate_hz': 100,
        'description': 'DJI OcuSync 5.8 GHz — FHSS hopping'
    },
}


if __name__ == "__main__":
    # Test: print all drone bands
    print("=== Drone RF Signature Database ===")
    print(f"Total signatures: {len(DRONE_SIGNATURES)}")
    print()
    bands = get_drone_band_summary()
    for key, info in sorted(bands.items()):
        drones = ', '.join(set(info['drones']))
        print(f"  {info['lo']:>5}-{info['hi']:<5} MHz: {drones}")
    print()
    # Test classification
    test_cases = [
        (2437, -15, 8, "WiFi-like bursty signal"),
        (5800, -25, 3, "Continuous carrier at 5.8 GHz"),
        (910, -20, 1, "Continuous at 900 MHz"),
        (433, -30, 5, "Unknown signal at 433 MHz"),
    ]
    print("=== Classification Tests ===")
    for freq, power, std, desc in test_cases:
        is_drone, conf, name, ddesc = classify_as_drone(freq, power, std)
        status = "DRONE" if is_drone else "not drone"
        print(f"  {freq} MHz ({desc}): {status} conf={conf:.2f} → {name or 'N/A'}")
