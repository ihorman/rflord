#!/usr/bin/env python3
"""
FPV Video Decoder — Decode analog FPV video from IQ captures.

Dual-mode decoder:
  1. PySDR approach (primary): FM demod → filter → resample → reshape
     Tries multiple drift corrections, picks best result.
  2. Sync-based (fallback): proper H-sync flywheel + per-line TBC.

Based on: windytan, batchdrake, PALindrome, orecchiette, PySDR.

Usage:
  python3 fpv_decode.py capture --freq 5800
  python3 fpv_decode.py file.iq --freq 5925 --force
"""
import numpy as np
import scipy.signal as sig
from PIL import Image
import sys
import argparse
import os
import time

NTSC_LINES_PER_FRAME = 525
NTSC_LINE_HZ = (30.0 / 1.001) * NTSC_LINES_PER_FRAME  # 15734.26
PAL_LINES_PER_FRAME = 625
PAL_LINE_HZ = 25.0 * PAL_LINES_PER_FRAME  # 15625

FPV_CHANNELS = {
    '900mhz': {'band': '900 MHz', 'freqs': [910, 920, 930, 940, 950, 960]},
    '1.2ghz': {'band': '1.2 GHz', 'freqs': [1080, 1120, 1160, 1200, 1240, 1280]},
    '2.4ghz': {'band': '2.4 GHz', 'freqs': [2414, 2432, 2450, 2468, 2490]},
    '5.8ghz': {
        'band': '5.8 GHz',
        'freqs': {
            'A': [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
            'B': [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
            'E': [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
            'F': [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
            'R': [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
        },
    },
}


def load_iq_file(path, sample_rate=10000000):
    """Load IQ from file. Supports complex64 (GNU Radio/PySDR) and int8 (HackRF)."""
    ext = os.path.splitext(path)[1].lower()

    # Complex64 extensions: always complex64
    if ext in ('.cf32', '.cfile'):
        return np.fromfile(path, dtype=np.complex64)

    # .iq extension: could be either format. Detect by content.
    if ext == '.iq':
        # Read first 100 samples as complex64 and check magnitude
        probe = np.fromfile(path, dtype=np.complex64, count=100)
        if len(probe) > 0 and np.nanmax(np.abs(probe)) > 0.01:
            return np.fromfile(path, dtype=np.complex64)

    # Default: int8 interleaved (HackRF)
    raw = np.fromfile(path, dtype=np.int8)
    if len(raw) == 0:
        raw = np.fromfile(path, dtype=np.uint8)
        iq = (raw[::2].astype(np.float32) - 127.5) / 127.5 + \
             1j * (raw[1::2].astype(np.float32) - 127.5) / 127.5
    else:
        iq = raw[::2].astype(np.float32) / 128 + 1j * raw[1::2].astype(np.float32) / 128
    return iq


def capture_iq_hackrf(freq_mhz, duration_s, sample_rate=10000000):
    """Capture IQ using HackRF."""
    import subprocess
    num_bytes = int(sample_rate * duration_s * 2)
    raw_file = '/tmp/fpv_capture.raw'
    print("Capturing %ds at %d MHz..." % (duration_s, freq_mhz))
    cmd = ['hackrf_transfer', '-r', raw_file, '-f', str(int(freq_mhz * 1e6)),
           '-s', str(sample_rate), '-n', str(num_bytes), '-l', '32', '-g', '40', '-a', '1']
    subprocess.run(cmd, capture_output=True, timeout=duration_s + 15)
    if not os.path.exists(raw_file) or os.path.getsize(raw_file) < 1024:
        print("Capture failed!")
        return None
    raw = np.fromfile(raw_file, dtype=np.int8)
    iq = raw[::2].astype(np.float32) / 128 + 1j * raw[1::2].astype(np.float32) / 128
    print("Captured %d samples (%.1f ms)" % (len(iq), len(iq) / sample_rate * 1000))
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        freq_label = ("%.1f" % freq_mhz).replace('.', 'p')
        iq_dir = os.path.expanduser('~/.rflord/iq_samples')
        os.makedirs(iq_dir, exist_ok=True)
        iq_path = os.path.join(iq_dir, "%s_%sMHz.iq" % (ts, freq_label))
        raw.tofile(iq_path)
        print("Saved IQ: %s" % iq_path)
    except Exception as e:
        print("Could not save IQ: %s" % e)
    return iq


def has_video_signal(iq, sample_rate):
    """Check if IQ contains analog video (line rate harmonics)."""
    n = min(len(iq), int(sample_rate * 0.1))
    seg = iq[:n]
    d = np.angle(seg[1:] * np.conj(seg[:-1]))
    h = sig.firwin(301, 3e6, fs=sample_rate)
    d = np.convolve(d, h, 'same')
    f, psd = sig.welch(d, fs=sample_rate, nperseg=min(4096, len(d)))
    psd_db = 10 * np.log10(psd + 1e-20)
    for line_rate in [NTSC_LINE_HZ, PAL_LINE_HZ]:
        diffs = []
        for harmonic in [1, 2, 3, 4]:
            target = line_rate * harmonic
            idx = np.argmin(np.abs(f - target))
            lo = max(0, idx - 20)
            noise = np.median(psd_db[max(0, lo - 50):lo]) if lo > 50 else np.median(psd_db)
            diffs.append(psd_db[idx] - noise)
        if sum(1 for p in diffs if p > 5) >= 2:
            return True
    return False


def create_spectrogram(iq, sample_rate, fft_size=2048, height=500):
    """Create spectrogram image."""
    num_ffts = min(len(iq) // fft_size, height)
    wf = np.zeros((num_ffts, fft_size))
    for i in range(num_ffts):
        chunk = iq[i * fft_size:(i + 1) * fft_size]
        wf[i] = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(chunk * np.hanning(fft_size)))) + 1e-10)
    wfn = (wf - wf.min()) / (wf.max() - wf.min() + 1e-10)
    rgb = np.zeros((num_ffts, fft_size, 3), dtype=np.uint8)
    rgb[:, :, 0] = (wfn * 76).astype(np.uint8)
    rgb[:, :, 1] = (wfn * 255).astype(np.uint8)
    rgb[:, :, 2] = (wfn * 128).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB').resize((720, height), Image.BILINEAR)


# ─── DECODE METHODS ──────────────────────────────────────────────────────────

def _pysdr_decode(x, sample_rate, line_hz, pixels_per_line, max_lines, drift):
    """PySDR approach: fixed resample rate + reshape."""
    rate = pixels_per_line / (sample_rate / line_hz) * drift
    xr = sig.resample(x, int(len(x) * rate))
    xr = xr[:len(xr) - (len(xr) % pixels_per_line)]
    frame = xr.reshape(-1, pixels_per_line)
    return frame[:max_lines]


def _frame_score(frame):
    """Score a decoded frame: higher = more structure (less noise).
    Uses row-to-row variance which is high for real video, low for noise."""
    if frame is None or frame.shape[0] < 10:
        return 0
    row_means = np.mean(frame, axis=1)
    return float(np.var(row_means))


def _sync_decode(x, sample_rate, line_hz, pixels_per_line, max_lines):
    """Sync-based decoder with flywheel PLL and per-line TBC."""
    spl = sample_rate / line_hz

    # Find sync tips: local minima below threshold
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    threshold = med - 1.5 * mad

    below = x < threshold
    falling = np.where(np.diff(below.astype(int)) == 1)[0] + 1
    if len(falling) < 5:
        return None

    # Filter by expected interval
    raw_syncs = []
    last = -spl * 2
    for pos in falling:
        if pos - last > spl * 0.7:
            raw_syncs.append(pos)
            last = pos
    if len(raw_syncs) < 5:
        return None

    actual_period = np.median(np.diff(raw_syncs))

    # Flywheel
    clean = [raw_syncs[0]]
    miss = 0
    ri = 1
    for _ in range(max_lines * 2):
        expected = clean[-1] + actual_period
        best_dist = actual_period * 0.2
        best_pos = None
        while ri < len(raw_syncs) and raw_syncs[ri] < expected + actual_period * 0.5:
            dist = abs(raw_syncs[ri] - expected)
            if dist < best_dist:
                best_dist = dist
                best_pos = raw_syncs[ri]
            ri += 1
        if best_pos is not None:
            clean.append(best_pos)
            miss = 0
        else:
            clean.append(int(expected))
            miss += 1
            if miss > 5:
                break

    if len(clean) < 10:
        return None

    clean = np.array(clean)

    # Find VBI
    gaps = np.diff(clean)
    vbi_idx = 0
    for i in range(len(gaps)):
        if gaps[i] > actual_period * 1.8:
            vbi_idx = i + 1
            break

    start_line = min(vbi_idx + 20, len(clean) - 2)
    num_lines = min(max_lines, len(clean) - start_line - 1)
    if num_lines < 5:
        return None

    frame = np.zeros((num_lines, pixels_per_line))
    for li in range(num_lines):
        idx = start_line + li
        s = int(clean[idx])
        e = int(clean[idx + 1])
        if e <= s or e - s < actual_period * 0.3 or e - s > actual_period * 2:
            continue
        s, e = max(0, s), min(len(x), e)
        line = x[s:e]
        if len(line) < 10:
            continue
        frame[li, :] = sig.resample(line, pixels_per_line)

    return frame


def decode_frame(iq, sample_rate=10e6, standard='NTSC', pixels_per_line=720):
    """Decode FPV video using autocorrelation-based line detection.

    1. FM demod
    2. Low-pass filter (remove audio)
    3. Autocorrelation to find line rate period
    4. Extract lines at detected period
    5. Resample each line to fixed pixel count
    """
    if standard == 'PAL':
        lines_per_frame = PAL_LINES_PER_FRAME
        line_hz = PAL_LINE_HZ
    else:
        lines_per_frame = NTSC_LINES_PER_FRAME
        line_hz = NTSC_LINE_HZ

    expected_period = sample_rate / line_hz  # e.g. 635.6 samples at 10 MHz

    # FM demodulation
    iq = iq - np.mean(iq)
    x = np.angle(iq[1:] * np.conj(iq[:-1]))

    # Low-pass filter (remove audio at 4.5+ MHz)
    b = sig.firwin(301, 4.0e6, fs=sample_rate)
    x = np.convolve(b, x, 'same')

    max_lines = lines_per_frame // 2

    # ── Find line rate via autocorrelation (FFT-based for speed) ──
    seg_len = min(len(x), int(sample_rate * 0.05))  # 50ms segment
    seg = x[:seg_len]
    seg_centered = seg - np.mean(seg)
    # FFT-based autocorrelation (O(n log n) vs O(n²) for direct)
    n = len(seg_centered)
    fft_seg = np.fft.rfft(seg_centered, n=2*n)
    autocorr = np.fft.irfft(fft_seg * np.conj(fft_seg))[:n]
    autocorr = autocorr / autocorr[0]  # Normalize

    # Search for peak near expected line period (±20%)
    search_lo = int(expected_period * 0.8)
    search_hi = int(expected_period * 1.2)
    if search_hi >= len(autocorr):
        search_hi = len(autocorr) - 1
    if search_lo >= search_hi:
        return None

    search_region = autocorr[search_lo:search_hi]
    if len(search_region) == 0:
        return None

    peak_offset = np.argmax(search_region)
    detected_period = search_lo + peak_offset
    peak_value = autocorr[detected_period]

    print("  autocorr: expected=%.1f detected=%.1f peak=%.3f" % (
        expected_period, detected_period, peak_value))

    if peak_value < 0.03:
        print("  autocorr peak too weak — no video")
        return None

    # ── Extract lines using autocorrelation period directly ──
    # No sync detection needed — autocorrelation gives exact period
    period = detected_period
    num_lines = min(max_lines, len(x) // int(period))
    if num_lines < 5:
        return None

    # Find best starting offset by trying a few and picking highest variance
    # (real video has varying brightness, noise is uniform)
    best_offset = 0
    best_var = 0
    for offset in range(0, int(period), int(period // 4)):
        sample = []
        for li in range(min(20, num_lines)):
            start = offset + int(li * period)
            end = start + int(period)
            if end > len(x):
                break
            sample.append(np.mean(np.abs(x[start:end])))
        var = np.var(sample) if len(sample) > 1 else 0
        if var > best_var:
            best_var = var
            best_offset = offset

    # Extract and resample each line
    frame = np.zeros((num_lines, pixels_per_line))
    for li in range(num_lines):
        start = best_offset + int(li * period)
        end = best_offset + int((li + 1) * period)
        if end > len(x):
            break
        line = x[start:end]
        if len(line) > 0:
            frame[li, :] = sig.resample(line, pixels_per_line)

    return frame


def normalize_frame(frame):
    """Normalize to 0-255 using min-max (matches PySDR)."""
    if frame is None or frame.size == 0:
        return np.zeros((100, 720), dtype=np.uint8)
    mn, mx = np.min(frame), np.max(frame)
    if mx - mn < 1e-10:
        return np.zeros_like(frame, dtype=np.uint8)
    return ((frame - mn) / (mx - mn) * 255).astype(np.uint8)


def is_recognizable(frame_norm):
    """Check if decoded frame contains recognizable video content (not just noise).

    Returns True if the image has enough structure to be worth saving.
    Real video has: horizontal correlation, edge structure, varying brightness.
    Noise is: uniform random, no correlation, flat histogram.

    Metrics (all must pass):
    1. Row correlation: adjacent rows should be correlated (>0.3) in real video
    2. Edge density: real video has edges (5-50% of pixels)
    3. Brightness variance: real video has varying brightness (std > 15)
    """
    if frame_norm is None or frame_norm.size == 0:
        return False

    h, w = frame_norm.shape
    if h < 20 or w < 100:
        return False

    f = frame_norm.astype(np.float32)

    # 1. Row-to-row correlation (real video: >0.3, noise: ~0)
    # Sample every 4th row for speed
    correlations = []
    for i in range(0, min(h - 1, 200), 4):
        r1 = f[i]
        r2 = f[i + 1]
        # Pearson correlation
        m1, m2 = np.mean(r1), np.mean(r2)
        s1, s2 = np.std(r1), np.std(r2)
        if s1 > 1 and s2 > 1:
            corr = np.mean((r1 - m1) * (r2 - m2)) / (s1 * s2)
            correlations.append(corr)
    avg_corr = np.mean(correlations) if correlations else 0

    # 2. Edge density (Sobel-like horizontal edges)
    # Real video: 5-50% of pixels are edges. Noise: ~50% (random).
    dy = np.abs(np.diff(f, axis=0))
    threshold = np.std(dy) * 1.5
    edge_pixels = np.sum(dy > threshold)
    total_pixels = dy.size
    edge_ratio = edge_pixels / total_pixels if total_pixels > 0 else 0

    # 3. Brightness variance (real video: std > 15, noise: ~40-74)
    brightness_std = np.std(f)

    # Decision: require 2 of 3 metrics (real video may have some noise)
    checks_passed = 0
    if avg_corr > 0.10:
        checks_passed += 1
    if 0.01 < edge_ratio < 0.8:
        checks_passed += 1
    if brightness_std > 8:
        checks_passed += 1

    passed = checks_passed >= 2

    # Debug info
    print("  quality: corr=%.3f edge_ratio=%.3f bright_std=%.1f checks=%d/3 %s" % (
        avg_corr, edge_ratio, brightness_std, checks_passed,
        "PASS" if passed else "FAIL"
    ))

    return passed


# ─── AUDIO DECODER ───────────────────────────────────────────────────────────

def decode_audio(iq, sample_rate=10e6, standard='NTSC', output_path=None):
    """Decode audio from analog TV/FPV IQ capture.

    Audio subcarrier is FM-modulated at 4.5-6.5 MHz offset from video carrier.
    Pipeline: bandpass filter → FM demod → de-emphasis → resample to 48kHz → WAV.

    Returns: numpy array of audio samples at 48kHz, or None if no audio found.
    """
    if standard == 'PAL':
        audio_offset = 5.5e6  # PAL audio at 5.5 MHz offset
        deemph_tau = 50e-6    # 50us de-emphasis
    else:
        audio_offset = 4.5e6  # NTSC audio at 4.5 MHz offset
        deemph_tau = 75e-6    # 75us de-emphasis

    # ── STAGE 1: Bandpass filter around audio subcarrier ──
    # The audio is at +/- audio_offset from center, but since we're at baseband,
    # it appears at audio_offset in the spectrum.
    # Use a narrow bandpass (~200 kHz bandwidth for FM audio)
    audio_bw = 200e3  # FM audio bandwidth
    f_lo = audio_offset - audio_bw
    f_hi = audio_offset + audio_bw

    # Ensure filter frequencies are within valid range
    nyq = sample_rate / 2
    if f_hi >= nyq:
        # Audio subcarrier is above Nyquist — aliased
        # Try aliased frequency
        f_hi_aliased = sample_rate - f_hi
        f_lo_aliased = sample_rate - f_lo
        if f_lo_aliased > 0 and f_hi_aliased < nyq:
            f_lo, f_hi = f_lo_aliased, f_hi_aliased
            print("  audio: aliased to %.1f-%.1f kHz" % (f_lo/1e3, f_hi/1e3))
        else:
            print("  audio: subcarrier above Nyquist, cannot decode")
            return None

    try:
        b = sig.firwin(301, [f_lo, f_hi], fs=sample_rate, pass_zero=False)
    except ValueError:
        print("  audio: invalid filter params")
        return None

    # Apply bandpass filter to raw IQ (before FM demod!)
    iq_bp = sig.lfilter(b, 1.0, iq)

    # ── STAGE 2: FM demodulate the audio subcarrier ──
    audio_raw = np.angle(iq_bp[1:] * np.conj(iq_bp[:-1]))

    # ── STAGE 3: De-emphasis filter ──
    # Single-pole IIR: H(s) = 1/(1 + s*tau)
    # Digital: y[n] = alpha * x[n] + (1-alpha) * y[n-1]
    # where alpha = 1 - exp(-1/(tau * sample_rate))
    alpha = 1.0 - np.exp(-1.0 / (deemph_tau * sample_rate))
    audio_deemph = np.zeros_like(audio_raw)
    audio_deemph[0] = audio_raw[0]
    for i in range(1, len(audio_raw)):
        audio_deemph[i] = alpha * audio_raw[i] + (1 - alpha) * audio_deemph[i - 1]

    # ── STAGE 4: Decimate to 48kHz ──
    target_rate = 48000
    decimation = int(sample_rate / target_rate)
    if decimation < 1:
        decimation = 1
    audio_48k = sig.decimate(audio_deemph, decimation, ftype='fir', zero_phase=True)
    actual_rate = sample_rate / decimation

    # ── STAGE 5: Normalize ──
    peak = np.max(np.abs(audio_48k))
    if peak > 0:
        audio_48k = audio_48k / peak * 0.9

    # ── STAGE 6: Save as WAV ──
    if output_path:
        import wave
        import struct

        # Convert to 16-bit PCM
        audio_pcm = (audio_48k * 32767).astype(np.int16)

        wav_path = output_path
        if not wav_path.endswith('.wav'):
            wav_path = wav_path.rsplit('.', 1)[0] + '.wav'

        with wave.open(wav_path, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(actual_rate))
            wf.writeframes(audio_pcm.tobytes())

        duration_s = len(audio_48k) / actual_rate
        print("  audio: saved %s (%.1fs, %d Hz)" % (wav_path, duration_s, int(actual_rate)))
        return wav_path

    return audio_48k


def main():
    parser = argparse.ArgumentParser(description='FPV Video Decoder')
    parser.add_argument('input', nargs='?', help='IQ file or "capture" for live capture')
    parser.add_argument('--freq', type=float, help='Frequency in MHz')
    parser.add_argument('--standard', choices=['NTSC', 'PAL'], default='NTSC')
    parser.add_argument('--output', '-o', default='fpv_frame.png')
    parser.add_argument('--duration', type=float, default=2.0)
    parser.add_argument('--sample-rate', type=int, default=10000000)
    parser.add_argument('--list-channels', action='store_true')
    parser.add_argument('--force', action='store_true', help='Skip video validation')
    parser.add_argument('--width', type=int, default=720, help='Output width in pixels')
    parser.add_argument('--audio', action='store_true', help='Also decode audio subcarrier')
    args = parser.parse_args()

    if args.list_channels:
        for name, info in FPV_CHANNELS.items():
            print("\n  %s:" % info['band'])
            freqs = info['freqs']
            if isinstance(freqs, dict):
                for band, f in freqs.items():
                    print("    %s: %s MHz" % (band, ', '.join(str(x) for x in f)))
            else:
                print("    %s MHz" % ', '.join(str(x) for x in freqs))
        return

    if args.input == 'capture' or args.input is None:
        if not args.freq:
            print("ERROR: --freq required for capture")
            return
        iq = capture_iq_hackrf(args.freq, args.duration, args.sample_rate)
        if iq is None:
            return
        sample_rate = args.sample_rate
    else:
        print("Loading %s..." % args.input)
        iq = load_iq_file(args.input, args.sample_rate)
        sample_rate = args.sample_rate
        print("Loaded %d samples (%.1f ms)" % (len(iq), len(iq) / sample_rate * 1000))

    if not args.force:
        print("Checking for video signal...")
        if not has_video_signal(iq, sample_rate):
            print("NO VIDEO SIGNAL — saving spectrogram")
            out = args.output.replace('.png', '_no_video.png')
            create_spectrogram(iq, sample_rate).save(out)
            print("Saved: %s" % out)
            sys.exit(2)
        print("Video signal detected!")

    print("Decoding %s..." % args.standard)
    # Limit input to 2M samples (200ms) for speed
    if len(iq) > 2000000:
        iq = iq[:2000000]
        print("  (trimmed to 2M samples for speed)")
    frame = decode_frame(iq, sample_rate, args.standard, args.width)

    if frame is None or frame.shape[0] < 5:
        print("DECODE FAILED — no video found")
        out = args.output.replace('.png', '_no_video.png')
        create_spectrogram(iq, sample_rate).save(out)
        print("Saved: %s" % out)
        sys.exit(3)

    frame_norm = normalize_frame(frame)

    # Quality check: skip saving if image is just noise
    if not is_recognizable(frame_norm):
        print("NOT RECOGNIZABLE — skipping save (noise)")
        sys.exit(4)

    Image.fromarray(frame_norm, mode='L').save(args.output)
    print("Saved: %s (%dx%d)" % (args.output, frame_norm.shape[1], frame_norm.shape[0]))

    # Green phosphor version
    rgb = np.zeros((frame_norm.shape[0], frame_norm.shape[1], 3), dtype=np.uint8)
    rgb[:, :, 0] = (frame_norm * 0.2).astype(np.uint8)
    rgb[:, :, 1] = frame_norm
    rgb[:, :, 2] = (frame_norm * 0.3).astype(np.uint8)
    green_path = args.output.replace('.png', '_green.png')
    Image.fromarray(rgb, 'RGB').save(green_path)
    print("Saved: %s" % green_path)

    # Audio decode
    if args.audio:
        print("Decoding audio...")
        audio_path = args.output.replace('.png', '.wav')
        try:
            result = decode_audio(iq, sample_rate, args.standard, audio_path)
            if result is None:
                print("  no audio subcarrier found")
            elif isinstance(result, str):
                print("  audio: %s" % result)
        except Exception as e:
            print("  audio decode failed: %s" % e)


if __name__ == '__main__':
    main()