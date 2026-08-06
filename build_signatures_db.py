#!/usr/bin/env python3
"""
Build unified rflord signatures database from all sources.

Sources:
- spy_db.py: 81 surveillance device RF signatures
- drone_rf_db.py: 45 drone RF signatures
- rf_protocols.json: 692 Sub-GHz protocol signatures (ringmast4r)
- /opt/artemis/Data/db.csv: 432 signal database (Artemis 3)
- AirHound defaults.rs: 115+ MAC OUIs, BLE UUIDs, SSID patterns

Output: ~/.local/share/rflord/signatures.db (SQLite)
"""
import json
import os
import sqlite3
import sys
import time

DB_PATH = os.path.expanduser("~/.local/share/rflord/signatures.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def create_schema(conn):
    """Create unified signature database schema."""
    conn.executescript("""
        -- RF frequency-based signatures (from spy_db, drone_rf, artemis, rf_protocols)
        CREATE TABLE IF NOT EXISTS signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            freq_low_hz REAL,
            freq_high_hz REAL,
            bandwidth_hz REAL,
            modulation TEXT,
            category TEXT NOT NULL DEFAULT 'unknown',
            subcategory TEXT,
            threat_level INTEGER DEFAULT 3,
            icon TEXT,
            manufacturer TEXT,
            country TEXT,
            description TEXT,
            source TEXT NOT NULL,
            source_id TEXT,
            encoding TEXT,
            bits TEXT,
            checksum TEXT,
            url TEXT,
            active INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_sig_freq ON signatures(freq_low_hz, freq_high_hz);
        CREATE INDEX IF NOT EXISTS idx_sig_category ON signatures(category);
        CREATE INDEX IF NOT EXISTS idx_sig_source ON signatures(source);
        CREATE INDEX IF NOT EXISTS idx_sig_name ON signatures(name);

        -- MAC OUI prefixes (from AirHound)
        CREATE TABLE IF NOT EXISTS mac_oui (
            oui TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            category TEXT DEFAULT 'unknown',
            source TEXT DEFAULT 'airhound'
        );

        -- BLE signatures (from AirHound)
        CREATE TABLE IF NOT EXISTS ble_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sig_type TEXT NOT NULL,
            value TEXT NOT NULL,
            description TEXT,
            category TEXT DEFAULT 'unknown',
            source TEXT DEFAULT 'airhound'
        );

        CREATE INDEX IF NOT EXISTS idx_ble_type ON ble_signatures(sig_type);
        CREATE INDEX IF NOT EXISTS idx_ble_value ON ble_signatures(value);

        -- WiFi SSID patterns (from AirHound)
        CREATE TABLE IF NOT EXISTS ssid_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            match_type TEXT NOT NULL,
            description TEXT,
            category TEXT DEFAULT 'unknown',
            source TEXT DEFAULT 'airhound'
        );

        -- Rules: named device detections combining signatures
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            category TEXT DEFAULT 'unknown',
            expression TEXT
        );

        -- Metadata
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

def import_spy_db(conn):
    """Import spy_db.py signatures."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from spy_db import SPY_DEVICES

    cur = conn.cursor()
    count = 0
    for entry in SPY_DEVICES:
        freq_low, freq_high, name, icon, threat = entry
        cur.execute("""
            INSERT INTO signatures (name, freq_low_hz, freq_high_hz, category, threat_level, icon, source, source_id)
            VALUES (?, ?, ?, 'surveillance', ?, ?, 'spy_db', ?)
        """, (name, freq_low * 1e6, freq_high * 1e6, threat, icon, f"spy_{count}"))
        count += 1
    print(f"  spy_db: {count} signatures")
    return count

def import_drone_rf(conn):
    """Import drone_rf_db.py signatures."""
    from drone_rf_db import DRONE_SIGNATURES

    cur = conn.cursor()
    count = 0
    for entry in DRONE_SIGNATURES:
        freq_low, freq_high, bw_khz, modulation, name, description = entry
        cur.execute("""
            INSERT INTO signatures (name, freq_low_hz, freq_high_hz, bandwidth_hz, modulation,
                                    category, description, source, source_id)
            VALUES (?, ?, ?, ?, ?, 'drone', ?, 'drone_rf', ?)
        """, (name, freq_low * 1e6, freq_high * 1e6, bw_khz * 1e3, modulation,
              description, f"drone_{count}"))
        count += 1
    print(f"  drone_rf: {count} signatures")
    return count

def import_rf_protocols(conn):
    """Import rf_protocols.json signatures."""
    proto_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rf_protocols.json")
    if not os.path.exists(proto_path):
        proto_path = os.path.expanduser("~/.local/share/rflord/rf_protocols.json")
    if not os.path.exists(proto_path):
        print("  rf_protocols: NOT FOUND, skipping")
        return 0

    with open(proto_path) as f:
        db = json.load(f)

    cur = conn.cursor()
    count = 0
    for d in db.get("devices", []):
        freq_hz = d.get("frequency")
        if freq_hz and isinstance(freq_hz, str):
            try:
                freq_hz = float(freq_hz)
            except:
                freq_hz = None

        cur.execute("""
            INSERT INTO signatures (name, freq_low_hz, freq_high_hz, modulation, category,
                                    manufacturer, description, source, source_id, encoding, bits, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'rf_protocols', ?, ?, ?, ?)
        """, (
            d.get("name", ""),
            freq_hz,
            freq_hz,
            d.get("modulation"),
            d.get("category", "unknown"),
            d.get("manufacturer"),
            None,
            d.get("device_id"),
            d.get("encoding"),
            d.get("bits"),
            d.get("checksum"),
        ))
        count += 1
    print(f"  rf_protocols: {count} signatures")
    return count

def import_artemis(conn):
    """Import Artemis 3 db.csv."""
    artemis_path = "/opt/artemis/Data/db.csv"
    if not os.path.exists(artemis_path):
        print("  artemis: NOT FOUND, skipping")
        return 0

    cur = conn.cursor()
    count = 0
    with open(artemis_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.strip().split("*")
            if len(parts) < 8:
                continue
            name = parts[0].strip("'\"")
            try:
                freq_low = int(parts[1]) if parts[1] else 0
                freq_high = int(parts[2]) if parts[2] else 0
            except:
                continue
            if freq_low <= 0 and freq_high <= 0:
                continue

            modulation = parts[3] if len(parts) > 3 else ""
            bandwidth = parts[4] if len(parts) > 4 else ""
            country = parts[6] if len(parts) > 6 else ""
            url = parts[7] if len(parts) > 7 else ""
            description = parts[8][:200] if len(parts) > 8 else ""
            sig_type = parts[9] if len(parts) > 9 else ""

            try:
                bw_hz = float(bandwidth) if bandwidth else None
            except:
                bw_hz = None

            cur.execute("""
                INSERT INTO signatures (name, freq_low_hz, freq_high_hz, bandwidth_hz, modulation,
                                        category, country, url, description, source, source_id)
                VALUES (?, ?, ?, ?, ?, 'signal', ?, ?, ?, 'artemis', ?)
            """, (name, freq_low, freq_high, bw_hz, modulation, country, url, description,
                  f"artemis_{count}"))
            count += 1
    print(f"  artemis: {count} signatures")
    return count

def import_airhound_mac(conn):
    """Import AirHound MAC OUI database."""
    # Hardcoded from AirHound src/defaults.rs
    oui_data = [
        ("B4:1E:52", "Flock Safety", "alpr_camera"),
        ("58:8E:81", "Silicon Labs", "camera_hardware"),
        ("CC:CC:CC", "Silicon Labs", "camera_hardware"),
        ("EC:1B:BD", "Silicon Labs", "camera_hardware"),
        ("90:35:EA", "Silicon Labs", "camera_hardware"),
        ("04:0D:84", "Silicon Labs", "camera_hardware"),
        ("F0:82:C0", "Silicon Labs", "camera_hardware"),
        ("1C:34:F1", "Silicon Labs", "camera_hardware"),
        ("38:5B:44", "Silicon Labs", "camera_hardware"),
        ("94:34:69", "Silicon Labs", "camera_hardware"),
        ("B4:E3:F9", "Silicon Labs", "camera_hardware"),
        ("70:C9:4E", "Silicon Labs", "camera_hardware"),
        ("3C:91:80", "Silicon Labs", "camera_hardware"),
        ("D8:F3:BC", "Silicon Labs", "camera_hardware"),
        ("80:30:49", "Silicon Labs", "camera_hardware"),
        ("14:5A:FC", "Silicon Labs", "camera_hardware"),
        ("74:4C:A1", "Silicon Labs", "camera_hardware"),
        ("08:3A:88", "Silicon Labs", "camera_hardware"),
        ("9C:2F:9D", "Silicon Labs", "camera_hardware"),
        ("94:08:53", "Silicon Labs", "camera_hardware"),
        ("E4:AA:EA", "Silicon Labs", "camera_hardware"),
        ("70:1A:D5", "Avigilon Alta", "surveillance_camera"),
        ("00:40:8C", "Axis Communications", "surveillance_camera"),
        ("AC:CC:8E", "Axis Communications", "surveillance_camera"),
        ("B8:A4:4F", "Axis Communications", "surveillance_camera"),
        ("E8:27:25", "Axis Communications", "surveillance_camera"),
        ("00:13:56", "FLIR Radiation", "thermal_camera"),
        ("00:40:7F", "FLIR Systems", "thermal_camera"),
        ("00:1B:D8", "FLIR Systems", "thermal_camera"),
        ("00:13:E2", "GeoVision", "surveillance_camera"),
        ("44:B4:23", "Hanwha Vision", "surveillance_camera"),
        ("8C:1D:55", "Hanwha Vision", "surveillance_camera"),
        ("E4:30:22", "Hanwha Vision", "surveillance_camera"),
        ("00:10:BE", "March Networks", "surveillance_camera"),
        ("00:12:81", "March Networks", "surveillance_camera"),
        ("00:03:C5", "Mobotix", "surveillance_camera"),
        ("00:1C:27", "Sunell Electronics", "surveillance_camera"),
        ("DE:AD:BE", "Pwnagotchi", "hacking_tool"),
        ("90:3A:E6", "Open Drone ID", "drone"),
        # China Dragon Technology (many camera manufacturers)
        ("1C:79:2D", "China Dragon Technology", "camera_hardware"),
        ("3C:3B:AD", "China Dragon Technology", "camera_hardware"),
        ("40:9C:A7", "China Dragon Technology", "camera_hardware"),
        ("54:AE:BC", "China Dragon Technology", "camera_hardware"),
        ("5C:8A:AE", "China Dragon Technology", "camera_hardware"),
        ("6C:05:D3", "China Dragon Technology", "camera_hardware"),
        ("A4:6B:40", "China Dragon Technology", "camera_hardware"),
        ("A8:4F:A4", "China Dragon Technology", "camera_hardware"),
        ("A8:A0:92", "China Dragon Technology", "camera_hardware"),
        ("B0:AC:82", "China Dragon Technology", "camera_hardware"),
        ("BC:2B:02", "China Dragon Technology", "camera_hardware"),
        ("C0:E3:50", "China Dragon Technology", "camera_hardware"),
        ("C8:26:E2", "China Dragon Technology", "camera_hardware"),
        ("C8:8A:D8", "China Dragon Technology", "camera_hardware"),
        ("00:7E:56", "China Dragon Technology", "camera_hardware"),
        ("04:39:26", "China Dragon Technology", "camera_hardware"),
        ("24:B7:2A", "China Dragon Technology", "camera_hardware"),
        ("3C:7A:AA", "China Dragon Technology", "camera_hardware"),
        ("44:EF:BF", "China Dragon Technology", "camera_hardware"),
        ("78:8A:86", "China Dragon Technology", "camera_hardware"),
        ("94:E0:D6", "China Dragon Technology", "camera_hardware"),
        ("A0:67:20", "China Dragon Technology", "camera_hardware"),
        ("A0:9D:C1", "China Dragon Technology", "camera_hardware"),
        ("A8:43:A4", "China Dragon Technology", "camera_hardware"),
        ("D0:A4:6F", "China Dragon Technology", "camera_hardware"),
        ("E0:51:D8", "China Dragon Technology", "camera_hardware"),
        ("E0:75:26", "China Dragon Technology", "camera_hardware"),
        ("20:F4:1B", "Shenzhen Bilian", "camera_hardware"),
        ("28:F3:66", "Shenzhen Bilian", "camera_hardware"),
        ("3C:33:00", "Shenzhen Bilian", "camera_hardware"),
        ("44:33:4C", "Shenzhen Bilian", "camera_hardware"),
        ("AC:A2:13", "Shenzhen Bilian", "camera_hardware"),
        ("48:05:60", "Meta Platforms", "vr_headset"),
        ("50:99:03", "Meta Platforms", "vr_headset"),
        ("78:C4:FA", "Meta Platforms", "vr_headset"),
        ("80:F3:EF", "Meta Platforms", "vr_headset"),
        ("84:57:F7", "Meta Platforms", "vr_headset"),
        ("88:25:08", "Meta Platforms", "vr_headset"),
        ("94:F9:29", "Meta Platforms", "vr_headset"),
        ("B4:17:A8", "Meta Platforms", "vr_headset"),
        ("C0:DD:8A", "Meta Platforms", "vr_headset"),
        ("CC:A1:74", "Meta Platforms", "vr_headset"),
        ("D0:B3:C2", "Meta Platforms", "vr_headset"),
        ("D4:D6:59", "Meta Platforms", "vr_headset"),
    ]

    cur = conn.cursor()
    for oui, vendor, cat in oui_data:
        cur.execute("INSERT OR IGNORE INTO mac_oui (oui, vendor_name, category, source) VALUES (?, ?, ?, 'airhound')",
                    (oui, vendor, cat))
    print(f"  airhound MAC OUIs: {len(oui_data)}")
    return len(oui_data)

def import_airhound_ble(conn):
    """Import AirHound BLE signatures."""
    ble_data = [
        ("service_uuid", "3100", "Raven GPS service", "surveillance"),
        ("service_uuid", "3200", "Raven Power service", "surveillance"),
        ("service_uuid", "3300", "Raven Network service", "surveillance"),
        ("service_uuid", "3400", "Raven Upload service", "surveillance"),
        ("service_uuid", "3500", "Raven Error service", "surveillance"),
        ("service_uuid", "FFFA", "ASTM F3411 Open Drone ID", "drone"),
        ("manufacturer_id", "2504", "XUNTONG Technology (Flock Safety)", "alpr_camera"),
        ("name_pattern", "flock", "Flock Safety BLE device", "alpr_camera"),
        ("name_pattern", "penguin", "Penguin device", "tracker"),
        ("name_pattern", "fs ext battery", "Flock Safety external battery", "alpr_camera"),
        ("name_pattern", "hc-03", "HC-03 BLE module (card skimmer)", "skimmer"),
        ("name_pattern", "hc-05", "HC-05 BLE module (card skimmer)", "skimmer"),
        ("name_pattern", "hc-06", "HC-06 BLE module (card skimmer)", "skimmer"),
        ("ad_bytes", "4C001219", "Apple FindMy / AirTag advertisement", "tracker"),
        ("ad_bytes", "8030", "Flipper Zero (White)", "hacking_tool"),
        ("ad_bytes", "8130", "Flipper Zero (Black)", "hacking_tool"),
    ]

    cur = conn.cursor()
    for sig_type, value, desc, cat in ble_data:
        cur.execute("INSERT INTO ble_signatures (sig_type, value, description, category, source) VALUES (?, ?, ?, ?, 'airhound')",
                    (sig_type, value, desc, cat))
    print(f"  airhound BLE signatures: {len(ble_data)}")
    return len(ble_data)

def import_airhound_ssid(conn):
    """Import AirHound SSID patterns."""
    ssid_data = [
        ("^Flock-[0-9A-Fa-f]{6}$", "regex", "Flock Safety camera WiFi AP", "alpr_camera"),
        ("FS Ext Battery", "exact", "Flock Safety external battery WiFi", "alpr_camera"),
        ("flock", "contains", "Any SSID containing 'flock'", "alpr_camera"),
        ("^Penguin-[0-9]{10}$", "regex", "Penguin device WiFi", "tracker"),
    ]

    cur = conn.cursor()
    for pattern, match_type, desc, cat in ssid_data:
        cur.execute("INSERT INTO ssid_patterns (pattern, match_type, description, category, source) VALUES (?, ?, ?, ?, 'airhound')",
                    (pattern, match_type, desc, cat))
    print(f"  airhound SSID patterns: {len(ssid_data)}")
    return len(ssid_data)

def build_rules(conn):
    """Build named device detection rules."""
    rules = [
        ("Flock Safety Camera", "Flock Safety ALPR camera detected via WiFi/BLE/MAC", "alpr_camera",
         json.dumps({"anyOf": ["mac_oui:B4:1E:52", "ssid_pattern:^Flock-", "ble_name:flock"]})),
        ("Raven Acoustic Sensor", "ShotSpotter/Raven gunshot detection device", "surveillance",
         json.dumps({"anyOf": ["ble_uuid:3100", "ble_uuid:3200", "ble_uuid:3300", "ble_uuid:3400", "ble_uuid:3500"]})),
        ("Apple AirTag", "Apple AirTag or FindMy-compatible tracker", "tracker",
         json.dumps({"ble_ad": "4C001219"})),
        ("Flipper Zero", "Flipper Zero multi-tool device", "hacking_tool",
         json.dumps({"anyOf": ["ble_ad:8030", "ble_ad:8130"]})),
        ("Card Skimmer", "Bluetooth card skimmer with HC-series module", "skimmer",
         json.dumps({"anyOf": ["ble_name:hc-03", "ble_name:hc-05", "ble_name:hc-06"]})),
        ("Pwnagotchi", "WiFi hacking tool with fixed MAC DE:AD:BE:EF:DE:AD", "hacking_tool",
         json.dumps({"mac_oui": "DE:AD:BE"})),
        ("Open Drone ID", "Drone with WiFi/BLE Remote ID (ASTM F3411)", "drone",
         json.dumps({"anyOf": ["ble_uuid:FFFA", "mac_oui:90:3A:E6"]})),
    ]

    cur = conn.cursor()
    for name, desc, cat, expr in rules:
        cur.execute("INSERT OR IGNORE INTO rules (name, description, category, expression) VALUES (?, ?, ?, ?)",
                    (name, desc, cat, expr))
    print(f"  rules: {len(rules)}")
    return len(rules)

def main():
    print(f"Building unified signature database: {DB_PATH}")

    # Remove old DB
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    print("\nCreating schema...")
    create_schema(conn)

    total = 0
    print("\nImporting sources:")
    total += import_spy_db(conn)
    total += import_drone_rf(conn)
    total += import_rf_protocols(conn)
    total += import_artemis(conn)
    total += import_airhound_mac(conn)
    total += import_airhound_ble(conn)
    total += import_airhound_ssid(conn)
    total += build_rules(conn)

    # Set metadata
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('version', '1.0.0')")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('built', ?)", (time.strftime('%Y-%m-%d %H:%M:%S'),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('total_entries', ?)", (str(total),))

    conn.commit()

    # Verify
    print("\nVerification:")
    for table in ['signatures', 'mac_oui', 'ble_signatures', 'ssid_patterns', 'rules']:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows")

    conn.close()

    # Get file size
    size_kb = os.path.getsize(DB_PATH) / 1024
    print(f"\nDone. {total} total entries. DB size: {size_kb:.0f} KB")
    print(f"Path: {DB_PATH}")

if __name__ == "__main__":
    main()
