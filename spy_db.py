# Spy device and surveillance equipment database
# Format: freq_low_mhz, freq_high_mhz, name, icon, threat_level
# threat_level: 0=critical, 1=high, 2=medium, 3=low

SPY_DEVICES = [
    # Hidden cameras — analog video transmitters
    (900, 928, "Hidden Camera 900MHz", "SC", 0),
    (1080, 1200, "Hidden Camera 1.2GHz", "SC", 0),
    (1200, 1300, "Hidden Camera 1.2GHz", "SC", 0),
    (2400, 2483, "Hidden Camera 2.4GHz", "SC", 0),
    (5725, 5875, "Hidden Camera 5.8GHz", "SC", 0),
    
    # FPV video transmitters (used in drones and spy cameras)
    (5645, 5945, "FPV Video TX 5.8GHz", "FP", 1),
    (430, 450, "FPV Video TX 70cm", "FP", 1),
    (1080, 1300, "FPV Video TX 1.2GHz", "FP", 1),
    
    # GPS trackers
    (1575, 1576, "GPS L1 Tracker", "GT", 1),
    (1227, 1228, "GPS L2 Tracker", "GT", 1),
    (1176, 1177, "GPS L5 Tracker", "GT", 1),
    
    # GSM/LTE IMSI catchers (StingRay, cell-site simulators)
    (935, 960, "GSM IMSI Catcher", "IC", 0),
    (1805, 1880, "GSM 1800 IMSI Catcher", "IC", 0),
    (2110, 2170, "3G IMSI Catcher", "IC", 0),
    (2620, 2690, "LTE IMSI Catcher", "IC", 0),
    
    # Audio bugs / wiretaps
    (35, 45, "VHF Audio Bug", "AB", 0),
    (72, 76, "VHF Audio Bug", "AB", 0),
    (150, 174, "VHF Audio Bug", "AB", 0),
    (400, 470, "UHF Audio Bug", "AB", 0),
    (2400, 2483, "2.4GHz Audio Bug", "AB", 0),
    
    # Bluetooth trackers (AirTag, Tile, etc.)
    (2402, 2480, "BT Tracker (AirTag/Tile)", "BT", 2),
    
    # WiFi spy cameras
    (2412, 2462, "WiFi Spy Camera", "WC", 1),
    (5180, 5825, "WiFi 5GHz Spy Camera", "WC", 1),
    
    # Radar detectors / speed cameras
    (10500, 10550, "X-band Radar", "RD", 2),
    (24050, 24250, "K-band Radar", "RD", 2),
    (33400, 36000, "Ka-band Radar", "RD", 2),
    
    # Keyloggers / RF emanations
    (0, 30, "Keylogger RF Emission", "KL", 0),
    
    # Satellite phones (could be used for covert comms)
    (1616, 1626, "Iridium Sat Phone", "SP", 2),
    (1980, 2010, "Inmarsat Sat Phone", "SP", 2),
    (1626, 1660, "Globalstar Sat Phone", "SP", 2),
    
    # Covert video links
    (1700, 1900, "Covert Video Link", "CV", 0),
    (2200, 2300, "Covert Video Link", "CV", 0),
    (3000, 3500, "Covert Video Link S-band", "CV", 0),
    
    # Drones
    (900, 928, "Drone Control 900MHz", "DR", 1),
    (2400, 2483, "Drone Control 2.4GHz", "DR", 1),
    (5725, 5875, "Drone Video 5.8GHz", "DR", 1),
    (1430, 1444, "Drone Video 1.4GHz", "DR", 1),
    (2300, 2500, "DJI OcuSync/O3/O4", "DR", 1),
    (5725, 5875, "DJI OccuSync Video", "DR", 1),
    (900, 928, "ExpressLRS (ELRS)", "DR", 1),
    (2400, 2483, "ExpressLRS (ELRS)", "DR", 1),
    (868, 870, "TBS Crossfire EU", "DR", 1),
    (915, 928, "TBS Crossfire US", "DR", 1),
    (2400, 2483, "TBS Tracer", "DR", 1),
    (2400, 2483, "FrSky ACCESS", "DR", 1),
    (5725, 5875, "HDZero Digital FPV", "DR", 1),
    (5725, 5875, "Walksnail Avatar", "DR", 1),
]

# Icon mapping for signal types
SIGNAL_ICONS = {
    "Mil/Enc": "M ",
    "Link-11": "L1",
    "Milstar": "MS",
    "Gonets": "GN",
    "Tetrapol": "TP",
    "TETRA": "TR",
    "Kiwi": "KW",
    "SPY-CAM": "SC",
    "CAM?": "C?",
    "FPV?": "FP",
    "Display Port": "DP",
    "USB-noise": "UN",
    "USB-burst": "UB",
    "DAB": "DB",
    "DAB+": "D+",
    "CW": "CW",
    "Analog": "AN",
    "Digital": "DG",
    "Bursty": "BY",
    "WiFi/BT": "WB",
    "WiFi/FPV": "WF",
    "Keyfob": "KF",
}

# Threat level icons
THREAT_ICONS = {
    0: "!!",  # Critical
    1: "! ",  # High
    2: "? ",  # Medium
    3: "  ",  # Low
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
    return SIGNAL_ICONS.get(sig_type, "IC")

def get_threat_icon(freq_mhz, std):
    """Get threat level icon."""
    spy_name, spy_icon, threat = identify_spy_device(freq_mhz, std)
    if threat is not None:
        return THREAT_ICONS.get(threat, "  ")
    return "  "
