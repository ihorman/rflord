#!/usr/bin/env python3
"""
drone_dsp.py — Drone signal DSP analysis using IQ captures.
Based on algorithms from arall/sigint (github.com/arall/sigint).

Detects drone video links (DJI O4/OcuSync) and control links (ELRS/Crossfire)
by analyzing IQ samples for:
- Spectral flatness (OFDM flat-top signature)
- Duty cycle (continuous video vs bursty WiFi)
- FHSS burst timing (control link hop rates)
- Bandwidth vs WiFi channel mismatch
"""

import numpy as np

# Standard WiFi channel frequencies
WIFI_CHANNELS_24 = [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472]
WIFI_CHANNELS_5 = [5180, 5200, 5220, 5240, 5260, 5280, 5300, 5320,
                   5500, 5520, 5540, 5560, 5580, 5600, 5620, 5640, 5660, 5680, 5700, 5720,
                   5745, 5765, 5785, 5805, 5825, 5845]

# Known drone control hop rates (Hz)
ELRS_RATES = [25, 50, 150, 250, 500]
CROSSFIRE_RATE = 150


def compute_spectrogram(iq_samples, sample_rate, fft_size=1024, overlap=0.5):
    """Compute spectrogram from IQ samples. Returns (times, freqs, power_db)."""
    hop = int(fft_size * (1 - overlap))
    n_frames = max(1, (len(iq_samples) - fft_size) // hop)
    window = np.hanning(fft_size)
    power = np.empty((n_frames, fft_size), dtype=np.float32)
    for i in range(n_frames):
        seg = iq_samples[i * hop: i * hop + fft_size]
        spec = np.fft.fftshift(np.fft.fft(seg * window))
        power[i] = 10 * np.log10(np.abs(spec) ** 2 + 1e-20)
    freqs = np.fft.fftshift(np.fft.fftfreq(fft_size, 1 / sample_rate))
    times = np.arange(n_frames) * hop / sample_rate
    return times, freqs, power


def spectral_flatness(power_db):
    """Compute spectral flatness (0-1). OFDM signals score 0.7-1.0."""
    linear = 10 ** (power_db / 10)
    geo_mean = np.exp(np.mean(np.log(linear + 1e-20)))
    arith_mean = np.mean(linear)
    if arith_mean < 1e-20:
        return 0.0
    return float(geo_mean / arith_mean)


def is_wifi_channel(freq_mhz, tolerance_mhz=3):
    """Check if frequency matches a standard WiFi channel."""
    all_channels = WIFI_CHANNELS_24 + WIFI_CHANNELS_5
    for ch in all_channels:
        if abs(freq_mhz - ch) < tolerance_mhz:
            return True
    return False


def detect_ofdm_bursts(iq_samples, sample_rate, fft_size=1024,
                       min_bw_hz=5e6, min_snr_db=8.0):
    """Detect wideband OFDM-like energy bursts. Returns (bursts, noise_db)."""
    times, freqs, power_db = compute_spectrogram(iq_samples, sample_rate, fft_size)
    if len(times) == 0:
        return [], -100.0

    freq_res = freqs[1] - freqs[0]
    min_bins = max(1, int(min_bw_hz / freq_res))

    # Noise floor from quietest 25% of frames
    frame_medians = np.median(power_db, axis=1)
    noise_db = float(np.percentile(frame_medians, 25))
    threshold = noise_db + min_snr_db

    bursts = []
    for i, t in enumerate(times):
        row = power_db[i]
        active = row > threshold
        regions = _contiguous_regions(active)
        for start_bin, end_bin in regions:
            width = end_bin - start_bin
            if width < min_bins:
                continue
            region_power = row[start_bin:end_bin]
            bw = width * freq_res
            center_offset = (freqs[start_bin] + freqs[end_bin - 1]) / 2
            flatness = spectral_flatness(region_power)
            bursts.append({
                "start_s": float(t),
                "duration_s": float(times[1] - times[0]) if len(times) > 1 else 0,
                "center_freq_offset_hz": float(center_offset),
                "bandwidth_hz": float(bw),
                "power_db": float(np.max(region_power)),
                "noise_db": noise_db,
                "flatness": flatness,
            })
    return bursts, noise_db


def _contiguous_regions(mask):
    """Find start/end indices of contiguous True regions."""
    regions = []
    in_region = False
    start = 0
    for i, v in enumerate(mask):
        if v and not in_region:
            start = i
            in_region = True
        elif not v and in_region:
            regions.append((start, i))
            in_region = False
    if in_region:
        regions.append((start, len(mask)))
    return regions


def measure_duty_cycle(bursts, total_duration_s):
    """Fraction of time signal is present. Drone video >0.5, WiFi <0.3."""
    if not bursts or total_duration_s <= 0:
        return 0.0
    active_time = sum(b["duration_s"] for b in bursts)
    return min(1.0, active_time / total_duration_s)


def classify_drone_video(bursts, center_freq_mhz, sample_rate, noise_db):
    """Classify detected OFDM bursts as drone video vs WiFi.

    Returns dict: detected, confidence, bandwidth_mhz, duty_cycle, flatness, is_wifi, protocol
    """
    if not bursts:
        return {"detected": False, "confidence": 0, "n_bursts": 0}

    bws = [b["bandwidth_hz"] for b in bursts]
    centers = [b["center_freq_offset_hz"] for b in bursts]
    flatnesses = [b["flatness"] for b in bursts]
    powers = [b["power_db"] for b in bursts]

    median_bw = float(np.median(bws))
    median_center = float(np.median(centers))
    mean_flatness = float(np.mean(flatnesses))
    peak_power = float(np.max(powers))
    snr = peak_power - noise_db

    abs_freq_mhz = center_freq_mhz + median_center / 1e6
    total_duration = bursts[-1]["start_s"] - bursts[0]["start_s"]
    if total_duration <= 0:
        total_duration = bursts[0]["duration_s"] or 0.01
    duty = measure_duty_cycle(bursts, total_duration)

    wifi_match = is_wifi_channel(abs_freq_mhz)

    # Confidence scoring (from arall/sigint)
    confidence = 0.0

    # Wideband OFDM (>5 MHz): strong indicator
    if median_bw > 5e6:
        confidence += 0.3
    if median_bw > 10e6:
        confidence += 0.1

    # High spectral flatness (OFDM-like)
    if mean_flatness > 0.5:
        confidence += 0.2
    if mean_flatness > 0.7:
        confidence += 0.1

    # High duty cycle (continuous video stream)
    if duty > 0.5:
        confidence += 0.2
    if duty > 0.8:
        confidence += 0.1

    # Not on standard WiFi channel
    if not wifi_match:
        confidence += 0.2

    # WiFi channel match reduces confidence
    if wifi_match:
        confidence -= 0.3

    # Low duty cycle reduces confidence
    if duty < 0.2:
        confidence -= 0.2

    confidence = max(0.0, min(1.0, confidence))
    detected = confidence >= 0.4 and snr > 6 and len(bursts) >= 3

    # Identify likely protocol
    protocol = "Unknown"
    if detected:
        if median_bw > 10e6 and mean_flatness > 0.6:
            if 5725 <= abs_freq_mhz <= 5875:
                protocol = "DJI O4/O3 or Walksnail"
            elif 2400 <= abs_freq_mhz <= 2483:
                protocol = "DJI OcuSync/O3 (2.4G)"
            elif 5150 <= abs_freq_mhz <= 5350:
                protocol = "DJI O4 (5G low)"
            else:
                protocol = "Drone video (OFDM)"
        elif median_bw > 5e6:
            protocol = "Possible drone video"
        elif duty > 0.5:
            protocol = "Continuous carrier (possible drone)"

    return {
        "detected": detected,
        "confidence": round(confidence, 2),
        "bandwidth_mhz": round(median_bw / 1e6, 1),
        "duty_cycle": round(duty, 2),
        "flatness": round(mean_flatness, 2),
        "snr_db": round(snr, 1),
        "center_freq_mhz": round(abs_freq_mhz, 1),
        "is_wifi": wifi_match,
        "n_bursts": len(bursts),
        "protocol": protocol,
        "power_db": round(peak_power, 1),
        "noise_db": round(noise_db, 1),
    }


def analyze_drone_signal(iq_samples, sample_rate, center_freq_mhz):
    """Full drone signal analysis pipeline on IQ samples.

    Returns dict with detection results.
    """
    # Detect OFDM bursts
    bursts, noise_db = detect_ofdm_bursts(
        iq_samples, sample_rate,
        min_bw_hz=2e6,  # Lower threshold to catch more signals
        min_snr_db=6.0,
    )

    # Classify
    result = classify_drone_video(bursts, center_freq_mhz, sample_rate, noise_db)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 drone_dsp.py <raw_file> <sample_rate> [center_freq_mhz]")
        print("  raw_file: signed 8-bit interleaved IQ file from hackrf_transfer")
        print("  sample_rate: in Hz (e.g. 8000000)")
        print("  center_freq_mhz: center frequency in MHz (default: auto from filename)")
        sys.exit(1)

    raw_file = sys.argv[1]
    sample_rate = int(sys.argv[2])
    center_freq_mhz = float(sys.argv[3]) if len(sys.argv) > 3 else 0

    # Read IQ data
    data = np.fromfile(raw_file, dtype=np.int8)
    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
    iq /= 128.0  # Normalize to [-1, 1]

    print(f"Loaded {len(iq)} samples from {raw_file}")
    print(f"Sample rate: {sample_rate/1e6:.1f} MHz")
    print(f"Duration: {len(iq)/sample_rate:.2f} s")

    result = analyze_drone_signal(iq, sample_rate, center_freq_mhz)

    print(f"\n=== Drone Signal Analysis ===")
    print(f"Detected: {result['detected']}")
    if result['detected']:
        print(f"Protocol: {result['protocol']}")
        print(f"Confidence: {result['confidence']:.0%}")
        print(f"Bandwidth: {result['bandwidth_mhz']:.1f} MHz")
        print(f"Duty cycle: {result['duty_cycle']:.0%}")
        print(f"Spectral flatness: {result['flatness']:.2f}")
        print(f"SNR: {result['snr_db']:.1f} dB")
        print(f"Center freq: {result['center_freq_mhz']:.1f} MHz")
        print(f"Is WiFi: {result['is_wifi']}")
    else:
        print(f"SNR: {result.get('snr_db', 0):.1f} dB")
        print(f"Bursts: {result.get('n_bursts', 0)}")
        print(f"Flatness: {result.get('flatness', 0):.2f}")
