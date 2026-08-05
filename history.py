"""SQLite-backed signal history tracker for rflord."""

import csv
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path


DB_DEFAULT_PATH = os.path.expanduser("~/.local/share/rflord/signals.db")

# Frequency band definitions (Hz)
BANDS = {
    "fm_broadcast": (88e6, 108e6),
    "vhf_low": (30e6, 50e6),
    "vhf_mid": (137e6, 174e6),
    "vhf_high": (216e6, 225e6),
    "uhf": (400e6, 512e6),
    "cellular_850": (824e6, 894e6),
    "cellular_1900": (1850e6, 1990e6),
    "wifi_24": (2400e6, 2500e6),
    "wifi_5": (5150e6, 5850e6),
    "ism_433": (433e6, 435e6),
    "ism_915": (902e6, 928e6),
}


class SignalHistory:
    """Track RF signal history in SQLite for trend analysis and reporting."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_DEFAULT_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init_db(self):
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                device TEXT,
                signal_count INTEGER DEFAULT 0,
                sus_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                freq_mhz REAL NOT NULL,
                peak_dbfs REAL,
                avg_dbfs REAL,
                std REAL,
                classification TEXT,
                signal_type TEXT,
                identification TEXT,
                distance REAL,
                first_seen REAL,
                last_seen REAL,
                FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS signal_trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                freq_mhz REAL NOT NULL,
                peak_dbfs REAL NOT NULL,
                timestamp REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signals_freq ON signals(freq_mhz);
            CREATE INDEX IF NOT EXISTS idx_signals_scan ON signals(scan_id);
            CREATE INDEX IF NOT EXISTS idx_trends_freq ON signal_trends(freq_mhz);
            CREATE INDEX IF NOT EXISTS idx_trends_ts ON signal_trends(timestamp);
            CREATE INDEX IF NOT EXISTS idx_scans_ts ON scans(timestamp);
        """)
        self.conn.commit()

    def record_scan(self, signals, device=None):
        """Store a batch of scan results.

        Args:
            signals: list of dicts with keys:
                freq (Hz), peak (dBFS), avg (dBFS), std,
                classification, type, identification, distance
            device: device identifier string
        Returns:
            scan_id
        """
        now = time.time()
        sus_count = sum(1 for s in signals if s.get("classification") in ("sus", "danger"))
        cur = self.conn.execute(
            "INSERT INTO scans (timestamp, device, signal_count, sus_count) VALUES (?, ?, ?, ?)",
            (now, device, len(signals), sus_count),
        )
        scan_id = cur.lastrowid

        sig_rows = []
        trend_rows = []
        for s in signals:
            freq_mhz = s.get("freq", 0) / 1e6
            peak = s.get("peak", 0.0)
            sig_rows.append((
                scan_id, freq_mhz, peak, s.get("avg", 0.0), s.get("std", 0.0),
                s.get("classification", ""), s.get("type", ""),
                s.get("identification", ""), s.get("distance"), now, now,
            ))
            trend_rows.append((freq_mhz, peak, now))

        if sig_rows:
            self.conn.executemany(
                """INSERT INTO signals
                   (scan_id, freq_mhz, peak_dbfs, avg_dbfs, std, classification,
                    signal_type, identification, distance, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                sig_rows,
            )
        if trend_rows:
            self.conn.executemany(
                "INSERT INTO signal_trends (freq_mhz, peak_dbfs, timestamp) VALUES (?, ?, ?)",
                trend_rows,
            )
        self.conn.commit()
        return scan_id

    def get_history(self, freq_mhz, days=7):
        """Return signal history for a frequency over the given window.

        Args:
            freq_mhz: frequency in MHz
            days: lookback window
        Returns:
            list of dicts
        """
        cutoff = time.time() - days * 86400
        rows = self.conn.execute(
            """SELECT s.*, sc.timestamp as scan_time, sc.device
               FROM signals s JOIN scans sc ON s.scan_id = sc.id
               WHERE s.freq_mhz = ? AND sc.timestamp >= ?
               ORDER BY sc.timestamp DESC""",
            (freq_mhz, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_trend(self, freq_mhz, n=10):
        """Return last N peak values for trend arrows.

        Args:
            freq_mhz: frequency in MHz
            n: number of recent data points
        Returns:
            list of dicts with peak_dbfs and timestamp
        """
        rows = self.conn.execute(
            "SELECT peak_dbfs, timestamp FROM signal_trends WHERE freq_mhz = ? ORDER BY timestamp DESC LIMIT ?",
            (freq_mhz, n),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_band_activity(self, band, days=1):
        """How active is a frequency band.

        Args:
            band: band name key (e.g. 'fm_broadcast', 'wifi_24') or tuple (low_hz, high_hz)
            days: lookback window
        Returns:
            dict with scan_count, signal_count, sus_count, avg_peak
        """
        if isinstance(band, tuple):
            low, high = band
            low_mhz, high_mhz = low / 1e6, high / 1e6
        elif isinstance(band, str) and band in BANDS:
            low, high = BANDS[band]
            low_mhz, high_mhz = low / 1e6, high / 1e6
        else:
            return {"scan_count": 0, "signal_count": 0, "sus_count": 0, "avg_peak": None}

        cutoff = time.time() - days * 86400
        row = self.conn.execute(
            """SELECT
                COUNT(DISTINCT s.scan_id) as scan_count,
                COUNT(*) as signal_count,
                SUM(CASE WHEN s.classification IN ('sus','danger') THEN 1 ELSE 0 END) as sus_count,
                AVG(s.peak_dbfs) as avg_peak
               FROM signals s JOIN scans sc ON s.scan_id = sc.id
               WHERE s.freq_mhz BETWEEN ? AND ? AND sc.timestamp >= ?""",
            (low_mhz, high_mhz, cutoff),
        ).fetchone()
        return dict(row) if row else {"scan_count": 0, "signal_count": 0, "sus_count": 0, "avg_peak": None}

    def cleanup(self, max_days=30):
        """Delete records older than max_days."""
        cutoff = time.time() - max_days * 86400
        self.conn.execute("DELETE FROM scans WHERE timestamp < ?", (cutoff,))
        self.conn.execute("DELETE FROM signal_trends WHERE timestamp < ?", (cutoff,))
        # orphan signals cleaned by cascade, but belt-and-suspenders:
        self.conn.execute(
            "DELETE FROM signals WHERE scan_id NOT IN (SELECT id FROM scans)"
        )
        self.conn.commit()

    def export_csv(self, path, signals):
        """Export current scan signals to CSV."""
        headers = ["freq", "peak", "avg", "std", "classification", "type", "identification", "distance"]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            for s in signals:
                w.writerow(s)

    def export_json(self, path, signals):
        """Export current scan signals to JSON."""
        with open(path, "w") as f:
            json.dump(signals, f, indent=2)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
