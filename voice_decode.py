#!/usr/bin/env python3
"""
voice_decode.py — Analog and digital voice decoder for SDR signals.

Decodes voice signals from IQ captures using:
- FM demodulation (analog FM voice)
- AM demodulation (air band, military)
- DSD (dsdccx): DMR, D-STAR, DPMR, YSF, NXDN
- multimon-ng: POCSAG, FLEX, EAS, DTMF, Morse, AFSK

Usage:
  python3 voice_decode.py decode <freq_mhz> [--mode auto|fm|am|nfm|dmr|dstar|pocsag]
  python3 voice_decode.py scan <freq_mhz> [--duration 5]
  python3 voice_decode.py demod <raw_file> <freq_mhz> [--mode nfm]

Requires: hackrf_transfer, rtl_fm, dsdccx, multimon-ng, ffmpeg
"""

import subprocess
import sys
import os
import tempfile
import struct
import math

# Decode modes
MODES = {
    'nfm': {'desc': 'Narrow FM (PMR, taxi, business)', 'bw': 12500, 'decoder': 'fm'},
    'wfm': {'desc': 'Wide FM (broadcast)', 'bw': 200000, 'decoder': 'fm'},
    'am': {'desc': 'AM (air band, military)', 'bw': 6000, 'decoder': 'am'},
    'usb': {'desc': 'Upper Sideband (HF)', 'bw': 3000, 'decoder': 'ssb'},
    'lsb': {'desc': 'Lower Sideband (HF)', 'bw': 3000, 'decoder': 'ssb'},
    'dmr': {'desc': 'DMR/MOTOTRBO (digital)', 'bw': 12500, 'decoder': 'dsd'},
    'dstar': {'desc': 'D-STAR (ham digital)', 'bw': 6250, 'decoder': 'dsd'},
    'dpmr': {'desc': 'dPMR Tier 1/2 (digital)', 'bw': 6250, 'decoder': 'dsd'},
    'ysf': {'desc': 'Yaesu System Fusion', 'bw': 12500, 'decoder': 'dsd'},
    'nxdn': {'desc': 'NXDN/IDAS (digital)', 'bw': 6250, 'decoder': 'dsd'},
    'p25': {'desc': 'P25 Phase 1 (public safety)', 'bw': 12500, 'decoder': 'dsd'},
    'pocsag': {'desc': 'POCSAG (pager)', 'bw': 12500, 'decoder': 'multimon'},
    'flex': {'desc': 'FLEX (pager)', 'bw': 12500, 'decoder': 'multimon'},
    'eas': {'desc': 'Emergency Alert System', 'bw': 6000, 'decoder': 'multimon'},
    'dtmf': {'desc': 'DTMF (touch tones)', 'bw': 6000, 'decoder': 'multimon'},
    'morse': {'desc': 'Morse/CW', 'bw': 500, 'decoder': 'multimon'},
    'auto': {'desc': 'Auto-detect', 'bw': 12500, 'decoder': 'auto'},
}

# Frequency-to-mode hints
FREQ_HINTS = [
    (108, 137, 'am', 'Air band (AM voice)'),
    (137, 144, 'nfm', 'Military VHF'),
    (144, 148, 'nfm', '2m amateur'),
    (150, 174, 'nfm', 'VHF business/PMR'),
    (400, 470, 'nfm', 'UHF business/PMR'),
    (430, 440, 'nfm', '70cm amateur'),
    (440, 470, 'nfm', 'PMR/LMR'),
    (850, 960, 'nfm', 'Cellular (not decodable)'),
    (162, 163, 'nfm', 'Marine VHF'),
    (462, 468, 'nfm', 'FRS/GMRS'),
]


def guess_mode(freq_mhz):
    """Guess decode mode from frequency."""
    for lo, hi, mode, desc in FREQ_HINTS:
        if lo <= freq_mhz <= hi:
            return mode, desc
    # Check for digital voice bands
    if 440 <= freq_mhz <= 450:
        return 'dmr', 'Possible DMR/MOTOTRBO'
    if 144 <= freq_mhz <= 148:
        return 'dstar', 'Possible D-STAR'
    return 'auto', 'Unknown'


def capture_iq(freq_mhz, duration_s=3, sample_rate=2000000, output=None):
    """Capture IQ data using HackRF. Min sample rate: 2 MHz."""
    if output is None:
        output = tempfile.mktemp(suffix='.raw', prefix='voice_')
    freq_hz = int(freq_mhz * 1e6)
    n_samples = int(sample_rate * duration_s * 2)  # I + Q
    cmd = ["hackrf_transfer", "-r", output, "-f", str(freq_hz),
           "-s", str(sample_rate), "-n", str(n_samples),
           "-l", "32", "-g", "40", "-a", "1"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration_s + 10)
    if os.path.exists(output) and os.path.getsize(output) > 1000:
        return output
    return None


def iq_to_audio_wav(raw_file, sample_rate=48000, mode='nfm', output=None):
    """Convert IQ raw file to demodulated audio WAV."""
    if output is None:
        output = tempfile.mktemp(suffix='.wav', prefix='voice_demod_')

    import numpy as np
    data = np.fromfile(raw_file, dtype=np.int8)
    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
    iq /= 128.0

    if mode in ('nfm', 'wfm', 'auto'):
        # FM demodulation
        phase = np.unwrap(np.angle(iq))
        audio = np.diff(phase) * sample_rate / (2 * np.pi)
        # Normalize
        audio = audio / (np.max(np.abs(audio)) + 1e-10) * 0.9
        # For WFM, apply de-emphasis
        if mode == 'wfm':
            # Simple de-emphasis filter
            alpha = 1.0 / (1.0 + sample_rate * 75e-6)
            for i in range(1, len(audio)):
                audio[i] = audio[i] * (1 - alpha) + audio[i-1] * alpha
    elif mode in ('am', 'usb', 'lsb'):
        # AM demodulation
        audio = np.abs(iq)
        # Remove DC
        audio = audio - np.mean(audio)
        # Normalize
        audio = audio / (np.max(np.abs(audio)) + 1e-10) * 0.9
    else:
        # For digital modes, use FM demod as input to DSD
        phase = np.unwrap(np.angle(iq))
        audio = np.diff(phase) * sample_rate / (2 * np.pi)
        audio = audio / (np.max(np.abs(audio)) + 1e-10) * 0.9

    # Resample to 48kHz if needed
    if sample_rate != 48000:
        from scipy import signal as scipy_signal
        audio = scipy_signal.resample(audio, int(len(audio) * 48000 / sample_rate))

    # Write WAV
    import wave
    audio_16 = (audio * 32767).astype(np.int16)
    with wave.open(output, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(audio_16.tobytes())

    return output


def decode_dsd(wav_file, mode='auto'):
    """Decode digital voice using dsdccx."""
    mode_map = {
        'auto': ['-fa'],
        'dmr': ['-fr'],
        'dstar': ['-fd'],
        'dpmr': ['-fm'],
        'ysf': ['-fy'],
        'nxdn': ['-fi'],
        'p25': ['-f1'],
    }
    args = mode_map.get(mode, ['-fa'])
    cmd = ["dsdccx", "-i", wav_file, "-o", "/dev/null", "-q"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return "[DSD timeout]"
    except FileNotFoundError:
        return "[DSD not installed]"


def decode_multimon(wav_file, mode='auto'):
    """Decode using multimon-ng."""
    demod_map = {
        'auto': ['-a', 'POCSAG512', '-a', 'POCSAG1200', '-a', 'POCSAG2400',
                 '-a', 'FLEX', '-a', 'DTMF', '-a', 'MORSE_CW', '-a', 'EAS'],
        'pocsag': ['-a', 'POCSAG512', '-a', 'POCSAG1200', '-a', 'POCSAG2400'],
        'flex': ['-a', 'FLEX', '-a', 'FLEX_NEXT'],
        'eas': ['-a', 'EAS'],
        'dtmf': ['-a', 'DTMF'],
        'morse': ['-a', 'MORSE_CW'],
        'afsk': ['-a', 'AFSK1200', '-a', 'AFSK2400'],
    }
    args = demod_map.get(mode, demod_map['auto'])
    cmd = ["multimon-ng", "-t", "wav", "-q"] + args + [wav_file]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return "[multimon-ng timeout]"
    except FileNotFoundError:
        return "[multimon-ng not installed]"


def decode_signal(freq_mhz, mode='auto', duration_s=3, raw_file=None, sample_rate=48000):
    """Full decode pipeline: capture → demod → decode.

    Returns dict with decode results.
    """
    result = {
        'freq_mhz': freq_mhz,
        'mode': mode,
        'duration': duration_s,
        'audio_file': None,
        'decoded_text': '',
        'protocol': None,
        'confidence': 0,
    }

    # Guess mode if auto
    if mode == 'auto':
        mode, hint = guess_mode(freq_mhz)
        result['mode_hint'] = hint

    # Capture IQ if not provided
    if raw_file is None:
        raw_file = capture_iq(freq_mhz, duration_s, sample_rate)
        if raw_file is None:
            result['error'] = 'Capture failed'
            return result

    # Convert to audio WAV
    wav_file = iq_to_audio_wav(raw_file, sample_rate, mode)
    result['audio_file'] = wav_file

    # Check signal presence
    import numpy as np
    data = np.fromfile(raw_file, dtype=np.int8)
    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
    iq /= 128.0
    power_db = 20 * np.log10(np.mean(np.abs(iq)) + 1e-10)
    env = np.abs(iq)
    pmr = np.max(env) / (np.mean(env) + 1e-10)
    result['power_db'] = round(power_db, 1)
    result['peak_mean_ratio'] = round(pmr, 1)

    if power_db < -40:
        result['decoded_text'] = '[Signal too weak]'
        return result

    # Try DSD for digital modes
    dsd_modes = ['auto', 'dmr', 'dstar', 'dpmr', 'ysf', 'nxdn', 'p25']
    if mode in dsd_modes:
        dsd_out = decode_dsd(wav_file, mode)
        if dsd_out.strip() and '[DSD' not in dsd_out:
            result['decoded_text'] = dsd_out
            result['decoder'] = 'dsdccx'
            # Try to identify protocol
            for proto in ['DMR', 'D-STAR', 'DPMR', 'YSF', 'NXDN', 'P25']:
                if proto.lower() in dsd_out.lower():
                    result['protocol'] = proto
                    result['confidence'] = 0.8
                    break
            return result

    # Try multimon-ng for paging/data modes
    mm_modes = ['auto', 'pocsag', 'flex', 'eas', 'dtmf', 'morse', 'afsk']
    if mode in mm_modes:
        mm_out = decode_multimon(wav_file, mode)
        if mm_out.strip() and '[multimon' not in mm_out:
            result['decoded_text'] = mm_out
            result['decoder'] = 'multimon-ng'
            for proto in ['POCSAG', 'FLEX', 'EAS', 'DTMF', 'MORSE', 'AFSK']:
                if proto.lower() in mm_out.lower():
                    result['protocol'] = proto
                    result['confidence'] = 0.8
                    break
            return result

    # For analog modes, just provide the demodulated audio
    if mode in ('nfm', 'wfm', 'am', 'usb', 'lsb'):
        result['decoded_text'] = f'[Analog {mode.upper()} demodulated — audio saved to {wav_file}]'
        result['decoder'] = 'analog'
        result['protocol'] = mode.upper()
        result['confidence'] = 0.5
        return result

    result['decoded_text'] = '[No decode — unknown signal type]'
    return result


def scan_and_decode(freq_mhz, duration_s=5):
    """Capture and try all decode modes on a frequency."""
    print(f"  Capturing {freq_mhz} MHz for {duration_s}s...")
    raw_file = capture_iq(freq_mhz, duration_s)
    if raw_file is None:
        print(f"  ERROR: Capture failed")
        return None

    print(f"  Analyzing signal...")
    import numpy as np
    data = np.fromfile(raw_file, dtype=np.int8)
    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
    iq /= 128.0
    power = 20 * np.log10(np.mean(np.abs(iq)) + 1e-10)
    env = np.abs(iq)
    pmr = np.max(env) / (np.mean(env) + 1e-10)

    print(f"  Power: {power:.1f} dBFS | Peak/Mean: {pmr:.1f}x")

    # Guess mode
    mode, hint = guess_mode(freq_mhz)
    print(f"  Hint: {hint} (mode: {mode})")

    # Try decode
    result = decode_signal(freq_mhz, mode=mode, raw_file=raw_file)
    print(f"  Decoder: {result.get('decoder', 'none')}")
    print(f"  Protocol: {result.get('protocol', 'unknown')}")
    if result.get('decoded_text'):
        print(f"  Result: {result['decoded_text'][:200]}")
    if result.get('audio_file'):
        print(f"  Audio: {result['audio_file']}")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Voice decoder for SDR signals")
    parser.add_argument('command', choices=['decode', 'scan', 'demod'])
    parser.add_argument('freq_or_file', help='Frequency in MHz or raw file path')
    parser.add_argument('--mode', default='auto', choices=list(MODES.keys()))
    parser.add_argument('--duration', type=int, default=3)
    parser.add_argument('--output', '-o', help='Output WAV file')
    args = parser.parse_args()

    if args.command == 'scan':
        freq = float(args.freq_or_file)
        scan_and_decode(freq, args.duration)
    elif args.command == 'decode':
        freq = float(args.freq_or_file)
        result = decode_signal(freq, mode=args.mode, duration_s=args.duration)
        for k, v in result.items():
            if v is not None:
                print(f"  {k}: {v}")
    elif args.command == 'demod':
        raw_file = args.freq_or_file
        freq = float(sys.argv[3]) if len(sys.argv) > 3 else 0
        wav = iq_to_audio_wav(raw_file, mode=args.mode, output=args.output)
        print(f"  Demodulated audio: {wav}")


if __name__ == "__main__":
    main()
