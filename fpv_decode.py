#!/usr/bin/env python3
"""
FPV Video Decoder — Decode analog FPV video from IQ captures.
Supports NTSC and PAL standards on 900 MHz, 1.2 GHz, 2.4 GHz, 5.8 GHz bands.

Usage:
  python3 fpv_decode.py capture.raw --freq 5800 --standard NTSC
  python3 fpv_decode.py capture.raw --freq 1280 --standard PAL
  python3 fpv_decode.py capture.raw --auto  # Auto-detect standard
"""
import numpy as np
from PIL import Image
from scipy.signal import butter, filtfilt, find_peaks
import sys
import argparse
import os

# ============================================================
#  TV STANDARD DEFINITIONS
# ============================================================

NTSC = {
    'name': 'NTSC',
    'total_lines': 525,
    'active_lines': 480,
    'fps': 29.97,
    'line_duration': 63.555e-6,  # seconds
    'h_sync_width': 4.7e-6,
    'v_sync_pulses': 6,  # Equalizing + serration pulses
    'color_subcarrier': 3.579545e6,  # Hz
    'video_bw': 4.2e6,  # Video bandwidth
    'fm_deviation': 4.0e6,  # FM deviation for video
    'audio_offset': 4.5e6,  # Audio carrier offset from video
}

PAL = {
    'name': 'PAL',
    'total_lines': 625,
    'active_lines': 576,
    'fps': 25.0,
    'line_duration': 64.0e-6,
    'h_sync_width': 4.7e-6,
    'v_sync_pulses': 6,
    'color_subcarrier': 4.43361875e6,
    'video_bw': 5.0e6,
    'fm_deviation': 5.0e6,
    'audio_offset': 5.5e6,
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
    return iq, sample_rate

def load_iq_file(path, sample_rate=10000000):
    """Load IQ from file"""
    raw = np.fromfile(path, dtype=np.int8)
    if len(raw) == 0:
        # Try unsigned8-bit (RTL-SDR format)
        raw = np.fromfile(path, dtype=np.uint8)
        iq = (raw[::2].astype(np.float32) - 127.5)/127.5 + \
             1j*(raw[1::2].astype(np.float32) - 127.5)/127.5
    else:
        iq = raw[::2].astype(np.float32)/128 + 1j*raw[1::2].astype(np.float32)/128
    return iq

# ============================================================
#  FM DEMODULATION
# ============================================================

def fm_demodulate(iq, sample_rate, max_deviation=4.0e6):
    """FM demodulate IQ signal to baseband video"""
    # Instantaneous frequency
    phase = np.angle(iq)
    unwrapped = np.unwrap(phase)
    video = np.diff(unwrapped) * sample_rate / (2 * np.pi)
    
    # Normalize to expected deviation
    video = video / max_deviation
    
    return video

def am_demodulate(iq):
    """AM demodulate — extract envelope"""
    return np.abs(iq)

# ============================================================
#  SYNC DETECTION
# ============================================================

def find_h_sync(video, sample_rate, tv_std):
    """Find horizontal sync pulses"""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    sync_samples = int(tv_std['h_sync_width'] * sample_rate)
    
    # Low-pass filter to clean up video
    nyquist = sample_rate / 2
    cutoff = tv_std['video_bw'] / nyquist
    if cutoff >= 1.0:
        cutoff = 0.99
    b, a = butter(4, cutoff)
    filtered = filtfilt(b, a, video)
    
    # Find sync tips (negative peaks)
    # Sync pulses are the most negative part of the video signal
    threshold = np.percentile(filtered, 3)  # Bottom3%
    
    # Find negative peaks
    inv_video = -filtered
    peaks, props = find_peaks(inv_video, height=-threshold,
                               distance=int(sync_samples*0.5),
                               prominence=np.std(filtered)*0.5)
    
    return peaks, filtered

def find_v_sync(h_sync_positions, sample_rate, tv_std):
    """Find vertical sync (wider gaps between H-sync pulses)"""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    v_sync = []
    
    if len(h_sync_positions) < 2:
        return v_sync
    
    gaps = np.diff(h_sync_positions)
    
    # VSync has gaps that are2x or more of normal line spacing
    for i, gap in enumerate(gaps):
        if gap > line_samples * 1.8:
            v_sync.append(h_sync_positions[i])
    
    return v_sync

# ============================================================
#  FRAME EXTRACTION
# ============================================================

def extract_frame(video, h_sync_positions, sample_rate, tv_std):
    """Extract one video frame from demodulated signal"""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    active_lines = tv_std['active_lines']
    pixels_per_line = 720  # Standard width
    
    # Find first complete frame (after VSync)
    vsync = find_v_sync(h_sync_positions, sample_rate, tv_std)
    
    if vsync:
        frame_start = vsync[0]
        # Skip vertical blanking (about25 lines)
        vblank_lines = 25
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
        
        # Skip sync pulse + back porch (about10 μs)
        back_porch = int(10e-6 * sample_rate)
        active_start = line_start + back_porch
        
        # Active line duration (about52 μs)
        active_duration = int(52e-6 * sample_rate)
        active_end = active_start + active_duration
        
        if active_end > len(video):
            break
        
        # Extract and resample to pixel width
        line_data = video[active_start:active_end]
        if len(line_data) > 0:
            indices = np.linspace(0, len(line_data)-1, pixels_per_line)
            frame[lines_extracted] = np.interp(indices, np.arange(len(line_data)), line_data)
            lines_extracted += 1
    
    # Trim to actual lines
    frame = frame[:lines_extracted]
    
    return frame

def normalize_frame(frame):
    """Normalize frame to0-255 uint8"""
    frame = frame - np.min(frame)
    max_val = np.max(frame)
    if max_val > 0:
        frame = frame / max_val * 255
    return np.clip(frame, 0, 255).astype(np.uint8)

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
    """Auto-detect NTSC or PAL from video signal"""
    # Find sync pulses with both standards
    ntsc_line = int(NTSC['line_duration'] * sample_rate)
    pal_line = int(PAL['line_duration'] * sample_rate)
    
    # Low-pass filter
    nyquist = sample_rate / 2
    b, a = butter(4, min(0.99, NTSC['video_bw']/nyquist))
    filtered = filtfilt(b, a, video)
    
    # Find negative peaks
    inv = -filtered
    peaks, _ = find_peaks(inv, distance=int(min(ntsc_line, pal_line)*0.5),
                           prominence=np.std(filtered)*0.5)
    
    if len(peaks) < 10:
        return None, peaks
    
    # Check spacing
    spacings = np.diff(peaks)
    avg_spacing = np.mean(spacings)
    
    ntsc_match = abs(avg_spacing - ntsc_line) / ntsc_line
    pal_match = abs(avg_spacing - pal_line) / pal_line
    
    if ntsc_match < pal_match and ntsc_match < 0.1:
        return NTSC, peaks
    elif pal_match < 0.1:
        return PAL, peaks
    else:
        # Return the closer one
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
    
    # FM demodulate
    print("\nFM demodulating...")
    video = fm_demodulate(iq, sample_rate, max_deviation=4.0e6)
    
    # Detect or use specified standard
    if args.standard:
        tv_std = NTSC if args.standard == 'NTSC' else PAL
        print("Using %s standard" % tv_std['name'])
        # Find sync pulses
        h_sync, filtered = find_h_sync(video, sample_rate, tv_std)
    else:
        print("Auto-detecting TV standard...")
        tv_std, h_sync = detect_standard(video, sample_rate)
        if tv_std:
            print("Detected: %s" % tv_std['name'])
        else:
            print("Could not detect standard, trying NTSC...")
            tv_std = NTSC
            h_sync, filtered = find_h_sync(video, sample_rate, tv_std)
    
    print("Found %d horizontal sync pulses" % len(h_sync))
    
    if len(h_sync) < 10:
        print("\nNot enough sync pulses found!")
        print("Trying AM demodulation instead...")
        video_am = am_demodulate(iq)
        h_sync, filtered = find_h_sync(video_am, sample_rate, tv_std)
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
        print("Sync spacing: %.1f samples (%.1f μs, expected %.1f μs)" % 
              (avg_spacing, avg_spacing/sample_rate*1e6, expected/sample_rate*1e6))
    
    # Extract frame
    print("\nExtracting frame...")
    frame = extract_frame(video, h_sync, sample_rate, tv_std)
    
    if frame.shape[0] < 100:
        print("WARNING: Only %d lines extracted (expected %d)" % (frame.shape[0], tv_std['active_lines']))
    
    # Normalize and save
    frame_norm = normalize_frame(frame)
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
