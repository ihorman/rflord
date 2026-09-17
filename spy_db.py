# Spy device and surveillance equipment database
# Format: freq_low_mhz, freq_high_mhz, name, icon, threat_level
# threat_level: 0=critical, 1=high, 2=medium, 3=low

SPY_DEVICES = [
    # Smoke detectors (wireless alarm transmitters) — wartime: booby trap indicators

    # Laser/IR perimeter beam alarms — wartime: tripwire/booby trap indicators

    # Hidden cameras — analog video transmitters

    # FPV video transmitters (used in drones and spy cameras)

    # GPS trackers

    # GSM/LTE IMSI catchers (StingRay, cell-site simulators)

    # Audio bugs / wiretaps

    # Bluetooth trackers (AirTag, Tile, SmartTag, Flipper)

    # WiFi spy cameras

    # Flock Safety ALPR cameras (from AirHound)

    # Cell phone signal snoopers / IMSI catchers

    # RF signal jammers

    # Radar detectors / speed cameras
    (10500, 10550, "X-band Radar", "🚨", 2),

    # Keyloggers / RF emanations

    # Satellite phones (could be used for covert comms)
    (1616, 1626, "Iridium Sat Phone", "🛰", 2),
    (1980, 2010, "Inmarsat Sat Phone", "🛰", 2),
    (1626, 1660, "Globalstar Sat Phone", "🛰", 2),

    # Covert video links

    # Drones

    # Raven acoustic sensors (ShotSpotter) — from AirHound

    # Flipper Zero — hacking multi-tool (from AirHound)

    # Wireless microphone systems (covert audio)

    # Zigbee/Z-Wave smart home surveillance

    # LoRa surveillance devices
]

# Icon mapping for signal types
SIGNAL_ICONS = {
    "Mil/Enc": "🎖",
    "Link-11": "🎖",
    "Milstar": "🎖",
    "Gonets": "🎖",
    "Tetrapol": "🎖",
    "TETRA": "🎖",
    "Kiwi": "🎖",
    "SPY-CAM": "📹",
    "CAM?": "📹",
    "FPV?": "🎯",
    "Display Port": "💻",
    "USB-noise": "🔌",
    "USB-burst": "🔌",
    "DAB": "📻",
    "DAB+": "📻",
    "📡": "📡",
    "Analog": "🔊",
    "Digital": "📡",
    "Bursty": "📡",
    "WiFi/BT": "📶",
    "WiFi/FPV": "📶",
    "Keyfob": "🔑",
    "Flock": "📷",
    "Raven": "🔫",
    "Flipper": "🔧",
    "Jammer": "🚫",
    "IMSI": "📡",
    "Tracker": "📍",
    "Zigbee": "🏠",
    "Z-Wave": "🏠",
    "LoRa": "📡",
    "DECT": "🎙",
}

import unicodedata

def pad_icon(icon):
    """Pad icon to exactly 2 display cells."""
    w = 0
    for c in icon:
        ew = unicodedata.east_asian_width(c)
        w += 2 if ew in ('W', 'F') else 1
    if w < 2:
        return icon + ' ' * (2 - w)
    return icon

# Threat level icons
THREAT_ICONS = {
    0: "🔴",  # Critical
    1: "🟠",  # High
    2: "🟡",  # Medium
    3: "🟢",  # Low
}

def identify_spy_device(freq_mhz, std):
    """Check if frequency matches known spy device.
    Camera/FPV bands only flagged if std < 2 (continuous carrier).
    Bursty signals (std > 3) in those bands are cellular/digital, not cameras.
    Satellite frequencies are excluded — they're not spy devices.
    """
    # Satellite NAVIGATION exclusion — these are navigation signals, not spy devices
    # NOTE: Iridium/Inmarsat/Globalstar are sat PHONES — NOT excluded (they're in spy_db)
    _sat_ranges = [
        (1574, 1577),  # GPS L1, Galileo E1, GLONASS
        (1227, 1228),  # GPS L2
        (1176, 1177),  # GPS L5
        (1600, 1606),  # GLONASS L1
        (1242, 1252),  # GLONASS L2
        (1559, 1563),  # BeiDou B1
        (1207, 1210),  # BeiDou B2
        (1268, 1269),  # BeiDou B3
        (1191, 1192),  # Galileo E5
        (1087, 1095),  # ADS-B
    ]
    for sat_lo, sat_hi in _sat_ranges:
        if sat_lo <= freq_mhz <= sat_hi:
            return None, None, None  # Satellite — not a spy device

    # Known legitimate bands — never spy devices
    _legit_ranges = [
        (88, 108),     # FM broadcast
        (174, 230),    # DAB/DVB-T
        (470, 862),    # DVB-T2/TV
        (880, 960),    # GSM900
        (1805, 1880),  # GSM1800
        (1920, 1979),  # 3G/LTE (before Inmarsat 1980-2010)
        (2011, 2170),  # 3G/LTE (after Inmarsat 1980-2010)
        (2300, 2500),  # WiFi 2.4GHz (narrower — don't exclude 2.5GHz drone control)
        (5150, 5875),  # WiFi 5GHz
        (108, 137),    # Air band
        (144, 148),    # 2m ham
        # NOTE: 430-470 and 868-870 NOT excluded — smoke detectors, laser
        # perimeter alarms, and other security devices operate there
    ]
    for legit_lo, legit_hi in _legit_ranges:
        if legit_lo <= freq_mhz <= legit_hi:
            return None, None, None  # Known legitimate — not a spy device

    for lo, hi, name, icon, threat in SPY_DEVICES:
        if lo <= freq_mhz <= hi:
            # Camera/FPV bands: only flag if continuous carrier (std < 2)
            # In Ukraine, 1080-1300 MHz is CDMA2000 cellular (bursty, std > 3)
            # 900-928 MHz is GSM cellular
            # 2400-2483 MHz is WiFi
            if "Camera" in name or "FPV" in name:
                if std >= 2:
                    continue  # Not a camera — skip to next entry
            return name, icon, threat
    return None, None, None

def get_signal_icon(sig_type, freq_mhz, std):
    """Get icon for signal type."""
    # Check spy devices first
    spy_name, spy_icon, threat = identify_spy_device(freq_mhz, std)
    if spy_icon:
        return spy_icon
    
    # Check signal type icons
    return SIGNAL_ICONS.get(sig_type, "📡")

def get_threat_icon(freq_mhz, std):
    """Get threat level icon."""
    spy_name, spy_icon, threat = identify_spy_device(freq_mhz, std)
    if threat is not None:
        return THREAT_ICONS.get(threat, "🟢")
    return "🟢"
