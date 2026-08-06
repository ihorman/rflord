"""Unified signature database — SQLite-backed signal identification.

Consolidates spy_db, drone_rf_db, rf_protocols, Artemis, and AirHound
into a single searchable database.

Usage:
    from signatures_db import SignaturesDB
    db = SignaturesDB()
    matches = db.identify_freq(433.92)
    matches = db.identify_mac("B4:1E:52")
    matches = db.identify_ssid("Flock-A1B2C3")
"""
import json
import os
import sqlite3

_default_db = os.path.expanduser("~/.local/share/rflord/signatures.db")
_local_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signatures.db")
DB_PATH = _default_db if os.path.exists(_default_db) else _local_db


class SignaturesDB:
    """Unified signature database for signal identification."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- RF frequency lookup ---

    def identify_freq(self, freq_mhz, tolerance_mhz=0.5):
        """Find all signatures matching a frequency (in MHz).

        Returns list of dicts with name, category, modulation, threat_level, etc.
        """
        freq_hz = freq_mhz * 1e6
        tol_hz = tolerance_mhz * 1e6
        rows = self.conn.execute("""
            SELECT * FROM signatures
            WHERE freq_low_hz IS NOT NULL
              AND freq_low_hz <= ? + ?
              AND freq_high_hz >= ? - ?
            ORDER BY threat_level ASC
        """, (freq_hz, tol_hz, freq_hz, tol_hz)).fetchall()
        return [dict(r) for r in rows]

    def identify_freq_strict(self, freq_mhz):
        """Find signatures where frequency falls strictly within range."""
        freq_hz = freq_mhz * 1e6
        rows = self.conn.execute("""
            SELECT * FROM signatures
            WHERE freq_low_hz IS NOT NULL
              AND freq_low_hz <= ?
              AND freq_high_hz >= ?
            ORDER BY threat_level ASC
        """, (freq_hz, freq_hz)).fetchall()
        return [dict(r) for r in rows]

    # --- MAC OUI lookup ---

    def identify_mac(self, mac_address):
        """Look up MAC OUI prefix (e.g., 'B4:1E:52' or 'B4:1E:52:01:02:03')."""
        oui = mac_address.upper()[:8]  # First 3 bytes
        rows = self.conn.execute(
            "SELECT * FROM mac_oui WHERE oui = ?", (oui,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- BLE lookup ---

    def identify_ble_uuid(self, uuid_16):
        """Look up BLE service UUID (16-bit hex string, e.g., '3100')."""
        rows = self.conn.execute(
            "SELECT * FROM ble_signatures WHERE sig_type = 'service_uuid' AND value = ?",
            (uuid_16.upper(),)
        ).fetchall()
        return [dict(r) for r in rows]

    def identify_ble_name(self, name):
        """Look up BLE device name (case-insensitive contains)."""
        rows = self.conn.execute(
            "SELECT * FROM ble_signatures WHERE sig_type = 'name_pattern' AND ? LIKE '%' || value || '%'",
            (name.lower(),)
        ).fetchall()
        return [dict(r) for r in rows]

    def identify_ble_mfr(self, company_id):
        """Look up BLE manufacturer ID."""
        rows = self.conn.execute(
            "SELECT * FROM ble_signatures WHERE sig_type = 'manufacturer_id' AND value = ?",
            (str(company_id),)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- SSID lookup ---

    def identify_ssid(self, ssid):
        """Look up WiFi SSID against all patterns."""
        import re
        rows = self.conn.execute("SELECT * FROM ssid_patterns").fetchall()
        matches = []
        for r in rows:
            r = dict(r)
            pattern = r["pattern"]
            match_type = r["match_type"]
            if match_type == "exact" and ssid == pattern:
                matches.append(r)
            elif match_type == "contains" and pattern.lower() in ssid.lower():
                matches.append(r)
            elif match_type == "prefix" and ssid.startswith(pattern):
                matches.append(r)
            elif match_type == "regex":
                try:
                    if re.match(pattern, ssid, re.IGNORECASE):
                        matches.append(r)
                except re.error:
                    pass
        return matches

    # --- Rule evaluation ---

    def evaluate_rules(self, matched_signatures):
        """Evaluate rules against a set of matched signature identifiers.

        Args:
            matched_signatures: set of signature identifiers like "mac_oui:B4:1E:52", "ble_uuid:3100"
        Returns:
            list of matched rule names
        """
        rows = self.conn.execute("SELECT * FROM rules").fetchall()
        matched_rules = []
        for r in rows:
            r = dict(r)
            expr = json.loads(r["expression"]) if r["expression"] else {}
            if self._eval_expr(expr, matched_signatures):
                matched_rules.append(r)
        return matched_rules

    def _eval_expr(self, expr, sigs):
        """Evaluate a boolean expression against matched signatures."""
        if isinstance(expr, str):
            return expr in sigs
        if "anyOf" in expr:
            return any(self._eval_expr(item, sigs) for item in expr["anyOf"])
        if "allOf" in expr:
            return all(self._eval_expr(item, sigs) for item in expr["allOf"])
        if "not" in expr:
            return not self._eval_expr(expr["not"], sigs)
        return False

    # --- Statistics ---

    def stats(self):
        """Return database statistics."""
        tables = {}
        for table in ["signatures", "mac_oui", "ble_signatures", "ssid_patterns", "rules"]:
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            tables[table] = count

        categories = {}
        rows = self.conn.execute(
            "SELECT category, COUNT(*) as cnt FROM signatures GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        for r in rows:
            categories[r["category"]] = r["cnt"]

        sources = {}
        rows = self.conn.execute(
            "SELECT source, COUNT(*) as cnt FROM signatures GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        for r in rows:
            sources[r["source"]] = r["cnt"]

        meta = {}
        for r in self.conn.execute("SELECT key, value FROM meta").fetchall():
            meta[r["key"]] = r["value"]

        return {
            "tables": tables,
            "categories": categories,
            "sources": sources,
            "meta": meta,
        }

    # --- Search ---

    def search(self, query, limit=20):
        """Search signatures by name (case-insensitive LIKE)."""
        pattern = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM signatures WHERE name LIKE ? LIMIT ?", (pattern, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_category(self, category, limit=100):
        """Get signatures by category."""
        rows = self.conn.execute(
            "SELECT * FROM signatures WHERE category = ? LIMIT ?", (category, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_threats(self, max_level=1):
        """Get high-threat signatures (level 0-1)."""
        rows = self.conn.execute(
            "SELECT * FROM signatures WHERE threat_level <= ? AND threat_level IS NOT NULL ORDER BY threat_level",
            (max_level,)
        ).fetchall()
        return [dict(r) for r in rows]
