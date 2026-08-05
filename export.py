"""Simple CSV/JSON export for rflord signal data."""

import csv
import json


def export_csv(filepath, signals):
    """Write signals list to CSV.

    Args:
        filepath: output file path
        signals: list of dicts with keys:
            freq, peak, avg, std, classification, type, identification, distance
    """
    headers = ["freq", "peak", "avg", "std", "classification", "type", "identification", "distance"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for s in signals:
            writer.writerow(s)


def export_json(filepath, signals):
    """Write signals list to JSON.

    Args:
        filepath: output file path
        signals: list of dicts with keys:
            freq, peak, avg, std, classification, type, identification, distance
    """
    with open(filepath, "w") as f:
        json.dump(signals, f, indent=2)
