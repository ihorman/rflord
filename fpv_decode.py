#!/usr/bin/env python3
"""
FPV Video Decoder — Decode analog FPV video from IQ captures.
Uses PySDR approach: FM demod → filter audio → resample to samples_per_line → reshape.

No sync detection needed — image may be shifted but always recognizable.
Proven approach from https://pysdr.org/content/fpv_video.html

Usage:
  python3 fpv_decode.py capture --freq 5800
  python3 fpv_decode.py capture.raw --freq 1280 --standard PAL
  python3 fpv_decode.py capture --freq 5800 --force
"""
import numpy as np
import scipy.signal as sig
from PIL import Image
import sys
import argparse
import os
import time

# NTSC constants
NTSC_SAMPLES_PER_LINE = 508
NTSC_LINES_PER_FRAME = 525
NTSC_REFRESH_HZ = 30.0 / 1.001  # 29.97
NTSC_LINE_HZ = NTSC_REFRESH_HZ * NTSC_LINES_PER_FRAME  # 15734.26

# PAL constants
PAL_SAMPLES_PER_LINE = 512
PAL_LINES_PER_FRAME = 625
PAL_REFRESH_HZ = 25.0
PAL_LINE_HZ = PAL_REFRESH_HZ * PAL_LINES_PER_FRAME  # 15625

# FPV CHANNEL PLANS
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
        return None

    raw = np.fromfile(raw_file, dtype=np.int8)
    iq = raw[::2].astype(np.float32)/128 + 1j*raw[1::2].astype(np.float32)/128

    print("Captured %d samples (%.1f ms)" % (len(iq), len(iq)/sample_rate*1000))

    # Save IQ for redecoding
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

    return iq


def load_iq_file(path, sample_rate=10000000):
    """Load IQ from file. Supports complex64 (GNU Radio/PySDR) and int8 (HackRF)."""
    file_size = os.path.getsize(path)

    # Try complex64 first (GNU Radio / PySDR format)
    if file_size >= 8 and file_size % 8 == 0:
        iq = np.fromfile(path, dtype=np.complex64)
        if len(iq) > 0 and np.max(np.abs(iq)) < 1e6:
            return iq

    # Fall back to int8 interleaved (HackRF format)
    raw = np.fromfile(path, dtype=np.int8)
    if len(raw) == 0:
        raw = np.fromfile(path, dtype=np.uint8)
        iq = (raw[::2].astype(np.float32) - 127.5)/127.5 + \
             1j*(raw[1::2].astype(np.float32) - 127.5)/127.5
    else:
        iq = raw[::2].astype(np.float32)/128 + 1j*raw[1::2].astype(np.float32)/128
    return iq


def has_video_signal(iq, sample_rate):
    """Check if IQ contains analog video by looking for line rate harmonics."""
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
            noise = np.median(psd_db[max(0, lo-50):lo]) if lo > 50 else np.median(psd_db)
            diffs.append(psd_db[idx] - noise)
        strong = sum(1 for p in diffs if p > 5)
        if strong >= 2:
            return True
    return False


def decode_frame(iq, sample_rate=10e6, standard='NTSC'):
    """Decode FPV video using PySDR approach.
    FM demod → filter audio → resample → reshape. No sync detection needed.

    Reference: https://pysdr.org/content/fpv_video.html
    """
    if standard == 'PAL':
        samples_per_line = PAL_SAMPLES_PER_LINE
        lines_per_frame = PAL_LINES_PER_FRAME
        line_hz = PAL_LINE_HZ
    else:
        samples_per_line = NTSC_SAMPLES_PER_LINE
        lines_per_frame = NTSC_LINES_PER_FRAME
        line_hz = NTSC_LINE_HZ

    # FM demodulation (complex conjugate — standard SDR)
    iq = iq - np.mean(iq)  # DC removal
    x_demod = np.angle(iq[1:] * np.conj(iq[:-1]))

    # Filter out audio subcarrier (3 MHz cutoff)
    h = sig.firwin(301, 3e6, fs=sample_rate)
    x_demod = np.convolve(x_demod, h, 'same')

    # Resample to exactly samples_per_line per horizontal line
    resampling_rate = samples_per_line / (sample_rate / line_hz)
    resampling_rate *= 1.00003  # drift correction (SDR clock offset)
    x_demod = sig.resample(x_demod, int(len(x_demod) * resampling_rate))

    # Trim to multiple of samples_per_line
    x_demod = x_demod[:len(x_demod) - (len(x_demod) % samples_per_line)]

    # Reshape into 2D image
    frame = x_demod.reshape(-1, samples_per_line)

    # Take first field (half frame)
    frame = frame[:lines_per_frame // 2]

    return frame


def normalize_frame(frame):
    """Normalize to 0-255 using percentile (robust against outliers)."""
    p2 = np.percentile(frame, 2)
    p98 = np.percentile(frame, 98)
    if p98 - p2 < 1e-10:
        return np.zeros_like(frame, dtype=np.uint8)
    f = (frame - p2) / (p98 - p2)
    return (np.clip(f, 0, 1) * 255).astype(np.uint8)


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

    # Get IQ data
    if args.input == 'capture' or args.input is None:
        if not args.freq:
            print("ERROR: --freq required for capture")
            return
        iq = capture_iq_hackrf(args.freq, args.duration, args.sample_rate)
        if iq is None:
            return
        sample_rate = args.sample_rate
    else:
        print("Loading IQ from %s..." % args.input)
        iq = load_iq_file(args.input, args.sample_rate)
        sample_rate = args.sample_rate
        print("Loaded %d samples (%.1f ms)" % (len(iq), len(iq)/sample_rate*1000))

    # Validate video signal (unless --force)
    if not args.force:
        print("Checking for video signal...")
        if not has_video_signal(iq, sample_rate):
            print("NO VIDEO SIGNAL DETECTED — saving spectrogram")
            spec = create_spectrogram(iq, sample_rate)
            spec.save(args.output.replace('.png', '_no_video.png'))
            print("Saved: %s" % args.output.replace('.png', '_no_video.png'))
            sys.exit(2)
        print("Video signal detected — decoding...")

    # Use subset for speed (first 500K samples = 50ms, ~15 frames)
    n = min(len(iq), 500000)
    iq_subset = iq[:n]

    # Decode
    print("Decoding %s..." % args.standard)
    frame = decode_frame(iq_subset, sample_rate, args.standard)
    frame_norm = normalize_frame(frame)

    # Save grayscale
    img = Image.fromarray(frame_norm, mode='L')
    img.save(args.output)
    print("Saved: %s (%dx%d)" % (args.output, frame_norm.shape[1], frame_norm.shape[0]))

    # Save green phosphor version
    rgb = np.zeros((frame_norm.shape[0], frame_norm.shape[1], 3), dtype=np.uint8)
    rgb[:,:,0] = (frame_norm * 0.2).astype(np.uint8)
    rgb[:,:,1] = frame_norm
    rgb[:,:,2] = (frame_norm * 0.3).astype(np.uint8)
    green_path = args.output.replace('.png', '_green.png')
    Image.fromarray(rgb, 'RGB').save(green_path)
    print("Saved: %s" % green_path)


if __name__ == '__main__':
    main()
