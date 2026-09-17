#!/usr/bin/env python3
"""
FPV Video Decoder — Decode analog FPV video from IQ captures.
Supports NTSC and PAL standards on 900 MHz, 1.2 GHz, 2.4 GHz, 5.8 GHz bands.

Key techniques:
  - Complex-conjugate FM demod (standard SDR approach)
  - DC offset removal before demod
  - Audio subcarrier filtering (3 MHz cutoff)
  - De-emphasis filter (75ns time constant)
  - Adaptive sync detection (128-chunk min-averaging)
  - Back-porch DC restoration per line
  - IRE-level normalization (sync=0, black=7.5, white=100)

Usage:
  python3 fpv_decode.py capture --freq 5800 --standard NTSC
  python3 fpv_decode.py capture.raw --freq 1280 --standard PAL
  python3 fpv_decode.py capture --freq 5800 --auto
"""
import numpy as np
from PIL import Image
from scipy.signal import firwin, lfilter, find_peaks
import sys
import argparse
import os
import time

# ============================================================
#  TV STANDARD DEFINITIONS
# ============================================================

NTSC = {
    'name': 'NTSC',
    'total_lines': 525,
    'active_lines': 480,
    'fps': 29.97,
    'line_freq': 29.97 * 525,  # 15734.26 Hz
    'line_duration': 1.0 / (29.97 * 525),  # 63.556 us
    'h_sync_width': 4.7e-6,
    'back_porch': 8.0e-6,
    'active_start': 10.0e-6,
    'active_duration': 52.6e-6,
    'video_bw': 4.2e6,
    'fm_deviation': 4.0e6,
    'audio_offset': 4.5e6,
    'deemph_tau': 75e-9,
}

PAL = {
    'name': 'PAL',
    'total_lines': 625,
    'active_lines': 576,
    'fps': 25.0,
    'line_freq': 25.0 * 625,  # 15625 Hz
    'line_duration': 1.0 / (25.0 * 625),  # 64.0 us
    'h_sync_width': 4.7e-6,
    'back_porch': 8.0e-6,
    'active_start': 10.0e-6,
    'active_duration': 52.0e-6,
    'video_bw': 5.0e6,
    'fm_deviation': 5.0e6,
    'audio_offset': 5.5e6,
    'deemph_tau': 75e-9,
}

# ============================================================
#  FPV CHANNEL PLANS
# ============================================================

FPV_CHANNELS = {
    '900mhz': {
        'band': '900 MHz',
        'freqs': [910, 920, 930, 940, 950, 960],
        'description': 'Long range FPV, analog cameras',
    },
    '1.2ghz': {
        'band': '1.2 GHz',
        'freqs': [1080, 1120, 1160, 1200, 1240, 1280],
        'description': 'Long range FPV, penetrating',
    },
    '2.4ghz': {
        'band': '2.4 GHz',
        'freqs': [2414, 2432, 2450, 2468, 2490],
        'description': 'Analog video TX, shared with WiFi',
    },
    '5.8ghz': {
        'band': '5.8 GHz',
        'freqs': {
            'A': [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
            'B': [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
            'E': [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
            'F': [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
            'R': [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
        },
        'description': 'Most common FPV band, short range',
    },
}

# ============================================================
#  IQ CAPTURE
# ============================================================

def capture_iq_hackrf(freq_mhz, duration_s, sample_rate=10000000):
    """Capture IQ using HackRF"""
    import subprocess

    num_bytes = int(sample_rate * duration_s * 2)
    raw_file = '/tmp/fpv_capture.raw'

    print("Capturing %ds at %d MHz (SR=%d MHz)..." % (duration_s, freq_mhz, sample_rate/1e6))

    cmd = [
        'hackrf_transfer', '-r', raw_file,
        '-f', str(int(freq_mhz * 1e6)),
        '-s', str(sample_rate),
        '-n', str(num_bytes),
        '-l', '32', '-g', '40', '-a', '1',
    ]

    r = subprocess.run(cmd, capture_output=True, timeout=duration_s + 15)

    if not os.path.exists(raw_file) or os.path.getsize(raw_file) < 1024:
        print("Capture failed!")
        return None, sample_rate

    # Convert to complex float32
    raw = np.fromfile(raw_file, dtype=np.int8)
    iq = raw[::2].astype(np.float32)/128 + 1j*raw[1::2].astype(np.float32)/128

    print("Captured %d samples (%.1f ms)" % (len(iq), len(iq)/sample_rate*1000))

    # Save IQ alongside screenshots for later redecoding
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        freq_label = f"{freq_mhz:.1f}".replace('.', 'p')
        iq_dir = os.path.expanduser('~/.flord/iq_samples')
        os.makedirs(iq_dir, exist_ok=True)
        iq_path = os.path.join(iq_dir, f"{ts}_{freq_label}MHz.iq")
        raw.tofile(iq_path)
        print("Saved IQ sample: %s" % iq_path)
    except Exception as e:
        print("Could not save IQ: %s" % e)

    return iq, sample_rate


def load_iq_file(path, sample_rate=10000000):
    """Load IQ from file"""
    raw = np.fromfile(path, dtype=np.int8)
    if len(raw) == 0:
        # Try unsigned 8-bit (RTL-SDR format)
        raw = np.fromfile(path, dtype=np.uint8)
        iq = (raw[::2].astype(np.float32) - 127.5)/127.5 + \
             1j*(raw[1::2].astype(np.float32) - 127.5)/127.5
    else:
        iq = raw[::2].astype(np.float32)/128 + 1j*raw[1::2].astype(np.float32)/128
    return iq


# ============================================================
#  FM DEMODULATION (proper SDR approach)
# ============================================================

def fm_demodulate(iq, sample_rate, max_deviation=4.0e6):
    """FM demodulate using complex-conjugate multiplication.
    Standard SDR approach — avoids phase unwrapping artifacts."""
    # Remove DC offset first (critical for stable demod)
    iq = iq - np.mean(iq)

    # Complex conjugate multiplication
    demod = np.angle(iq[1:] * np.conj(iq[:-1]))

    return demod


def filter_audio_subcarrier(demod, sample_rate, cutoff=3.0e6):
    """Remove audio subcarrier from demodulated video.
    Audio is at 4.5-6.5 MHz offset — filter it out."""
    nyquist = sample_rate / 2
    freq = cutoff / nyquist
    if freq >= 1.0:
        freq = 0.99
    h = firwin(301, freq)
    return np.convolve(demod, h, 'same')


def deemphasis(video, sample_rate, tau=75e-9):
    """De-emphasis filter — undo transmitter pre-emphasis.
    Single-pole IIR low-pass with time constant tau."""
    dt = 1.0 / sample_rate
    alpha = dt / (tau + dt)
    b = [alpha]
    a = [1, -(1 - alpha)]
    return lfilter(b, a, video)


def am_demodulate(iq):
    """AM demodulate — extract envelope"""
    return np.abs(iq)


# ============================================================
#  SIGNAL VALIDATION
# ============================================================

def has_video_signal(iq, sample_rate):
    """Check if IQ data contains an analog video signal by looking for
    spectral peaks at the NTSC/PAL line rate harmonics."""
    from scipy.signal import welch

    # FM demod a short segment with proper approach
    n = min(len(iq), int(sample_rate * 0.1))  # 100ms max
    seg = iq[:n]
    iq_dc = seg - np.mean(seg)
    demod = np.angle(iq_dc[1:] * np.conj(iq_dc[:-1]))

    # Low-pass filter to remove audio
    demod = filter_audio_subcarrier(demod, sample_rate, cutoff=3.5e6)

    # Power spectrum
    f, psd = welch(demod, fs=sample_rate, nperseg=min(4096, len(demod)))
    psd_db = 10 * np.log10(psd + 1e-20)

    # Check for peaks at line rate harmonics
    for line_rate in [15734, 15625]:
        harmonic_powers = []
        for h in [1, 2, 3, 4]:
            target = line_rate * h
            idx = np.argmin(np.abs(f - target))
            lo = max(0, idx - 20)
            local_noise = np.median(psd_db[max(0, lo-50):lo]) if lo > 50 else np.median(psd_db)
            peak_power = psd_db[idx]
            harmonic_powers.append(peak_power - local_noise)

        # If at least 2 of 4 harmonics are >5 dB above local noise, it's video
        strong = sum(1 for p in harmonic_powers if p > 5)
        if strong >= 2:
            return True

    return False


# ============================================================
#  SYNC DETECTION (adaptive thresholding)
# ============================================================

def find_sync_pulses(video, sample_rate, tv_std):
    """Find horizontal sync pulses using adaptive thresholding.
    128-chunk method: split signal into 128 chunks, find minimum of each,
    average to get sync level. Then find sync crossings."""
    line_samples = int(tv_std['line_duration'] * sample_rate)

    # Adaptive sync level detection (128-chunk method)
    chunk_size = len(video) // 128
    if chunk_size < 10:
        return np.array([]), 0, 0

    chunk_mins = [np.min(video[i*chunk_size:(i+1)*chunk_size]) for i in range(128)]
    sync_level = np.mean(chunk_mins) + 0.1 * np.std(chunk_mins)

    # Black level estimate (median of the signal)
    black_level = np.median(video)

    # Threshold at 25% between sync level and black level
    threshold = sync_level + 0.25 * (black_level - sync_level)

    # Find sync crossings (signal drops below threshold)
    below = video < threshold
    crossings = np.where(np.diff(below.astype(int)))[0]

    if len(crossings) < 2:
        return np.array([]), sync_level, black_level

    # Filter for valid sync pulse widths
    sync_samples = int(tv_std['h_sync_width'] * sample_rate)
    min_sync = int(sync_samples * 0.3)
    max_sync = int(sync_samples * 3.0)

    valid_syncs = []
    i = 0
    while i < len(crossings) - 1:
        pulse_width = crossings[i+1] - crossings[i]
        if min_sync < pulse_width < max_sync:
            valid_syncs.append(crossings[i])
            i += 2  # skip the trailing edge
        else:
            i += 1

    return np.array(valid_syncs), sync_level, black_level


def find_v_sync(h_sync_positions, sample_rate, tv_std):
    """Find vertical sync by looking for gaps > 1.5x line spacing."""
    if len(h_sync_positions) < 10:
        return []

    gaps = np.diff(h_sync_positions)
    line_samples = int(tv_std['line_duration'] * sample_rate)

    vsync = []
    for i, gap in enumerate(gaps):
        if gap > line_samples * 1.5:
            vsync.append(h_sync_positions[i])

    return vsync


# ============================================================
#  DC RESTORATION (back-porch clamping)
# ============================================================

def restore_dc(video, sync_positions, sample_rate, tv_std):
    """DC restoration using back-porch clamping.
    For each line, sample the back porch to get black reference."""
    back_porch_start = int(tv_std['back_porch'] * 0.5 * sample_rate)  # 4us after sync
    back_porch_len = int(3e-6 * sample_rate)  # 3us sample window

    restored = np.copy(video)

    for i in range(len(sync_positions)):
        porch_start = sync_positions[i] + back_porch_start
        porch_end = porch_start + back_porch_len
        if porch_end < len(video):
            black_ref = np.mean(video[porch_start:porch_end])
            if i + 1 < len(sync_positions):
                line_end = sync_positions[i+1]
            else:
                line_end = min(sync_positions[i] + int(tv_std['line_duration'] * sample_rate), len(video))
            restored[porch_start:line_end] -= black_ref

    return restored


# ============================================================
#  FRAME EXTRACTION
# ============================================================

def extract_frame(video, h_sync_positions, sample_rate, tv_std):
    """Extract one video frame from demodulated signal."""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    active_lines = tv_std['active_lines']
    pixels_per_line = 720

    # Find vertical sync
    vsync = find_v_sync(h_sync_positions, sample_rate, tv_std)

    if vsync:
        frame_start = vsync[0]
        vblank_lines = 20
        frame_start += vblank_lines * line_samples
    else:
        frame_start = h_sync_positions[0]

    # Find sync index closest to frame start
    sync_idx = 0
    for i, pos in enumerate(h_sync_positions):
        if pos >= frame_start:
            sync_idx = i
            break

    # Extract scanlines
    frame = np.zeros((active_lines, pixels_per_line), dtype=np.float32)
    lines_extracted = 0

    for line in range(active_lines):
        if sync_idx + line >= len(h_sync_positions):
            break

        line_start = h_sync_positions[sync_idx + line]

        # Skip sync pulse + back porch
        active_start = line_start + int(tv_std['active_start'] * sample_rate)

        # Active line duration
        active_duration = int(tv_std['active_duration'] * sample_rate)
        active_end = active_start + active_duration

        if active_end > len(video):
            break

        # Extract and resample to pixel width
        line_data = video[active_start:active_end]
        if len(line_data) > 0:
            indices = np.linspace(0, len(line_data)-1, pixels_per_line)
            frame[lines_extracted] = np.interp(indices, np.arange(len(line_data)), line_data)
            lines_extracted += 1

    frame = frame[:lines_extracted]
    return frame


def normalize_frame(frame, sync_level=None, black_level=None):
    """Normalize frame to 0-255 uint8.
    Always uses percentile-based normalization — more robust than IRE-based
    because IRE levels depend on accurate sync/black detection which often
    fails with real-world signals."""
    p_low = np.percentile(frame, 2)
    p_high = np.percentile(frame, 98)
    if p_high - p_low < 1e-10:
        return np.zeros_like(frame, dtype=np.uint8)
    frame_norm = (frame - p_low) / (p_high - p_low)
    frame_norm = np.clip(frame_norm, 0, 1)
    return (frame_norm * 255).astype(np.uint8)


# ============================================================
#  SPECTROGRAM
# ============================================================

def create_spectrogram(iq, sample_rate, fft_size=2048, height=500):
    """Create spectrogram image"""
    num_ffts = min(len(iq) // fft_size, height)
    wf = np.zeros((num_ffts, fft_size))
    for i in range(num_ffts):
        chunk = iq[i*fft_size:(i+1)*fft_size]
        wf[i] = 20*np.log10(np.abs(np.fft.fftshift(np.fft.fft(chunk*np.hanning(fft_size))))+1e-10)
    wfn = (wf-wf.min())/(wf.max()-wf.min()+1e-10)
    rgb = np.zeros((num_ffts, fft_size, 3), dtype=np.uint8)
    rgb[:,:,0] = (wfn*76).astype(np.uint8)
    rgb[:,:,1] = (wfn*255).astype(np.uint8)
    rgb[:,:,2] = (wfn*128).astype(np.uint8)
    return Image.fromarray(rgb, 'RGB').resize((720, height), Image.BILINEAR)


# ============================================================
#  AUTO DETECTION
# ============================================================

def detect_standard(video, sample_rate):
    """Auto-detect NTSC or PAL from video signal."""
    ntsc_line = int(NTSC['line_duration'] * sample_rate)
    pal_line = int(PAL['line_duration'] * sample_rate)

    # Low-pass filter
    nyquist = sample_rate / 2
    freq = min(0.99, NTSC['video_bw'] / nyquist)
    h = firwin(301, freq)
    filtered = np.convolve(video, h, 'same')

    # Find negative peaks
    inv = -filtered
    peaks, _ = find_peaks(inv, distance=int(min(ntsc_line, pal_line)*0.5),
                           prominence=np.std(filtered)*0.5)

    if len(peaks) < 10:
        return None, peaks

    spacings = np.diff(peaks)
    avg_spacing = np.mean(spacings)

    ntsc_match = abs(avg_spacing - ntsc_line) / ntsc_line
    pal_match = abs(avg_spacing - pal_line) / pal_line

    if ntsc_match < pal_match and ntsc_match < 0.1:
        return NTSC, peaks
    elif pal_match < 0.1:
        return PAL, peaks
    else:
        return NTSC if ntsc_match < pal_match else PAL, peaks


# ============================================================
#  MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='FPV Video Decoder')
    parser.add_argument('input', nargs='?', help='IQ file or "capture" for live capture')
    parser.add_argument('--freq', type=float, help='Frequency in MHz')
    parser.add_argument('--standard', choices=['NTSC', 'PAL'], help='TV standard')
    parser.add_argument('--auto', action='store_true', help='Auto-detect standard')
    parser.add_argument('--output', '-o', default='fpv_frame.png', help='Output file')
    parser.add_argument('--duration', type=float, default=2.0, help='Capture duration')
    parser.add_argument('--sample-rate', type=int, default=10000000, help='Sample rate')
    parser.add_argument('--list-channels', action='store_true', help='List FPV channels')
    parser.add_argument('--spectrogram', action='store_true', help='Save spectrogram')
    args = parser.parse_args()

    if args.list_channels:
        print("\nFPV Channel Plans:")
        for name, info in FPV_CHANNELS.items():
            print("\n  %s (%s):" % (info['band'], info['description']))
            if isinstance(info['freqs'], dict):
                for band, freqs in info['freqs'].items():
                    print("    %s: %s MHz" % (band, ', '.join(str(f) for f in freqs)))
            else:
                print("    %s MHz" % ', '.join(str(f) for f in info['freqs']))
        return

    # Get IQ data
    if args.input == 'capture' or args.input is None:
        if not args.freq:
            print("ERROR: --freq required for capture")
            return
        iq, sample_rate = capture_iq_hackrf(args.freq, args.duration, args.sample_rate)
        if iq is None:
            return
    else:
        print("Loading IQ from %s..." % args.input)
        iq = load_iq_file(args.input, args.sample_rate)
        sample_rate = args.sample_rate
        print("Loaded %d samples (%.1f ms)" % (len(iq), len(iq)/sample_rate*1000))

    # Save spectrogram if requested
    if args.spectrogram:
        spec_img = create_spectrogram(iq, sample_rate)
        spec_path = args.output.replace('.png', '_spectrogram.png')
        spec_img.save(spec_path)
        print("Saved spectrogram: %s" % spec_path)

    # Check if there's actually a video signal before trying to decode
    print("\nChecking for video signal...")
    if not has_video_signal(iq, sample_rate):
        print("NO VIDEO SIGNAL DETECTED — skipping decode")
        print("Saving spectrogram instead...")
        spec_img = create_spectrogram(iq, sample_rate)
        spec_img.save(args.output.replace('.png', '_no_video.png'))
        print("Saved: %s" % args.output.replace('.png', '_no_video.png'))
        sys.exit(2)

    print("Video signal detected — decoding...")

    # FM demodulate (proper SDR approach)
    print("\nFM demodulating...")
    video = fm_demodulate(iq, sample_rate)

    # Filter out audio subcarrier
    print("Filtering audio subcarrier...")
    video = filter_audio_subcarrier(video, sample_rate, cutoff=3.0e6)

    # De-emphasis filter
    print("Applying de-emphasis filter...")
    video = deemphasis(video, sample_rate, tau=NTSC['deemph_tau'])

    # Detect or use specified standard
    if args.standard:
        tv_std = NTSC if args.standard == 'NTSC' else PAL
        print("Using %s standard" % tv_std['name'])
        h_sync, sync_level, black_level = find_sync_pulses(video, sample_rate, tv_std)
    else:
        print("Auto-detecting TV standard...")
        tv_std, h_sync_raw = detect_standard(video, sample_rate)
        if tv_std:
            print("Detected: %s" % tv_std['name'])
            h_sync, sync_level, black_level = find_sync_pulses(video, sample_rate, tv_std)
        else:
            print("Could not detect standard, trying NTSC...")
            tv_std = NTSC
            h_sync, sync_level, black_level = find_sync_pulses(video, sample_rate, tv_std)

    print("Found %d horizontal sync pulses" % len(h_sync))

    if len(h_sync) < 10:
        print("\nNot enough sync pulses found!")
        print("Trying AM demodulation instead...")
        video_am = am_demodulate(iq)
        h_sync, sync_level, black_level = find_sync_pulses(video_am, sample_rate, tv_std)
        print("AM demod: Found %d sync pulses" % len(h_sync))

        if len(h_sync) < 10:
            print("\nNo video signal found. Saving spectrogram...")
            spec_img = create_spectrogram(iq, sample_rate)
            spec_img.save(args.output.replace('.png', '_no_signal.png'))
            print("Saved: %s" % args.output.replace('.png', '_no_signal.png'))
            return
        else:
            video = video_am

    # Check sync spacing
    if len(h_sync) > 2:
        spacings = np.diff(h_sync)
        avg_spacing = np.mean(spacings)
        expected = tv_std['line_duration'] * sample_rate
        print("Sync spacing: %.1f samples (%.1f us, expected %.1f us)" %
              (avg_spacing, avg_spacing/sample_rate*1e6, expected/sample_rate*1e6))

    # DC restoration using back-porch clamping
    print("\nRestoring DC (back-porch clamping)...")
    video = restore_dc(video, h_sync, sample_rate, tv_std)

    # Extract frame
    print("Extracting frame...")
    frame = extract_frame(video, h_sync, sample_rate, tv_std)

    if frame.shape[0] < 100:
        print("WARNING: Only %d lines extracted (expected %d)" % (frame.shape[0], tv_std['active_lines']))

    # Normalize using IRE levels
    frame_norm = normalize_frame(frame, sync_level, black_level)
    img = Image.fromarray(frame_norm, mode='L')
    img.save(args.output)
    print("\nSaved: %s" % args.output)
    print("Resolution: %dx%d" % (frame_norm.shape[1], frame_norm.shape[0]))

    # Also save colorized version (green phosphor look)
    frame_rgb = np.zeros((frame_norm.shape[0], frame_norm.shape[1], 3), dtype=np.uint8)
    frame_rgb[:,:,0] = (frame_norm * 0.2).astype(np.uint8)
    frame_rgb[:,:,1] = frame_norm
    frame_rgb[:,:,2] = (frame_norm * 0.3).astype(np.uint8)
    color_path = args.output.replace('.png', '_green.png')
    Image.fromarray(frame_rgb, 'RGB').save(color_path)
    print("Saved green: %s" % color_path)


if __name__ == '__main__':
    main()
