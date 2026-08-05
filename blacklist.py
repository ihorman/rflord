"""Frequency blacklist/whitelist for rflord."""
import os


def load_blacklist(path=None):
    """Load frequency blacklist from config file.
    
    Returns list of (freq_low_mhz, freq_high_mhz) tuples.
    Single frequencies become (freq, freq) tuples.
    """
    if path is None:
        path = os.path.expanduser("~/.config/rflord/ignore.conf")
    
    ranges = []
    if not os.path.exists(path):
        return ranges
    
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                parts = line.split(':')
                try:
                    lo = float(parts[0])
                    hi = float(parts[1])
                    ranges.append((lo, hi))
                except ValueError:
                    continue
            else:
                try:
                    freq = float(line)
                    ranges.append((freq, freq))
                except ValueError:
                    continue
    
    return ranges


def is_blacklisted(freq_mhz, blacklist):
    """Check if a frequency is in the blacklist."""
    for lo, hi in blacklist:
        if lo <= freq_mhz <= hi:
            return True
    return False


def filter_blacklisted(signals, blacklist):
    """Remove blacklisted signals from list."""
    if not blacklist:
        return signals
    return [s for s in signals if not is_blacklisted(s['freq'] / 1e6, blacklist)]
