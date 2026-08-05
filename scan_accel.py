"""Scan acceleration — skip bands with no recent signals."""
import time
from collections import defaultdict


class ScanAccelerator:
    """Track band activity and skip empty bands after N consecutive empty scans."""
    
    def __init__(self, skip_after_empty=3):
        self.skip_after_empty = skip_after_empty
        self.band_empty_count = {}  # (f_lo, f_hi) -> consecutive empty count
        self.band_last_signal = {}  # (f_lo, f_hi) -> timestamp of last signal
    
    def record_band_result(self, f_lo, f_hi, signal_count):
        """Record whether a band had signals."""
        key = (f_lo, f_hi)
        if signal_count > 0:
            self.band_empty_count[key] = 0
            self.band_last_signal[key] = time.time()
        else:
            self.band_empty_count[key] = self.band_empty_count.get(key, 0) + 1
    
    def should_skip(self, f_lo, f_hi):
        """Should we skip this band this scan?"""
        key = (f_lo, f_hi)
        return self.band_empty_count.get(key, 0) >= self.skip_after_empty
    
    def get_active_bands(self, bands):
        """Return filtered band list, skipping inactive ones."""
        active = []
        skipped = 0
        for band in bands:
            f_lo, f_hi = band[0], band[1]
            if self.should_skip(f_lo, f_hi):
                skipped += 1
            else:
                active.append(band)
        # Always scan at least 2 bands to avoid skipping everything
        if len(active) < 2:
            return bands
        return active
    
    def reset(self):
        """Reset all counters (e.g., on rescan hotkey)."""
        self.band_empty_count.clear()
        self.band_last_signal.clear()
    
    def get_skipped_count(self, bands):
        """How many bands would be skipped."""
        return sum(1 for b in bands if self.should_skip(b[0], b[1]))
