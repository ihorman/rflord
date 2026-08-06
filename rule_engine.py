"""Rule engine for unified RF + WiFi + BLE threat assessment.

Collects signal events from multiple sources (HackRF/RTL-SDR, WiFi, BLE),
matches against the unified signature database, and evaluates boolean rules
to produce named threat assessments.

Architecture (modeled after AirHound):
  SignalEvent → SignatureMatch → RuleEvaluation → ThreatAssessment

Usage:
    engine = RuleEngine()
    engine.add_rf_signal(freq_mhz=433.92, power_dbfs=-30, std=2.5)
    engine.add_wifi_device(mac="B4:1E:52:01:02:03", ssid="Flock-A1B2C3")
    engine.add_ble_device(uuid="3100", name="Flock BLE", mfr_id=2504)
    assessments = engine.evaluate()
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("rflord")

# --- Data classes ---

@dataclass
class SignalEvent:
    """A detected signal from any source."""
    source: str  # 'rf', 'wifi', 'ble'
    timestamp: float = field(default_factory=time.time)

    # RF-specific
    freq_mhz: Optional[float] = None
    power_dbfs: Optional[float] = None
    std: Optional[float] = None

    # WiFi-specific
    mac: Optional[str] = None
    ssid: Optional[str] = None
    rssi: Optional[int] = None

    # BLE-specific
    ble_name: Optional[str] = None
    ble_uuid: Optional[str] = None
    ble_mfr_id: Optional[int] = None
    ble_ad_bytes: Optional[bytes] = None

    # Common
    signal_type: Optional[str] = None  # From classification

@dataclass
class SignatureMatch:
    """A signature that matched a signal event."""
    signature_type: str  # 'rf_freq', 'mac_oui', 'ssid_pattern', 'ble_uuid', 'ble_name', 'ble_mfr'
    signature_id: str  # e.g., 'mac_oui:B4:1E:52', 'freq:433.92'
    name: str
    category: str
    threat_level: int  # 0=critical, 1=high, 2=medium, 3=low
    source: str  # 'spy_db', 'artemis', 'drone_rf', 'rf_protocols', 'airhound'

@dataclass
class RuleMatch:
    """A named rule that matched."""
    rule_name: str
    description: str
    category: str
    matched_signatures: list
    confidence: float  # 0.0-1.0
    threat_level: int  # 0=critical, 1=high, 2=medium, 3=low
    source_types: set  # {'rf'}, {'wifi', 'ble'}, etc.
    events: list  # The SignalEvents that triggered this

@dataclass
class ThreatAssessment:
    """Final threat assessment combining all matched rules."""
    timestamp: float = field(default_factory=time.time)
    rules_matched: list = field(default_factory=list)  # list of RuleMatch
    unmatched_suspicious: list = field(default_factory=list)  # SignalEvents that didn't match rules
    total_signals: int = 0
    total_suspicious: int = 0
    max_threat_level: int = 3  # 0=critical, 3=low
    summary: str = ""

    @property
    def has_threats(self):
        return len(self.rules_matched) > 0

    @property
    def threat_level_str(self):
        levels = {0: "CRITICAL", 1: "HIGH", 2: "MEDIUM", 3: "LOW"}
        return levels.get(self.max_threat_level, "UNKNOWN")


# --- Rule Engine ---

class RuleEngine:
    """Unified threat assessment engine combining RF, WiFi, and BLE signals."""

    def __init__(self, signatures_db=None):
        """
        Args:
            signatures_db: SignaturesDB instance. If None, creates a new one.
        """
        if signatures_db is None:
            from signatures_db import SignaturesDB
            self.db = SignaturesDB()
        else:
            self.db = signatures_db

        self._events = []  # Current scan's SignalEvents
        self._matches = []  # Current scan's SignatureMatches
        self._history = {}  # Freq/MAC -> last seen time
        self._alert_counts = {}  # Rule name -> count

    def clear(self):
        """Clear events for new scan cycle."""
        self._events = []
        self._matches = []

    # --- Signal collection ---

    def add_rf_signal(self, freq_mhz, power_dbfs=None, std=None, signal_type=None):
        """Add an RF signal from HackRF/RTL-SDR scan."""
        event = SignalEvent(
            source='rf',
            freq_mhz=freq_mhz,
            power_dbfs=power_dbfs,
            std=std,
            signal_type=signal_type,
        )
        self._events.append(event)

    def add_rf_signals(self, signals):
        """Add multiple RF signals from scan results.

        Args:
            signals: list of dicts with 'freq' (Hz), 'peak', 'std' keys
        """
        for s in signals:
            self.add_rf_signal(
                freq_mhz=s['freq'] / 1e6,
                power_dbfs=s.get('peak'),
                std=s.get('std'),
                signal_type=s.get('type'),
            )

    def add_wifi_device(self, mac=None, ssid=None, rssi=None):
        """Add a WiFi device from scan."""
        event = SignalEvent(
            source='wifi',
            mac=mac,
            ssid=ssid,
            rssi=rssi,
        )
        self._events.append(event)

    def add_ble_device(self, name=None, uuid=None, mfr_id=None, ad_bytes=None, rssi=None):
        """Add a BLE device from scan."""
        event = SignalEvent(
            source='ble',
            ble_name=name,
            ble_uuid=uuid,
            ble_mfr_id=mfr_id,
            ble_ad_bytes=ad_bytes,
            rssi=rssi,
        )
        self._events.append(event)

    # --- Signature matching ---

    def _match_rf(self, event):
        """Match RF signal against frequency signatures."""
        matches = []
        if event.freq_mhz is None:
            return matches

        # Spy_db, drone_rf, artemis, rf_protocols
        sigs = self.db.identify_freq(event.freq_mhz, tolerance_mhz=0.5)
        for sig in sigs:
            threat = int(sig.get('threat_level', 3) or 3)
            if threat is None:
                threat = 3
            matches.append(SignatureMatch(
                signature_type='rf_freq',
                signature_id=f"freq:{event.freq_mhz:.1f}",
                name=sig['name'],
                category=sig.get('category', 'unknown'),
                threat_level=threat,
                source=sig.get('source', 'unknown'),
            ))
        return matches

    def _match_wifi(self, event):
        """Match WiFi device against MAC OUI and SSID patterns."""
        matches = []

        # MAC OUI lookup
        if event.mac:
            ouis = self.db.identify_mac(event.mac)
            for oui in ouis:
                matches.append(SignatureMatch(
                    signature_type='mac_oui',
                    signature_id=f"mac_oui:{event.mac[:8].upper()}",
                    name=oui['vendor_name'],
                    category=oui.get('category', 'unknown'),
                    threat_level=1,  # Known surveillance camera OUIs are high threat
                    source='airhound',
                ))

        # SSID pattern matching
        if event.ssid:
            ssids = self.db.identify_ssid(event.ssid)
            for ssid in ssids:
                matches.append(SignatureMatch(
                    signature_type='ssid_pattern',
                    signature_id=f"ssid:{event.ssid}",
                    name=ssid.get('description', event.ssid),
                    category=ssid.get('category', 'unknown'),
                    threat_level=1,
                    source='airhound',
                ))

        return matches

    def _match_ble(self, event):
        """Match BLE device against UUIDs, names, and manufacturer IDs."""
        matches = []

        # BLE service UUID
        if event.ble_uuid:
            uuids = self.db.identify_ble_uuid(event.ble_uuid)
            for u in uuids:
                matches.append(SignatureMatch(
                    signature_type='ble_uuid',
                    signature_id=f"ble_uuid:{event.ble_uuid}",
                    name=u.get('description', f'UUID {event.ble_uuid}'),
                    category=u.get('category', 'unknown'),
                    threat_level=1,
                    source='airhound',
                ))

        # BLE name pattern
        if event.ble_name:
            names = self.db.identify_ble_name(event.ble_name)
            for n in names:
                matches.append(SignatureMatch(
                    signature_type='ble_name',
                    signature_id=f"ble_name:{event.ble_name.lower()}",
                    name=n.get('description', event.ble_name),
                    category=n.get('category', 'unknown'),
                    threat_level=1,
                    source='airhound',
                ))

        # BLE manufacturer ID
        if event.ble_mfr_id:
            mfrs = self.db.identify_ble_mfr(event.ble_mfr_id)
            for m in mfrs:
                matches.append(SignatureMatch(
                    signature_type='ble_mfr',
                    signature_id=f"ble_mfr:{event.ble_mfr_id}",
                    name=m.get('description', f'MFR {event.ble_mfr_id}'),
                    category=m.get('category', 'unknown'),
                    threat_level=1,
                    source='airhound',
                ))

        # BLE advertisement bytes
        if event.ble_ad_bytes:
            ad_hex = event.ble_ad_bytes.hex().upper() if isinstance(event.ble_ad_bytes, bytes) else str(event.ble_ad_bytes)
            for length in [4, 2]:
                prefix = ad_hex[:length]
                rows = self.db.conn.execute(
                    "SELECT * FROM ble_signatures WHERE sig_type = 'ad_bytes' AND value = ?",
                    (prefix,)
                ).fetchall()
                for r in rows:
                    r = dict(r)
                    matches.append(SignatureMatch(
                        signature_type='ble_ad',
                        signature_id=f"ble_ad:{prefix}",
                        name=r.get('description', f'AD {prefix}'),
                        category=r.get('category', 'unknown'),
                        threat_level=1,
                        source='airhound',
                    ))

        return matches

    def match_all(self):
        """Match all collected events against signature database."""
        self._matches = []
        for event in self._events:
            if event.source == 'rf':
                self._matches.extend(self._match_rf(event))
            elif event.source == 'wifi':
                self._matches.extend(self._match_wifi(event))
            elif event.source == 'ble':
                self._matches.extend(self._match_ble(event))

        # Update history
        now = time.time()
        for event in self._events:
            if event.freq_mhz:
                self._history[f"rf:{event.freq_mhz:.1f}"] = now
            if event.mac:
                self._history[f"wifi:{event.mac}"] = now
            if event.ble_uuid:
                self._history[f"ble:{event.ble_uuid}"] = now

        return self._matches

    # --- Rule evaluation ---

    def evaluate(self):
        """Evaluate rules against matched signatures and produce threat assessment.

        Returns:
            ThreatAssessment with all matched rules and summary.
        """
        # First, match all events
        if not self._matches:
            self.match_all()

        # Build set of matched signature identifiers for rule evaluation
        sig_ids = set()
        for m in self._matches:
            sig_ids.add(m.signature_id)

        # Also build keyword sets for rule matching
        matched_categories = set()
        matched_names = set()
        for m in self._matches:
            matched_categories.add(m.category)
            matched_names.add(m.name.lower())

        # Evaluate rules from database
        rules = self.db.conn.execute("SELECT * FROM rules").fetchall()
        rule_matches = []

        for rule in rules:
            rule = dict(rule)
            expr = json.loads(rule["expression"]) if rule["expression"] else {}

            # Evaluate the boolean expression
            if self._eval_expr(expr, sig_ids, matched_categories, matched_names):
                # Find which events triggered this rule
                triggering_events = self._find_triggering_events(rule, sig_ids)

                # Determine source types
                source_types = set()
                for e in triggering_events:
                    source_types.add(e.source)

                # Calculate confidence (more sources = higher confidence)
                confidence = min(1.0, len(source_types) * 0.4 + 0.2)

                # Determine threat level from matched signatures
                threat_levels = [int(m.threat_level) for m in self._matches if any(
                    self._sig_matches_rule(m, sig_id) for sig_id in sig_ids
                )]
                min_threat = min(threat_levels) if threat_levels else 3

                rm = RuleMatch(
                    rule_name=rule["name"],
                    description=rule.get("description", ""),
                    category=rule.get("category", "unknown"),
                    matched_signatures=list(sig_ids),
                    confidence=confidence,
                    threat_level=min_threat,
                    source_types=source_types,
                    events=triggering_events,
                )
                rule_matches.append(rm)
                self._alert_counts[rule["name"]] = self._alert_counts.get(rule["name"], 0) + 1

        # Find suspicious signals that didn't match any rule
        rule_sig_ids = set()
        for rm in rule_matches:
            rule_sig_ids.update(rm.matched_signatures)
        unmatched = [m for m in self._matches if m.signature_id not in rule_sig_ids and m.threat_level <= 2]

        # Build assessment
        assessment = ThreatAssessment(
            rules_matched=rule_matches,
            unmatched_suspicious=unmatched,
            total_signals=len(self._events),
            total_suspicious=len(self._matches),
            max_threat_level=min([int(rm.threat_level) for rm in rule_matches], default=3),
        )

        # Build summary
        if rule_matches:
            names = [rm.rule_name for rm in rule_matches]
            assessment.summary = f"Threats detected: {', '.join(names)}"
        elif unmatched:
            assessment.summary = f"{len(unmatched)} suspicious signals (no rule match)"
        else:
            assessment.summary = "No threats detected"

        return assessment

    def _eval_expr(self, expr, sig_ids, categories, names):
        """Evaluate a boolean expression against matched signatures."""
        if isinstance(expr, str):
            # Check if it's a signature ID, category, or name
            return (expr in sig_ids or
                    expr in categories or
                    expr.lower() in names)
        if isinstance(expr, dict):
            if "anyOf" in expr:
                return any(self._eval_expr(item, sig_ids, categories, names) for item in expr["anyOf"])
            if "allOf" in expr:
                return all(self._eval_expr(item, sig_ids, categories, names) for item in expr["allOf"])
            if "not" in expr:
                return not self._eval_expr(expr["not"], sig_ids, categories, names)
        return False

    def _sig_matches_rule(self, match, sig_id):
        """Check if a SignatureMatch is related to a signature ID."""
        return match.signature_id == sig_id

    def _find_triggering_events(self, rule, sig_ids):
        """Find which SignalEvents triggered a rule match."""
        triggering = []
        for event in self._events:
            # Check if this event contributed to any matched signature
            event_sigs = []
            if event.source == 'rf':
                event_sigs = self._match_rf(event)
            elif event.source == 'wifi':
                event_sigs = self._match_wifi(event)
            elif event.source == 'ble':
                event_sigs = self._match_ble(event)

            for sig in event_sigs:
                if sig.signature_id in sig_ids:
                    triggering.append(event)
                    break

        return triggering

    # --- WiFi scanning ---

    @staticmethod
    def scan_wifi(interface="wlan0", timeout=5):
        """Scan WiFi networks and return list of SignalEvent.

        Uses 'iw dev <iface> scan' to discover WiFi devices.
        """
        import subprocess
        events = []
        try:
            r = subprocess.run(
                ["sudo", "iw", "dev", interface, "scan"],
                capture_output=True, text=True, timeout=timeout
            )
            if r.returncode != 0:
                log.warning(f"WiFi scan failed: {r.stderr[:200]}")
                return events

            current_bss = None
            current_ssid = None
            current_rssi = None

            for line in r.stdout.split('\n'):
                line = line.strip()
                if line.startswith('BSS '):
                    # Save previous
                    if current_bss:
                        events.append(SignalEvent(
                            source='wifi',
                            mac=current_bss,
                            ssid=current_ssid,
                            rssi=current_rssi,
                        ))
                    # Parse MAC from "BSS aa:bb:cc:dd:ee:ff(on wlan0)"
                    parts = line.split()
                    current_bss = parts[1].split('(')[0] if len(parts) > 1 else None
                    current_ssid = None
                    current_rssi = None
                elif line.startswith('SSID:'):
                    current_ssid = line[5:].strip()
                elif line.startswith('signal:'):
                    try:
                        current_rssi = int(float(line[7:].strip().replace(' dBm', '')))
                    except:
                        pass

            # Save last
            if current_bss:
                events.append(SignalEvent(
                    source='wifi',
                    mac=current_bss,
                    ssid=current_ssid,
                    rssi=current_rssi,
                ))

        except subprocess.TimeoutExpired:
            log.warning(f"WiFi scan timeout after {timeout}s")
        except Exception as e:
            log.warning(f"WiFi scan error: {e}")

        return events

    # --- BLE scanning ---

    @staticmethod
    def scan_ble(timeout=5):
        """Scan BLE devices and return list of SignalEvent.

        Uses 'hcitool lescan' to discover BLE devices.
        """
        import subprocess
        events = []
        try:
            # Start LE scan
            proc = subprocess.Popen(
                ["hcitool", "lescan"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            time.sleep(timeout)
            proc.terminate()
            stdout, _ = proc.communicate(timeout=2)

            for line in stdout.decode('utf-8', errors='replace').split('\n'):
                line = line.strip()
                if not line or line.startswith('LE Scan'):
                    continue
                # Format: "AA:BB:CC:DD:EE:FF Device Name"
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    mac = parts[0]
                    name = parts[1]
                    events.append(SignalEvent(
                        source='ble',
                        mac=mac,
                        ble_name=name,
                    ))

        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            log.warning(f"BLE scan error: {e}")

        return events

    # --- Integration with rflord main loop ---

    def process_rf_scan(self, signals, artemis_db=None):
        """Process RF scan results and return threat assessment.

        Args:
            signals: list of signal dicts from rflord scan
            artemis_db: optional Artemis database for additional identification
        Returns:
            ThreatAssessment
        """
        self.clear()

        # Add all RF signals
        self.add_rf_signals(signals)

        # Evaluate
        return self.evaluate()

    def full_scan(self, wifi_interface="wlan0"):
        """Run full scan: RF + WiFi + BLE and return combined assessment.

        This is meant to be called from a background thread.
        """
        self.clear()

        # WiFi scan
        wifi_events = self.scan_wifi(wifi_interface)
        self._events.extend(wifi_events)
        log.info(f"WiFi scan: {len(wifi_events)} devices")

        # BLE scan
        ble_events = self.scan_ble(timeout=3)
        self._events.extend(ble_events)
        log.info(f"BLE scan: {len(ble_events)} devices")

        # RF events are added separately by the main scanner

        return self.evaluate()

    # --- Alert formatting ---

    @staticmethod
    def format_assessment(assessment):
        """Format threat assessment for display."""
        lines = []
        if assessment.rules_matched:
            for rm in assessment.rules_matched:
                icon = "🔴" if rm.threat_level == 0 else "🟠" if rm.threat_level == 1 else "🟡"
                sources = "+".join(sorted(rm.source_types))
                conf = f"{rm.confidence:.0%}"
                lines.append(f"{icon} {rm.rule_name} [{sources}] ({conf}) — {rm.description}")
        if assessment.unmatched_suspicious:
            lines.append(f"⚠ {len(assessment.unmatched_suspicious)} suspicious signals (no rule match)")
        if not lines:
            lines.append("✓ No threats detected")
        return "\n".join(lines)

    def format_voice_alert(self, assessment):
        """Format threat assessment as voice alert text."""
        if not assessment.rules_matched:
            return None

        parts = []
        for rm in assessment.rules_matched:
            sources = " and ".join(sorted(rm.source_types))
            parts.append(f"{rm.rule_name} detected via {sources}")

        if len(parts) == 1:
            return f"Alert. {parts[0]}."
        else:
            return f"Alert. {len(parts)} threats: {', '.join(parts)}."

    def get_alert_history(self):
        """Return alert counts per rule."""
        return dict(self._alert_counts)
