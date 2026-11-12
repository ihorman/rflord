"""Scan acceleration: skip empty bands, but NEVER lose intermittent signals.

Rules learned the hard way:
- Voice bands must never be skipped (transmissions are intermittent).
- 'Contained in' checks fail for real configured bands (e.g. 430-470) —
  use OVERLAP tests instead.
- A skipped band must come back eventually (TTL revival), or one quiet
  stretch permanently blinds the scanner.
- A FAILED sweep (broken tool / timeout) must NOT count as an empty band —
  that retires bands on USB glitches.
"""
import time

# Voice radio bands — NEVER skip these (transmissions are intermittent by nature)
VOICE_BANDS = [
    (108, 137), (144, 148), (150, 174),   # air/ham/VHF
    (406, 410), (430, 470), (446, 447), (462, 468),  # beacons/UHF/PMR/FRS
]

# Bands with intermittent beacons — also protected from permanent skip
INTERMITTENT_BANDS = [
    (860, 960),    # GSM-R / ISM 868 LoRa beacons
    (900, 930),    # 915 MHz ISM
    (1574, 1576),  # Iridium bursts
]


def _overlaps(f_lo, f_hi, ranges):
    """True if the scanned band OVERLAPS any protected range."""
    for lo, hi in ranges:
        if f_lo <= hi and f_hi >= lo:
            return True
    return False


class ScanAccelerator:
    """Skip quiet bands to speed up scans — with TTL revival so a band can
    never go permanently dark."""

    def __init__(self, skip_after_empty=3, revival_s=600):
        self.skip_after_empty = skip_after_empty
        self.revival_s = revival_s
        self.band_empty_count = {}  # (f_lo, f_hi) -> consecutive empty count
        self.band_last_signal = {}  # (f_lo, f_hi) -> timestamp of last signal
        self.band_skip_time = {}    # (f_lo, f_hi) -> when skipping started

    def _protected(self, f_lo, f_hi):
        return _overlaps(f_lo, f_hi, VOICE_BANDS) or _overlaps(f_lo, f_hi, INTERMITTENT_BANDS)

    def record_band_result(self, f_lo, f_hi, signal_count, sweep_ok=True):
        """Update tracking for a band. sweep_ok=False marks a FAILED sweep —
        which must not count toward retirement."""
        key = (f_lo, f_hi)
        if signal_count > 0:
            self.band_empty_count[key] = 0
            self.band_last_signal[key] = time.time()
            self.band_skip_time.pop(key, None)
        elif sweep_ok:
            self.band_empty_count[key] = self.band_empty_count.get(key, 0) + 1
        # failed sweep: no state change

    def should_skip(self, f_lo, f_hi):
        """Should we skip this band this scan? Voice/intermittent bands: NEVER.
        Skipped bands revive after revival_s so they can never go dark forever."""
        if self._protected(f_lo, f_hi):
            return False
        key = (f_lo, f_hi)
        if self.band_empty_count.get(key, 0) < self.skip_after_empty:
            return False
        # TTL revival: re-probe a skipped band every revival_s seconds
        skip_start = self.band_skip_time.get(key)
        now = time.time()
        if skip_start is None:
            self.band_skip_time[key] = now
            return False  # first time reaching the threshold: scan it once more
        if now - skip_start >= self.revival_s:
            self.band_skip_time[key] = now
            return False  # revival probe
        return True

    def get_active_bands(self, bands):
        """Return filtered band list, skipping inactive ones."""
        active = []
        for band in bands:
            f_lo, f_hi = band[0], band[1]
            if not self.should_skip(f_lo, f_hi):
                active.append(band)
        # Always scan at least 2 bands to avoid skipping everything
        if len(active) < 2:
            return bands
        return active

    def reset(self):
        """Reset all counters (e.g., on rescan hotkey)."""
        self.band_empty_count.clear()
        self.band_last_signal.clear()
        self.band_skip_time.clear()

    def get_skipped_count(self, bands):
        """How many bands would be skipped."""
        return sum(1 for b in bands if self.should_skip(b[0], b[1]))
