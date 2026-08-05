"""RF Protocol Database — 692 Sub-GHz device signatures from ringmast4r/RF-Protocol-Database.

Covers 300-928 MHz ISM spectrum: garage doors, weather stations, car key fobs,
TPMS sensors, security alarms, smart meters, IoT devices.

Sources: URH-NG, Zero-Sploit FlipperZero-Subghz-DB, RTL_433.
"""
import json
import os

DB_PATH = os.path.expanduser("~/.local/share/rflord/rf_protocols.json")
_db = None

def _load_db():
    global _db
    if _db is not None:
        return _db
    if not os.path.exists(DB_PATH):
        # Try local copy
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rf_protocols.json")
        if os.path.exists(local):
            with open(local) as f:
                _db = json.load(f)
                return _db
        _db = {"devices": [], "categories": {}}
        return _db
    with open(DB_PATH) as f:
        _db = json.load(f)
    return _db

def get_protocol_count():
    """Return total number of protocols in database."""
    db = _load_db()
    return db.get("total_devices", len(db.get("devices", [])))

def identify_by_freq(freq_mhz, tolerance_mhz=0.5):
    """Find all protocols matching a frequency (in MHz).
    
    Args:
        freq_mhz: frequency in MHz
        tolerance_mhz: match tolerance (default 0.5 MHz)
    Returns:
        list of dicts with name, category, manufacturer, modulation
    """
    db = _load_db()
    freq_hz = freq_mhz * 1e6
    results = []
    for d in db.get("devices", []):
        f = d.get("frequency")
        if f is None:
            continue
        if isinstance(f, str):
            try:
                f = float(f)
            except:
                continue
        f_mhz = f / 1e6
        if abs(f_mhz - freq_mhz) <= tolerance_mhz:
            results.append({
                "name": d.get("name", ""),
                "category": d.get("category", ""),
                "manufacturer": d.get("manufacturer", ""),
                "modulation": d.get("modulation", ""),
                "bits": d.get("bits", ""),
                "frequency_mhz": f_mhz,
            })
    # Sort by category
    results.sort(key=lambda x: x["category"])
    return results

def get_categories():
    """Return dict of category -> count."""
    db = _load_db()
    return db.get("categories", {})

def search_by_name(query):
    """Search protocols by name (case-insensitive substring)."""
    db = _load_db()
    query_lower = query.lower()
    results = []
    for d in db.get("devices", []):
        name = d.get("name", "")
        if query_lower in name.lower():
            results.append({
                "name": name,
                "category": d.get("category", ""),
                "manufacturer": d.get("manufacturer", ""),
                "modulation": d.get("modulation", ""),
                "frequency_mhz": d.get("frequency", 0) / 1e6 if d.get("frequency") else None,
            })
    return results
