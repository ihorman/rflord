# Spy device and surveillance equipment database
# Format: freq_low_mhz, freq_high_mhz, name, icon, threat_level
# threat_level: 0=critical, 1=high, 2=medium, 3=low

SPY_DEVICES = [
    # Hidden cameras — analog video transmitters
    (900, 928, "Hidden Camera 900MHz", "📹", 0),
    (1080, 1200, "Hidden Camera 1.2GHz", "📹", 0),
    (1200, 1300, "Hidden Camera 1.2GHz", "📹", 0),
    (2400, 2483, "Hidden Camera 2.4GHz", "📹", 0),
    (5725, 5875, "Hidden Camera 5.8GHz", "📹", 0),
    (5925, 7125, "WiFi 6E Camera 6GHz", "📹", 0),

    # FPV video transmitters (used in drones and spy cameras)
    (5645, 5945, "FPV Video TX 5.8GHz", "🎯", 1),
    (430, 450, "FPV Video TX 70cm", "🎯", 1),
    (1080, 1300, "FPV Video TX 1.2GHz", "🎯", 1),

    # GPS trackers
    (1575, 1576, "GPS L1 Tracker", "📍", 1),
    (1227, 1228, "GPS L2 Tracker", "📍", 1),
    (1176, 1177, "GPS L5 Tracker", "📍", 1),
    (1575, 1576, "GLONASS L1 Tracker", "📍", 1),
    (1602, 1609, "GLONASS L1 Tracker", "📍", 1),

    # GSM/LTE IMSI catchers (StingRay, cell-site simulators)
    (935, 960, "GSM IMSI Catcher", "📡", 0),
    (1805, 1880, "GSM 1800 IMSI Catcher", "📡", 0),
    (2110, 2170, "3G IMSI Catcher", "📡", 0),
    (2620, 2690, "LTE IMSI Catcher", "📡", 0),
    (869, 894, "CDMA IMSI Catcher", "📡", 0),
    (791, 862, "LTE 800 IMSI Catcher", "📡", 0),
    (2500, 2570, "LTE 2600 IMSI Catcher", "📡", 0),
    (3400, 3800, "5G NR IMSI Catcher", "📡", 0),

    # Audio bugs / wiretaps
    (35, 45, "VHF Audio Bug", "🎙", 0),
    (72, 76, "VHF Audio Bug", "🎙", 0),
    (150, 174, "VHF Audio Bug", "🎙", 0),
    (400, 470, "UHF Audio Bug", "🎙", 0),
    (2400, 2483, "2.4GHz Audio Bug", "🎙", 0),
    (88, 108, "FM Band Bug", "🎙", 0),
    (108, 137, "Air Band Bug", "🎙", 0),

    # Bluetooth trackers (AirTag, Tile, SmartTag, Flipper)
    (2402, 2480, "BT Tracker (AirTag/Tile)", "📎", 2),

    # WiFi spy cameras
    (2412, 2462, "WiFi Spy Camera", "📷", 1),
    (5180, 5825, "WiFi 5GHz Spy Camera", "📷", 1),
    (5925, 7125, "WiFi 6E Spy Camera", "📷", 1),

    # Flock Safety ALPR cameras (from AirHound)
    (2400, 2483, "Flock Safety Camera", "📷", 0),
    (5180, 5825, "Flock Safety Camera 5GHz", "📷", 0),

    # Cell phone signal snoopers / IMSI catchers
    (700, 800, "Cell Snooper 700MHz", "📱", 0),
    (800, 900, "Cell Snooper 850MHz", "📱", 0),
    (1700, 1800, "Cell Snooper 1700MHz", "📱", 0),
    (1900, 2000, "Cell Snooper 1900MHz", "📱", 0),

    # RF signal jammers
    (800, 960, "Cell Jammer", "🚫", 0),
    (1800, 1900, "Cell Jammer DCS", "🚫", 0),
    (2100, 2200, "3G Jammer", "🚫", 0),
    (2400, 2500, "WiFi/Drone Jammer", "🚫", 0),
    (1570, 1580, "GPS Jammer", "🚫", 0),
    (1200, 1300, "GPS L2 Jammer", "🚫", 0),

    # Radar detectors / speed cameras
    (10500, 10550, "X-band Radar", "🚨", 2),
    (24050, 24250, "K-band Radar", "🚨", 2),
    (33400, 36000, "Ka-band Radar", "🚨", 2),

    # Keyloggers / RF emanations
    (0, 30, "Keylogger RF Emission", "⌨", 0),

    # Satellite phones (could be used for covert comms)
    (1616, 1626, "Iridium Sat Phone", "🛰", 2),
    (1980, 2010, "Inmarsat Sat Phone", "🛰", 2),
    (1626, 1660, "Globalstar Sat Phone", "🛰", 2),

    # Covert video links
    (1700, 1900, "Covert Video Link", "🎥", 0),
    (2200, 2300, "Covert Video Link", "🎥", 0),
    (3000, 3500, "Covert Video Link S-band", "🎥", 0),

    # Drones
    (900, 928, "Drone Control 900MHz", "🛸", 1),
    (2400, 2483, "Drone Control 2.4GHz", "🛸", 1),
    (5725, 5875, "Drone Video 5.8GHz", "🛸", 1),
    (1430, 1444, "Drone Video 1.4GHz", "🛸", 1),
    (2300, 2500, "DJI OcuSync/O3/O4", "🛸", 1),
    (5725, 5875, "DJI OccuSync Video", "🛸", 1),
    (900, 928, "ExpressLRS (ELRS)", "🛸", 1),
    (2400, 2483, "ExpressLRS (ELRS)", "🛸", 1),
    (868, 870, "TBS Crossfire EU", "🛸", 1),
    (915, 928, "TBS Crossfire US", "🛸", 1),
    (2400, 2483, "TBS Tracer", "🛸", 1),
    (2400, 2483, "FrSky ACCESS", "🛸", 1),
    (5725, 5875, "HDZero Digital FPV", "🛸", 1),
    (5725, 5875, "Walksnail Avatar", "🛸", 1),

    # Raven acoustic sensors (ShotSpotter) — from AirHound
    (2400, 2483, "Raven Acoustic Sensor", "🔫", 1),

    # Flipper Zero — hacking multi-tool (from AirHound)
    (2402, 2480, "Flipper Zero", "🔧", 2),

    # Wireless microphone systems (covert audio)
    (174, 216, "Wireless Mic VHF", "🎙", 0),
    (470, 698, "Wireless Mic UHF", "🎙", 0),
    (1785, 1805, "DECT Phone Bug", "🎙", 0),
    (1880, 1900, "DECT Phone Bug", "🎙", 0),

    # Zigbee/Z-Wave smart home surveillance
    (2400, 2483, "Zigbee Device", "🏠", 2),
    (868, 868, "Z-Wave EU Device", "🏠", 2),
    (908, 916, "Z-Wave US Device", "🏠", 2),

    # LoRa surveillance devices
    (868, 870, "LoRa Device EU", "📡", 2),
    (902, 928, "LoRa Device US", "📡", 2),
    (433, 434, "LoRa Device 433MHz", "📡", 2),
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
    """
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
