#!/usr/bin/env python3
"""
tv_capture.py — Capture screenshots from analog and digital TV signals
using HackRF One or RTL-SDR.

Supports:
  - Analog TV (NTSC/PAL) — common in spy cameras, FPV transmitters
  - Digital TV (DVB-T, ISDB-T) — broadcast digital television

Usage:
  # Analog TV — capture frame from 900 MHz analog camera
  python3 tv_capture.py analog --freq 910 --output frame.png

  # Analog TV — capture from 1.2 GHz FPV
  python3 tv_capture.py analog --freq 1280 --standard PAL --output fpv.png

  # Digital TV — capture frame from DVB-T broadcast
  python3 tv_capture.py digital --freq 578 --output dvbt_frame.png

  # Scan for active TV signals
  python3 tv_capture.py scan

  # Capture IQ raw data for later analysis
  python3 tv_capture.py raw --freq 910 --duration 5 --output capture.raw
"""

import subprocess
import sys
import os
import struct
import math
import tempfile
import argparse
from datetime import datetime

# Try imports
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def run_cmd(cmd, timeout=60):
    """Run a shell command and return stdout+stderr"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return b"", b"timeout", -1


def detect_device():
    """Detect available SDR device"""
    out, _, _ = run_cmd("lsusb")
    out_str = out.decode('utf-8', errors='ignore')
    if "1d50:6089" in out_str:
        return "hackrf"
    elif "0bda:2838" in out_str:
        return "rtlsdr"
    return None


def switch_portapack_to_hackrf():
    """Switch PortaPack Mayhem to HackRF mode (restore session first)"""
    out, _, _ = run_cmd("lsusb")
    out_str = out.decode('utf-8', errors='ignore')
    if "1d50:6089" in out_str:
        return True  # Already in HackRF mode
    if "1d50:6018" not in out_str:
        return False  # No PortaPack

    try:
        import serial, time
        for port in ["/dev/ttyACM0", "/dev/ttyACM1"]:
            try:
                s = serial.Serial(port, 115200, timeout=2)
                time.sleep(0.5)
                s.write(b'restore\r\n')
                time.sleep(1.5)
                s.read(s.in_waiting or 500)
                s.write(b'hackrf\r\n')
                time.sleep(3)
                s.read(s.in_waiting or 500)
                s.close()
                break
            except:
                continue
        out, _, _ = run_cmd("lsusb")
        return "1d50:6089" in out.decode('utf-8', errors='ignore')
    except ImportError:
        return False


def capture_iq_hackrf(freq_mhz, duration_s, sample_rate=2000000, gain=40):
    """Capture raw IQ samples using HackRF One"""
    print(f"  Capturing {duration_s}s of IQ data at {freq_mhz} MHz (SR={sample_rate/1e6}MHz)...")

    cmd = [
        "hackrf_transfer",
        "-r", "/dev/stdout",
        "-f", str(int(freq_mhz * 1e6)),
        "-s", str(sample_rate),
        "-n", str(int(sample_rate * duration_s) * 2),  # *2 for I+Q bytes
        "-l", "32",  # LNA gain
        "-g", str(gain),  # VGA gain
        "-a", "1",  # amp on
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw_data, err = proc.communicate(timeout=duration_s + 10)

    if len(raw_data) < 1024:
        print(f"  ERROR: Capture failed ({len(raw_data)} bytes)")
        return None, sample_rate

    # Convert signed 8-bit IQ to complex float
    samples = np.frombuffer(raw_data, dtype=np.int8)
    # Interleave I,Q pairs
    iq = samples[::2].astype(np.float32) + 1j * samples[1::2].astype(np.float32)
    iq /= 128.0  # Normalize to -1..+1

    print(f"  Captured {len(iq)} IQ samples ({len(raw_data)} bytes)")
    return iq, sample_rate


def capture_iq_rtlsdr(freq_mhz, duration_s, sample_rate=2000000):
    """Capture raw IQ samples using RTL-SDR"""
    print(f"  Capturing {duration_s}s of IQ data at {freq_mhz} MHz (SR={sample_rate/1e6}MHz)...")

    num_samples = int(sample_rate * duration_s)
    cmd = f"rtl_sdr -f {int(freq_mhz*1e6)} -s {sample_rate} -n {num_samples*2} -g 40 /dev/stdout"

    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw_data, err = proc.communicate(timeout=duration_s + 10)

    if len(raw_data) < 1024:
        print(f"  ERROR: Capture failed ({len(raw_data)} bytes)")
        return None, sample_rate

    # RTL-SDR outputs unsigned 8-bit IQ
    samples = np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32)
    samples = (samples - 127.5) / 127.5
    iq = samples[::2] + 1j * samples[1::2]

    print(f"  Captured {len(iq)} IQ samples ({len(raw_data)} bytes)")
    return iq, sample_rate


def capture_iq(freq_mhz, duration_s, device=None, sample_rate=2000000):
    """Capture IQ data using available device"""
    if device is None:
        device = detect_device()
    if device is None:
        print("ERROR: No SDR device found")
        return None, sample_rate

    if device == "hackrf":
        switch_portapack_to_hackrf()
        return capture_iq_hackrf(freq_mhz, duration_s, sample_rate)
    elif device == "rtlsdr":
        return capture_iq_rtlsdr(freq_mhz, duration_s, sample_rate)
    return None, sample_rate


# ═══════════════════════════════════════════════════════════
#  ANALOG TV DECODER (NTSC/PAL)
# ═══════════════════════════════════════════════════════════

# NTSC: 525 lines, 29.97 fps, 4.2 MHz video bandwidth
# PAL:  625 lines, 25 fps, 5.0 MHz video bandwidth
NTSC = {
    'name': 'NTSC',
    'total_lines': 525,
    'active_lines': 480,
    'fps': 29.97,
    'line_duration': 63.555e-6,  # seconds
    'h_sync_width': 4.7e-6,
    'v_sync_lines': 9,
    'video_bw': 4.2e6,
    'color_sub': 3.579545e6,
}

PAL = {
    'name': 'PAL',
    'total_lines': 625,
    'active_lines': 576,
    'fps': 25.0,
    'line_duration': 64.0e-6,
    'h_sync_width': 4.7e-6,
    'v_sync_lines': 9,
    'video_bw': 5.0e6,
    'color_sub': 4.43361875e6,
}


def fm_demodulate(iq, sample_rate):
    """FM demodulate IQ signal to get video signal"""
    # Instantaneous frequency
    phase = np.angle(iq)
    # Unwrap phase
    unwrapped = np.unwrap(phase)
    # Differentiate to get frequency (video signal)
    video = np.diff(unwrapped) * sample_rate / (2 * np.pi)
    return video


def am_demodulate(iq):
    """AM demodulate — extract envelope (for AM video signals)"""
    return np.abs(iq)


def find_sync_pulses(video, sample_rate, tv_std):
    """Find horizontal sync pulses to align scanlines"""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    h_sync_samples = int(tv_std['h_sync_width'] * sample_rate)

    # Low-pass filter the video signal (simple moving average)
    kernel_size = max(3, int(sample_rate / tv_std['video_bw'] / 2))
    kernel = np.ones(kernel_size) / kernel_size
    filtered = np.convolve(video, kernel, mode='same')

    # Find sync tips (negative peaks in filtered signal)
    threshold = np.percentile(filtered, 5)  # Bottom 5%
    sync_positions = []

    i = 0
    while i < len(filtered):
        if filtered[i] < threshold:
            # Find the start of this sync pulse
            sync_start = i
            while i < len(filtered) and filtered[i] < threshold:
                i += 1
            # Check if pulse width is reasonable
            pulse_width = i - sync_start
            if h_sync_samples * 0.5 < pulse_width < h_sync_samples * 3:
                sync_positions.append(sync_start)
        else:
            i += 1

    return sync_positions


def detect_vertical_sync(sync_positions, sample_rate, tv_std):
    """Detect vertical sync (missing pulses indicate VSYNC)"""
    line_samples = int(tv_std['line_duration'] * sample_rate)
    vsync_positions = []

    if len(sync_positions) < 2:
        return vsync_positions

    for i in range(1, len(sync_positions)):
        gap = sync_positions[i] - sync_positions[i-1]
        # VSync has wider gaps (multiple missing HSync pulses)
        if gap > line_samples * 2.5:
            vsync_positions.append(sync_positions[i-1])

    return vsync_positions


def decode_analog_frame(iq, sample_rate, tv_std, modulation='fm'):
    """Decode one frame of analog TV from IQ data"""
    print(f"  Decoding {tv_std['name']} {modulation.upper()} video...")

    # Demodulate video
    if modulation == 'fm':
        video = fm_demodulate(iq, sample_rate)
    else:
        video = am_demodulate(iq)

    # Normalize video signal
    video = video - np.mean(video)
    video_std = np.std(video)
    if video_std > 0:
        video = video / video_std

    # Find sync pulses
    sync_pos = find_sync_pulses(video, sample_rate, tv_std)
    print(f"  Found {len(sync_pos)} horizontal sync pulses")

    if len(sync_pos) < 10:
        print("  WARNING: Too few sync pulses — signal may be too weak or wrong frequency")
        # Try to create a spectrogram instead
        return create_spectrogram(iq, sample_rate)

    # Detect VSync
    vsync_pos = detect_vertical_sync(sync_pos, sample_rate, tv_std)
    print(f"  Found {len(vsync_pos)} vertical sync markers")

    # Extract one frame
    line_samples = int(tv_std['line_duration'] * sample_rate)
    active_lines = tv_std['active_lines']

    # Start from first VSync (or first available sync)
    if vsync_pos:
        frame_start = vsync_pos[0]
    else:
        frame_start = sync_pos[0]

    # Sample each scanline
    pixels_per_line = 640  # Standard width
    frame = np.zeros((active_lines, pixels_per_line), dtype=np.float32)

    line_idx = 0
    sync_idx = 0

    # Find the sync index closest to frame_start
    for i, sp in enumerate(sync_pos):
        if sp >= frame_start:
            sync_idx = i
            break

    for line in range(active_lines):
        if sync_idx + line >= len(sync_pos):
            break

        line_start = sync_pos[sync_idx + line]
        # Skip sync pulse + back porch (about 10us)
        back_porch = int(10e-6 * sample_rate)
        active_start = line_start + back_porch
        active_end = active_start + int(tv_std['line_duration'] * 0.8 * sample_rate)

        if active_end > len(video):
            break

        # Extract active line content
        line_data = video[active_start:active_end]

        if len(line_data) > 0:
            # Resample to fixed pixel width
            indices = np.linspace(0, len(line_data)-1, pixels_per_line)
            frame[line_idx] = np.interp(indices, np.arange(len(line_data)), line_data)
            line_idx += 1

    # Trim to actual lines decoded
    frame = frame[:line_idx]

    # Normalize to 0-255
    frame_min = np.min(frame)
    frame_max = np.max(frame)
    if frame_max > frame_min:
        frame = (frame - frame_min) / (frame_max - frame_min) * 255
    frame = np.clip(frame, 0, 255).astype(np.uint8)

    return frame


def create_spectrogram(iq, sample_rate, width=640, height=480):
    """Create a spectrogram image from IQ data (fallback when no sync found)"""
    print("  Creating spectrogram fallback...")

    # Split into FFT windows
    window_size = 1024
    num_windows = min(len(iq) // window_size, height)

    if num_windows < 10:
        print("  ERROR: Not enough data for spectrogram")
        return None

    spectrogram = np.zeros((num_windows, window_size), dtype=np.float32)

    for i in range(num_windows):
        chunk = iq[i*window_size:(i+1)*window_size]
        # Apply Hanning window
        windowed = chunk * np.hanning(window_size)
        fft_data = np.fft.fftshift(np.fft.fft(windowed))
        power = 20 * np.log10(np.abs(fft_data) + 1e-10)
        spectrogram[i] = power

    # Resize to target dimensions
    spectrogram = spectrogram - np.min(spectrogram)
    max_val = np.max(spectrogram)
    if max_val > 0:
        spectrogram = spectrogram / max_val * 255

    # Resize with PIL
    if HAS_PIL:
        img = Image.fromarray(spectrogram.astype(np.uint8), mode='L')
        img = img.resize((width, height), Image.BILINEAR)
        return np.array(img)

    return spectrogram.astype(np.uint8)


# ═══════════════════════════════════════════════════════════
#  DIGITAL TV DECODER (DVB-T / ISDB-T)
# ═══════════════════════════════════════════════════════════

def capture_digital_ts(freq_mhz, duration_s, device=None, output_ts=None):
    """Capture MPEG Transport Stream from digital TV signal"""
    if output_ts is None:
        output_ts = tempfile.mktemp(suffix='.ts')

    print(f"  Capturing {duration_s}s of DVB-T/ISDB-T at {freq_mhz} MHz...")

    if device is None:
        device = detect_device()

    if device == "rtlsdr":
        # Use rtl_sdr + tsdemux approach
        # First capture raw IQ
        raw_file = tempfile.mktemp(suffix='.raw')
        cmd = f"rtl_sdr -f {int(freq_mhz*1e6)} -s 2000000 -n {2000000*duration_s*2} -g 40 {raw_file}"
        out, err, rc = run_cmd(cmd, timeout=duration_s + 15)

        if rc != 0:
            print(f"  ERROR: Capture failed")
            return None

        print(f"  Captured raw IQ to {raw_file}")
        print(f"  Processing with GNU Radio DTV pipeline...")

        # Use a GNURadio flowgraph approach via command line
        # This requires gr-dtv which we have installed
        return decode_dtv_gr(raw_file, freq_mhz, output_ts)

    elif device == "hackrf":
        switch_portapack_to_hackrf()
        # Capture with hackrf_transfer
        raw_file = tempfile.mktemp(suffix='.raw')
        num_bytes = int(2000000 * duration_s * 2)
        cmd = f"hackrf_transfer -r {raw_file} -f {int(freq_mhz*1e6)} -s 2000000 -n {num_bytes} -l 32 -g 40 -a 1"
        out, err, rc = run_cmd(cmd, timeout=duration_s + 15)

        if rc != 0:
            print(f"  ERROR: Capture failed")
            return None

        print(f"  Captured raw IQ to {raw_file}")
        return decode_dtv_gr(raw_file, freq_mhz, output_ts)

    return None


def decode_dtv_gr(raw_file, freq_mhz, output_ts):
    """Decode DTV using GNU Radio flowgraph"""
    # Create a GNU Radio Python script for DVB-T demodulation
    gr_script = tempfile.mktemp(suffix='.py')

    script_content = f'''#!/usr/bin/env python3
"""GNU Radio DVB-T/ISDB-T decoder - auto-generated"""
import sys
import numpy as np

# Read raw IQ (signed 8-bit for hackrf, unsigned 8-bit for rtlsdr)
try:
    raw = np.fromfile("{raw_file}", dtype=np.int8)
    iq = raw[::2].astype(np.float32)/128 + 1j * raw[1::2].astype(np.float32)/128
except:
    raw = np.fromfile("{raw_file}", dtype=np.uint8).astype(np.float32)
    iq = (raw[::2]-127.5)/127.5 + 1j*(raw[1::2]-127.5)/127.5

print(f"Loaded {{len(iq)}} IQ samples")

# Try to find OFDM symbol structure
# DVB-T: 2K mode = 2048 subcarriers, 8K mode = 8192
# ISDB-T: same OFDM structure

# Estimate bandwidth and center offset
fft_size = 2048
num_symbols = min(len(iq) // fft_size, 1000)

if num_symbols < 10:
    print("ERROR: Not enough data")
    sys.exit(1)

# Create waterfall spectrogram
print(f"Creating waterfall spectrogram from {{num_symbols}} symbols...")
spectrogram = np.zeros((num_symbols, fft_size), dtype=np.float32)

for i in range(num_symbols):
    chunk = iq[i*fft_size:(i+1)*fft_size]
    windowed = chunk * np.hanning(fft_size)
    fft_data = np.fft.fftshift(np.fft.fft(windowed))
    power = 20*np.log10(np.abs(fft_data) + 1e-10)
    spectrogram[i] = power

# Save as image
from PIL import Image
spec_norm = spectrogram - np.min(spectrogram)
maxv = np.max(spec_norm)
if maxv > 0:
    spec_norm = spec_norm / maxv * 255
spec_norm = np.clip(spec_norm, 0, 255).astype(np.uint8)

# Resize to standard TV resolution
img = Image.fromarray(spec_norm, mode='L')
img = img.resize((720, 576), Image.BILINEAR)

# Apply colormap (green/amber for TV feel)
colored = np.zeros((576, 720, 3), dtype=np.uint8)
pixels = np.array(img)
colored[:,:,0] = (pixels * 0.3).astype(np.uint8)  # R
colored[:,:,1] = pixels.astype(np.uint8)           # G
colored[:,:,2] = (pixels * 0.5).astype(np.uint8)   # B

img_color = Image.fromarray(colored, mode='RGB')
img_color.save("{output_ts.replace('.ts', '_spectrum.png')}")
print(f"Saved spectrum image to {output_ts.replace('.ts', '_spectrum.png')}")

# Also try to extract any visible video frames via autocorrelation
# Look for repeating frame patterns
autocorr = np.correlate(np.abs(iq[:10000]), np.abs(iq[:10000]), mode='full')
autocorr = autocorr[len(autocorr)//2:]
# Find peaks (frame boundaries)
from scipy.signal import find_peaks
peaks, _ = find_peaks(autocorr, distance=fft_size*10, prominence=np.std(autocorr)*2)
if len(peaks) > 2:
    frame_period = np.mean(np.diff(peaks))
    print(f"Detected frame period: {{frame_period}} samples ({{frame_period/2e6*1000:.1f}} ms)")
    # Extract one frame
    frame_len = int(frame_period)
    if frame_len > 0 and frame_len < len(iq):
        frame_data = iq[:frame_len]
        # Create image from frame
        frame_size = int(np.sqrt(frame_len))
        if frame_size > 0:
            pixels = np.abs(frame_data[:frame_size*frame_size]).reshape(frame_size, frame_size)
            pixels = pixels / np.max(pixels) * 255
            img = Image.fromarray(pixels.astype(np.uint8), mode='L')
            img = img.resize((720, 576), Image.BILINEAR)
            img.save("{output_ts.replace('.ts', '_frame.png')}")
            print(f"Saved extracted frame to {output_ts.replace('.ts', '_frame.png')}")
'''

    with open(gr_script, 'w') as f:
        f.write(script_content)

    # Run the decoder
    out, err, rc = run_cmd(f"python3 {gr_script}", timeout=60)
    print(out.decode('utf-8', errors='ignore'))
    if err:
        print(err.decode('utf-8', errors='ignore'))

    # Clean up
    try:
        os.unlink(gr_script)
        os.unlink(raw_file)
    except:
        pass

    spectrum_png = output_ts.replace('.ts', '_spectrum.png')
    frame_png = output_ts.replace('.ts', '_frame.png')

    results = {}
    if os.path.exists(spectrum_png):
        results['spectrum'] = spectrum_png
    if os.path.exists(frame_png):
        results['frame'] = frame_png

    return results if results else None


# ═══════════════════════════════════════════════════════════
#  SCAN FOR TV SIGNALS
# ═══════════════════════════════════════════════════════════

def scan_for_tv_signals(device=None):
    """Scan common TV signal frequencies and report active ones"""
    print("\n  Scanning for active TV signals...")
    print("  Using wideband sweep to find TV-band signals...\n")

    if device is None:
        device = detect_device()

    active_signals = []

    if device == "hackrf":
        # Wide sweep over TV bands in one pass
        bands = [
            (470, 790, "DVB-T/ISDB-T broadcast"),
            (900, 960, "900 MHz analog cameras"),
            (1080, 1300, "1.2 GHz FPV/cameras"),
            (2400, 2500, "2.4 GHz analog video TX"),
            (5725, 5875, "5.8 GHz FPV/cameras"),
        ]
        for f_lo, f_hi, label in bands:
            cmd = f"hackrf_sweep -f {f_lo}:{f_hi} -w 1000000 -l 32 -g 40 -a 1 -N 2 2>/dev/null | grep '^[0-9]'"
            out, _, _ = run_cmd(cmd, timeout=20)
            if out:
                for line in out.decode('utf-8', errors='ignore').strip().split('\n'):
                    parts = line.split(', ')
                    if len(parts) >= 7:
                        try:
                            freq_lo = int(parts[2])
                            freq_hi = int(parts[3])
                            db_vals = [float(p) for p in parts[6:] if p.strip()]
                            if db_vals:
                                peak = max(db_vals)
                                center = (freq_lo + freq_hi) / 2 / 1e6
                                if peak > -20:
                                    band_name = get_tv_band(center)
                                    print(f"  ACTIVE {label}: {center:.1f} MHz ({peak:+.1f} dBFS) [{band_name}]")
                                    active_signals.append({'freq': center, 'category': label, 'power': peak})
                        except:
                            pass
    else:
        # RTL-SDR: single wide sweep
        cmd = "rtl_power -f 470M:960M:2M -e 8s -i 2 - 2>/dev/null | grep '^20'"
        out, _, _ = run_cmd(cmd, timeout=30)
        if out:
            for line in out.decode('utf-8', errors='ignore').strip().split('\n'):
                parts = line.split(', ')
                if len(parts) >= 7:
                    try:
                        freq_lo = int(parts[2])
                        freq_hi = int(parts[3])
                        db_vals = [float(p) for p in parts[6:] if p.strip()]
                        if db_vals:
                            peak = max(db_vals)
                            center = (freq_lo + freq_hi) / 2 / 1e6
                            if peak > -10:
                                band_name = get_tv_band(center)
                                print(f"  ACTIVE: {center:.1f} MHz ({peak:+.1f} dB) [{band_name}]")
                                active_signals.append({'freq': center, 'category': band_name, 'power': peak})
                    except:
                        pass

    if not active_signals:
        print("  No active TV signals detected in common bands.")
        print("  Try: python3 tv_capture.py analog --freq <MHz>")
    else:
        print(f"\n  Found {len(active_signals)} active TV signal(s)")
        print("  To capture a frame, run:")
        print(f"    python3 tv_capture.py analog --freq {active_signals[0]['freq']:.0f}")

    return active_signals


def get_tv_band(freq_mhz):
    """Identify what a frequency might be"""
    if 470 <= freq_mhz <= 790:
        return "DVB-T/ISDB-T"
    if 900 <= freq_mhz <= 960:
        return "900 MHz analog cam"
    if 1080 <= freq_mhz <= 1300:
        return "1.2 GHz FPV/cam"
    if 2400 <= freq_mhz <= 2500:
        return "2.4 GHz video TX"
    if 5725 <= freq_mhz <= 5875:
        return "5.8 GHz FPV"
    return "Unknown"


# ═══════════════════════════════════════════════════════════
#  RAW IQ CAPTURE
# ═══════════════════════════════════════════════════════════

def capture_raw(freq_mhz, duration_s, output_file, device=None, sample_rate=2000000):
    """Capture raw IQ data to file"""
    iq, sr = capture_iq(freq_mhz, duration_s, device, sample_rate)
    if iq is None:
        return False

    # Save as raw complex float32
    iq.tofile(output_file)
    print(f"  Saved {len(iq)} IQ samples to {output_file}")
    print(f"  File size: {os.path.getsize(output_file)} bytes")

    # Also create a spectrogram
    spec_file = output_file.replace('.raw', '_spectrogram.png')
    if HAS_PIL:
        spec = create_spectrogram(iq, sr)
        if spec is not None:
            img = Image.fromarray(spec, mode='L')
            img.save(spec_file)
            print(f"  Saved spectrogram to {spec_file}")

    return True


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Capture screenshots from analog and digital TV signals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s analog --freq 910                    # Capture analog camera at 910 MHz
  %(prog)s analog --freq 1280 --standard PAL    # PAL FPV at 1.2 GHz
  %(prog)s digital --freq 578                   # DVB-T broadcast at 578 MHz
  %(prog)s scan                                  # Scan for active TV signals
  %(prog)s raw --freq 910 --duration 5          # Raw IQ capture
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command')

    # Analog TV
    analog = subparsers.add_parser('analog', help='Capture analog TV frame')
    analog.add_argument('--freq', type=float, required=True, help='Frequency in MHz')
    analog.add_argument('--standard', choices=['NTSC', 'PAL'], default='NTSC', help='TV standard (default: NTSC)')
    analog.add_argument('--modulation', choices=['fm', 'am'], default='fm', help='Video modulation (default: fm)')
    analog.add_argument('--duration', type=float, default=2.0, help='Capture duration in seconds (default: 2)')
    analog.add_argument('--output', '-o', default='tv_frame.png', help='Output image file')
    analog.add_argument('--device', choices=['hackrf', 'rtlsdr'], help='Force device')

    # Digital TV
    digital = subparsers.add_parser('digital', help='Capture digital TV frame')
    digital.add_argument('--freq', type=float, required=True, help='Frequency in MHz')
    digital.add_argument('--duration', type=float, default=3.0, help='Capture duration in seconds')
    digital.add_argument('--output', '-o', default='dvbt_capture', help='Output file prefix')
    digital.add_argument('--device', choices=['hackrf', 'rtlsdr'], help='Force device')

    # Scan
    scan = subparsers.add_parser('scan', help='Scan for active TV signals')
    scan.add_argument('--device', choices=['hackrf', 'rtlsdr'], help='Force device')

    # Raw capture
    raw = subparsers.add_parser('raw', help='Capture raw IQ data')
    raw.add_argument('--freq', type=float, required=True, help='Frequency in MHz')
    raw.add_argument('--duration', type=float, default=5.0, help='Capture duration in seconds')
    raw.add_argument('--output', '-o', default='capture.raw', help='Output raw file')
    raw.add_argument('--sample-rate', type=int, default=2000000, help='Sample rate (default: 2M)')
    raw.add_argument('--device', choices=['hackrf', 'rtlsdr'], help='Force device')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    print(f"\n{'='*60}")
    print(f"  TV CAPTURE — Analog/Digital TV Frame Extractor")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if args.command == 'scan':
        scan_for_tv_signals(args.device)

    elif args.command == 'analog':
        tv_std = NTSC if args.standard == 'NTSC' else PAL
        print(f"  Target: {args.freq} MHz, {tv_std['name']} {args.modulation.upper()}")

        iq, sr = capture_iq(args.freq, args.duration, args.device)
        if iq is None:
            print("ERROR: Failed to capture IQ data")
            sys.exit(1)

        frame = decode_analog_frame(iq, sr, tv_std, args.modulation)

        if frame is not None and HAS_PIL:
            img = Image.fromarray(frame, mode='L')
            img.save(args.output)
            print(f"\n  Saved frame to {args.output}")
            print(f"  Resolution: {frame.shape[1]}x{frame.shape[0]} pixels")
        else:
            print("\n  ERROR: Could not decode frame")

    elif args.command == 'digital':
        print(f"  Target: {args.freq} MHz digital TV")
        results = capture_digital_ts(args.freq, args.duration, args.device, args.output + '.ts')

        if results:
            print(f"\n  Results:")
            for key, path in results.items():
                print(f"    {key}: {path}")
        else:
            print("\n  No decodable frames found. Try:")
            print(f"    python3 tv_capture.py raw --freq {args.freq} --duration 5")
            print(f"    Then analyze with URH or inspectrum")

    elif args.command == 'raw':
        print(f"  Target: {args.freq} MHz, {args.duration}s, SR={args.sample_rate/1e6}MHz")
        capture_raw(args.freq, args.duration, args.output, args.device, args.sample_rate)


if __name__ == "__main__":
    main()
