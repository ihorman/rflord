"""Perimeter Secured mode — auto-jam unknown signals.

When enabled, RFLord automatically jams any new suspicious signal detected
during scanning. Uses HackRF TX to transmit noise on the detected frequency.

Usage:
    from perimeter import PerimeterMode
    pm = PerimeterMode()
    pm.process_scan(signals, known_freqs)
"""
import logging
import os
import subprocess
import time

log = logging.getLogger("rflord")

# Bands to ignore (legitimate services)
IGNORE_BANDS = [
    (88, 108),    # FM broadcast
    (174, 230),   # VHF TV/ham
    (470, 790),   # DVB-T
    (800, 960),   # GSM base stations
    (1805, 1880), # GSM 1800
    (2110, 2170), # 3G
    (2400, 2500), # WiFi 2.4GHz
    (5150, 5900), # WiFi 5GHz
]


class PerimeterMode:
    """Auto-jam unknown signals in perimeter defense mode."""
    
    def __init__(self):
        self.active = False
        self.jamming = {}  # freq_mhz (rounded) -> {'proc', 'started', 'last_seen', 'signal', 'freq_mhz'}
        self.noise_file = '/tmp/rflord_noise.bin'
    
    def _should_jam(self, freq_mhz):
        """Check if frequency should be jammed (skip legitimate bands)."""
        for lo, hi in IGNORE_BANDS:
            if lo <= freq_mhz <= hi:
                return False
        return True
    
    def _ensure_noise(self):
        """Generate noise file if not exists."""
        if not os.path.exists(self.noise_file):
            import numpy as np
            n = int(2000000 * 60)  # 60 seconds at 2 MHz
            samples = np.random.randint(-127, 128, n, dtype=np.int8)
            samples.tofile(self.noise_file)
    
    def jam(self, freq_mhz, signal_info=None):
        """Start jamming a specific frequency. Returns True if started."""
        freq_key = round(freq_mhz)
        
        if freq_key in self.jamming:
            return False
        
        if not self._should_jam(freq_mhz):
            return False
        
        self._ensure_noise()
        
        freq_hz = int(freq_mhz * 1e6)
        cmd = ["hackrf_transfer", "-t", self.noise_file, "-f", str(freq_hz),
               "-s", "2000000", "-a", "1", "-x", "40", "-n", "120000000"]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.jamming[freq_key] = {
                'proc': p,
                'started': time.time(),
                'last_seen': time.time(),
                'signal': signal_info or {},
                'freq_mhz': freq_mhz,
            }
            log.warning(f"PERIMETER: jamming {freq_mhz:.1f} MHz")
            return True
        except Exception as e:
            log.warning(f"PERIMETER: failed to jam {freq_mhz:.1f} MHz: {e}")
            return False
    
    def stop_freq(self, freq_mhz):
        """Stop jamming a specific frequency."""
        freq_key = round(freq_mhz)
        if freq_key in self.jamming:
            info = self.jamming[freq_key]
            try:
                info['proc'].terminate()
                info['proc'].wait(timeout=2)
            except:
                try: info['proc'].kill()
                except: pass
            del self.jamming[freq_key]
            log.info(f"PERIMETER: stopped jamming {freq_mhz:.1f} MHz")
    
    def stop_all(self):
        """Stop all jamming."""
        for freq_key, info in list(self.jamming.items()):
            try:
                info['proc'].terminate()
                info['proc'].wait(timeout=2)
            except:
                try: info['proc'].kill()
                except: pass
        self.jamming.clear()
        log.info("PERIMETER: all jamming stopped")
    
    def process_scan(self, unique_signals, known_freqs, classify_fn):
        """Process scan results. Jam new suspicious signals.
        
        Args:
            unique_signals: list of signal dicts {'freq': Hz, 'peak': dB, 'std': float}
            known_freqs: dict of freq_mhz (rounded) -> first_seen_time
            classify_fn: function(freq_mhz, power, std) -> 'ok'/'sus'/'danger'
        
        Returns:
            list of newly jammed freq_mhz values
        """
        newly_jammed = []
        
        for s in unique_signals:
            f = s['freq'] / 1e6
            freq_key = round(f)
            cls = classify_fn(f, s['peak'], s['std'])
            
            # Only jam suspicious/danger signals
            if cls not in ('sus', 'danger'):
                continue
            
            # Skip if already known (seen before this session)
            if freq_key in known_freqs:
                continue
            
            # Skip if already jamming
            if freq_key in self.jamming:
                # Update last_seen
                self.jamming[freq_key]['last_seen'] = time.time()
                continue
            
            # Try to jam
            if self.jam(f, s):
                newly_jammed.append(f)
        
        # Stop jamming signals that disappeared for >30 seconds
        current_freq_keys = set(round(s['freq'] / 1e6) for s in unique_signals)
        for freq_key in list(self.jamming.keys()):
            if freq_key in current_freq_keys:
                self.jamming[freq_key]['last_seen'] = time.time()
            else:
                info = self.jamming[freq_key]
                if time.time() - info['last_seen'] > 30:
                    self.stop_freq(info['freq_mhz'])
        
        return newly_jammed
    
    @property
    def jam_count(self):
        return len(self.jamming)
    
    @property
    def jammed_freqs(self):
        return sorted(self.jamming.keys())
