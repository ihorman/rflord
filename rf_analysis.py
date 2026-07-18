#!/usr/bin/env python3
"""
rf_analysis.py — Signal distance estimation and territory classification.

Estimates approximate distance to transmitters using Free-Space Path Loss (FSPL)
and classifies the environment (countryside vs city) based on signal density.

Distance estimation uses:
- FSPL = 32.44 + 20*log10(d_km) + 20*log10(f_MHz)
- Typical transmit powers for known signal types
- HackRF sensitivity: ~-70 dBFS noise floor with AMP+LNA
- Antenna gain: ~2 dBi (stock antenna), 0 dBi (whip)
"""

import math


# Typical transmit powers (EIRP in dBm) by signal type
TX_POWERS = {
    # Cellular
    "GSM 900 downlink":    {"tx_dbm": 43, "desc": "GSM base station"},      # ~20W
    "GSM 1800 downlink":   {"tx_dbm": 40, "desc": "GSM base station"},      # ~10W
    "LTE/Cellular":        {"tx_dbm": 43, "desc": "LTE base station"},      # ~20W
    "4G LTE":              {"tx_dbm": 43, "desc": "LTE base station"},      # ~20W
    "3G/4G":               {"tx_dbm": 40, "desc": "3G/4G base station"},    # ~10W
    "Cellular/GSM":        {"tx_dbm": 43, "desc": "Cellular base station"},

    # Broadcast
    "FM Radio":            {"tx_dbm": 70, "desc": "FM broadcast tower"},     # ~10kW
    "DVB-T TV":            {"tx_dbm": 60, "desc": "DVB-T broadcast tower"}, # ~1kW
    "DAB":                 {"tx_dbm": 60, "desc": "DAB broadcast tower"},

    # Aviation
    "ADS-B aircraft":      {"tx_dbm": 27, "desc": "Aircraft transponder"},  # ~500mW
    "Air Band":            {"tx_dbm": 37, "desc": "Aircraft radio"},        # ~5W
    "VOR":                 {"tx_dbm": 50, "desc": "VOR navigation beacon"}, # ~100W
    "ACARS":               {"tx_dbm": 37, "desc": "Aircraft ACARS"},

    # Amateur / Business
    "2m Amateur":           {"tx_dbm": 37, "desc": "Ham radio station"},    # ~5W
    "VHF Business":         {"tx_dbm": 33, "desc": "VHF business radio"},  # ~2W
    "UHF Business/PMR":     {"tx_dbm": 30, "desc": "PMR radio"},           # ~1W
    "UHF Public Safety":    {"tx_dbm": 37, "desc": "Public safety radio"}, # ~5W

    # WiFi / BT
    "WiFi":                {"tx_dbm": 20, "desc": "WiFi router/device"},    # ~100mW
    "Bluetooth":           {"tx_dbm": 10, "desc": "Bluetooth device"},      # ~10mW
    "2.4 GHz WiFi/BT":    {"tx_dbm": 20, "desc": "WiFi/BT device"},
    "5 GHz WiFi":          {"tx_dbm": 23, "desc": "WiFi device"},          # ~200mW

    # Drone / FPV
    "5.8 GHz FPV/WiFi":    {"tx_dbm": 27, "desc": "Drone/FPV transmitter"}, # ~500mW
    "FPV":                 {"tx_dbm": 27, "desc": "FPV video transmitter"},

    # GPS
    "GPS L1":              {"tx_dbm": -10, "desc": "GPS satellite"},        # Very weak at ground

    # Military / Other
    "UHF Military":        {"tx_dbm": 40, "desc": "Military transmitter"},
    "S-Band/Military":     {"tx_dbm": 37, "desc": "S-Band transmitter"},
    "TETRA":               {"tx_dbm": 37, "desc": "TETRA base station"},    # ~5W
}


def estimate_distance_km(freq_mhz, rx_power_dbfs, signal_type=None, tx_dbm=None):
    """
    Estimate distance to transmitter using Free-Space Path Loss.

    Args:
        freq_mhz: Signal frequency in MHz
        rx_power_dbfs: Received power in dBFS (from hackrf_sweep)
        signal_type: Signal classification (for TX power lookup)
        tx_dbm: Override TX power in dBm (EIRP)

    Returns:
        dict with distance estimates and metadata
    """
    # HackRF calibration: approximate mapping from dBFS to dBm
    # HackRF with AMP=ON, LNA=40, VGA=32:
    #   - Noise floor ≈ -70 dBFS ≈ -100 dBm
    #   - Full scale ≈ 0 dBFS ≈ -30 dBm
    #   So: rx_dbm ≈ rx_power_dbfs + (-30)
    # This is approximate — varies with gain settings and frequency
    rx_dbm = rx_power_dbfs - 30

    # Get TX power
    if tx_dbm is None and signal_type:
        # Try exact match, then partial match
        for key in TX_POWERS:
            if key.lower() in signal_type.lower() or signal_type.lower() in key.lower():
                tx_dbm = TX_POWERS[key]["tx_dbm"]
                break
        if tx_dbm is None:
            # Default based on frequency band
            if 88 <= freq_mhz <= 108:
                tx_dbm = 70   # FM broadcast
            elif 174 <= freq_mhz <= 230:
                tx_dbm = 60   # DVB-T
            elif 800 <= freq_mhz <= 960:
                tx_dbm = 43   # Cellular
            elif 1700 <= freq_mhz <= 2000:
                tx_dbm = 40   # GSM 1800
            elif 2400 <= freq_mhz <= 2500:
                tx_dbm = 20   # WiFi/drone
            elif 5150 <= freq_mhz <= 5900:
                tx_dbm = 23   # WiFi 5 GHz
            else:
                tx_dbm = 30   # Default 1W

    if tx_dbm is None:
        tx_dbm = 30  # Fallback: 1W

    # FSPL = TX + TX_gain + RX_gain - RX_power
    # FSPL = 32.44 + 20*log10(d_km) + 20*log10(f_MHz)
    # d_km = 10^((FSPL - 32.44 - 20*log10(f_MHz)) / 20)

    # Assume: TX antenna gain = 0 dBi (omni), RX antenna gain = 2 dBi (stock)
    tx_antenna_gain = 0  # dBi
    rx_antenna_gain = 2  # dBi

    fspl_db = tx_dbm + tx_antenna_gain + rx_antenna_gain - rx_dbm

    # Clamp FSPL to reasonable range
    fspl_db = max(20, min(160, fspl_db))

    freq_log = 20 * math.log10(max(freq_mhz, 1))
    distance_km = 10 ** ((fspl_db - 32.44 - freq_log) / 20)

    # Sanity checks
    if distance_km < 0.001:
        distance_km = 0.001
    if distance_km > 500:
        distance_km = 500

    # Distance category
    if distance_km < 0.1:
        category = "VERY CLOSE (<100m)"
    elif distance_km < 0.5:
        category = "CLOSE (100-500m)"
    elif distance_km < 2:
        category = "NEARBY (0.5-2 km)"
    elif distance_km < 10:
        category = "MEDIUM (2-10 km)"
    elif distance_km < 30:
        category = "FAR (10-30 km)"
    else:
        category = "VERY FAR (>30 km)"

    return {
        "distance_km": round(distance_km, 2),
        "distance_m": round(distance_km * 1000),
        "category": category,
        "tx_dbm": tx_dbm,
        "rx_dbm": round(rx_dbm, 1),
        "fspl_db": round(fspl_db, 1),
        "rx_power_dbfs": rx_power_dbfs,
    }


def classify_territory(signals):
    """
    Classify environment as countryside vs city based on signal density.

    Args:
        signals: list of classified signal dicts with 'freq_mhz', 'peak', 'level', 'detail'

    Returns:
        dict with territory classification and reasoning
    """
    # Count signals by type
    cellular_count = 0
    wifi_count = 0
    broadcast_count = 0
    total_strong = 0  # Signals above -20 dBFS
    total_very_strong = 0  # Signals above -10 dBFS

    cellular_bands = set()
    wifi_channels = set()

    for s in signals:
        freq = s.get('freq_mhz', 0)
        power = s.get('peak', -100)
        detail = s.get('detail', '')

        if power > -20:
            total_strong += 1
        if power > -10:
            total_very_strong += 1

        # Cellular indicators
        if any(x in detail for x in ['GSM', 'LTE', 'Cellular', '3G', '4G']):
            cellular_count += 1
            if 935 <= freq <= 960:
                cellular_bands.add('GSM900')
            elif 1805 <= freq <= 1880:
                cellular_bands.add('GSM1800')
            elif 2100 <= freq <= 2200:
                cellular_bands.add('3G')
            elif 2500 <= freq <= 2700:
                cellular_bands.add('LTE2600')
            elif 700 <= freq <= 800:
                cellular_bands.add('LTE700/800')

        # WiFi indicators
        if any(x in detail for x in ['WiFi', 'Bluetooth']):
            wifi_count += 1
            wifi_channels.add(int(round(freq)))

        # Broadcast
        if any(x in detail for x in ['FM Radio', 'DVB-T', 'DAB']):
            broadcast_count += 1

    # Classification logic
    score = 0  # Negative = countryside, Positive = city

    # Many cellular bands active = city
    if len(cellular_bands) >= 4:
        score += 3
    elif len(cellular_bands) >= 2:
        score += 1

    # Many WiFi networks = city
    if wifi_count >= 10:
        score += 3
    elif wifi_count >= 5:
        score += 2
    elif wifi_count >= 2:
        score += 1

    # Very strong signals = close to infrastructure = city
    if total_very_strong >= 5:
        score += 2
    elif total_very_strong >= 2:
        score += 1

    # High signal density
    if total_strong >= 20:
        score += 2
    elif total_strong >= 10:
        score += 1

    # Countryside indicators
    if wifi_count <= 1 and cellular_count <= 2:
        score -= 2
    if total_strong <= 5:
        score -= 1

    # Classify
    if score >= 4:
        territory = "CITY"
        desc = "Dense urban environment — many cellular towers, WiFi networks, and strong signals"
    elif score >= 2:
        territory = "SUBURBAN"
        desc = "Suburban area — moderate cellular coverage, some WiFi networks"
    elif score >= 0:
        territory = "TOWNSHIP"
        desc = "Small town — limited cellular coverage, few WiFi networks"
    else:
        territory = "COUNTRYSIDE"
        desc = "Rural area — sparse cellular coverage, minimal WiFi, mostly broadcast signals"

    return {
        "territory": territory,
        "description": desc,
        "score": score,
        "cellular_bands": sorted(cellular_bands),
        "cellular_count": cellular_count,
        "wifi_count": wifi_count,
        "wifi_channels": sorted(wifi_channels),
        "broadcast_count": broadcast_count,
        "total_strong": total_strong,
        "total_very_strong": total_very_strong,
    }


def format_distance(dist_result):
    """Format distance estimate as human-readable string."""
    d = dist_result["distance_m"] if dist_result["distance_m"] < 1000 else dist_result["distance_km"]
    unit = "m" if dist_result["distance_m"] < 1000 else "km"
    if dist_result["distance_m"] < 1000:
        return f"~{d:.0f}{unit} ({dist_result['category']})"
    else:
        return f"~{d:.1f}{unit} ({dist_result['category']})"


if __name__ == "__main__":
    # Test distance estimation
    test_cases = [
        (950, -6, "GSM 900 downlink"),
        (2437, -15, "WiFi Ch 6"),
        (5800, -30, "5.8 GHz FPV/WiFi"),
        (100, -12, "FM Radio"),
        (5800, -10, "FPV transmitter"),
    ]
    print("=== Distance Estimation Tests ===")
    for freq, power, sig_type in test_cases:
        d = estimate_distance_km(freq, power, sig_type)
        print(f"  {freq} MHz {sig_type}: {format_distance(d)} (TX={d['tx_dbm']}dBm, RX={d['rx_dbm']}dBm)")
