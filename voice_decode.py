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
        output = tempfile.NamedTemporaryFile(suffix='.raw', prefix='voice_', delete=False).name
    freq_hz = int(freq_mhz * 1e6)
    n_samples = int(sample_rate * duration_s * 2)  # I + Q
    cmd = ["hackrf_transfer", "-r", output, "-f", str(freq_hz),
           "-s", str(sample_rate), "-n", str(n_samples),
           "-l", "32", "-g", "40", "-a", "1"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration_s + 10)
    if os.path.exists(output) and os.path.getsize(output) > 1000:
        return output
    return None


def suppress_stationary(audio, fs):
    """Subtract stationary tones (heterodyne whistle, hiss) from voice.

    Whistles are constant-frequency lines that survive any bandpass;
    spectral subtraction with the quietest frames as the noise profile
    removes them while speech (non-stationary) is preserved."""
    try:
        import numpy as np
        from scipy import signal as scipy_signal
        nper = 1024
        if len(audio) < nper * 4:
            return audio
        f_stft, t_stft, Z = scipy_signal.stft(audio, fs=fs, nperseg=nper, noverlap=nper // 2)
        mag = np.abs(Z)
        # noise profile: per-bin MINIMUM over time (stationary parts only)
        noise = np.percentile(mag, 10, axis=1, keepdims=True)
        # Gentle subtraction with a FLOOR: aggressive 2x + zero floor
        # produced musical-noise crunch ('compressed' sound). Keep at
        # least 35% of every bin — whistle goes, speech body stays.
        gain = np.maximum(mag - 1.2 * noise, 0.35 * mag) / (mag + 1e-9)
        Zc = Z * gain
        _, out = scipy_signal.istft(Zc, fs=fs, nperseg=nper, noverlap=nper // 2)
        if len(out) < len(audio):
            out = np.pad(out, (0, len(audio) - len(out)))
        return out[:len(audio)].astype(np.float32)
    except Exception:
        return audio


def fine_tune(iq, fs):
    """Find the strongest carrier and mix it to baseband.

    Two traps handled here:
    1. HackRF DC spike sits at the tuned frequency — ignore it in the
       peak SEARCH, but NEVER subtract it from the signal: for AM the
       real carrier IS at DC and subtracting the mean killed it
       (correlation with speech was 0.003!).
    2. A stronger neighbor within the search window can hijack tuning —
       window is +/-350 kHz (covers the sweep's half-bin freq error).
    """
    import numpy as np
    n_fft = min(len(iq), 8192)
    spec = np.zeros(n_fft)
    for off in range(0, len(iq) - n_fft, n_fft):
        spec += np.abs(np.fft.fftshift(np.fft.fft(iq[off:off + n_fft]))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(n_fft, 1.0 / fs))
    dc_idx = int(np.argmin(np.abs(freqs)))
    dc_power = float(spec[dc_idx])

    # Peak search ignores the DC bin and its immediate neighbors
    spec_clean = spec.copy()
    spec_clean[max(0, dc_idx - 2):dc_idx + 3] = 0.0
    window = (freqs >= -350000) & (freqs <= 350000)
    w_spec = np.where(window, spec_clean, 0.0)
    peak_idx = int(np.argmax(w_spec))
    peak_power = float(w_spec[peak_idx])

    # Signal at center? (DC dominates or no other peak) -> leave untouched
    if peak_power < 0.5 * dc_power or peak_power <= 1e-12:
        return iq

    f_offset = float(freqs[peak_idx])
    if abs(f_offset) > 10:  # only mix if actually offset
        t = np.arange(len(iq)) / fs
        iq = iq * np.exp(-2j * np.pi * f_offset * t)
    return iq

def demod_nfm(iq, fs, channel_bw=12500, deviation=2500, audio_rate=48000, de_emphasis_us=300, already_tuned=False):
    """Proper NFM demodulator: fine-tune -> channel filter -> FM demod -> de-emphasis -> audio LPF.

    The IQ is fs Hz wide (e.g. 2 MHz) but a PMR/ham channel is only 12.5-25 kHz.
    Demodulating the full band mixes 80 channels of noise over the voice.
    We MUST isolate the channel first."""
    import numpy as np
    from scipy import signal as scipy_signal

    if not already_tuned:
        iq = fine_tune(iq, fs)

    # 2. Channel filter: low-pass IQ to channel bandwidth (12.5 kHz PMR, 25 kHz wide)
    nyq = fs / 2.0
    cutoff = min(channel_bw / 2.0, nyq * 0.95)
    b, a = scipy_signal.butter(5, cutoff / nyq, 'low')
    iq_f = (scipy_signal.filtfilt(b, a, np.real(iq))
            + 1j * scipy_signal.filtfilt(b, a, np.imag(iq)))

    # 3. FM demodulation: instantaneous frequency via phase difference
    phase = np.unwrap(np.angle(iq_f))
    audio = np.diff(phase) * fs / (2.0 * np.pi)   # Hz deviation
    audio = audio / max(deviation, 1.0)            # normalize to ~±1

    # 4. De-emphasis (NFM radios: 300 us; WFM broadcast: 75 us)
    tau = de_emphasis_us * 1e-6
    alpha = 1.0 / (1.0 + fs * tau)
    audio = scipy_signal.lfilter([1.0 - alpha], [1.0, -alpha], audio)

    # 5. Proper resampling to audio_rate FIRST (polyphase, no aliasing).
    #    Audio band-pass must run at audio_rate — a 300-3400 Hz filter at fs=2 MHz
    #    is numerically unstable (NaN).
    from math import gcd
    g = gcd(int(fs), int(audio_rate))
    audio = scipy_signal.resample_poly(audio, int(audio_rate) // g, int(fs) // g)

    # 6. Audio band-pass 300-3400 Hz (voice band) at audio_rate, SOS for stability
    lo = 300.0
    hi = min(3400.0, audio_rate / 2.0 * 0.95)
    sos = scipy_signal.butter(4, [lo / (audio_rate / 2.0), hi / (audio_rate / 2.0)], 'band', output='sos')
    audio = scipy_signal.sosfiltfilt(sos, audio)

    # 7. AGC-lite normalize
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.8
    return audio


def demod_am_sync(iq, fs, audio_rate=48000):
    """Coherent (synchronous) AM demodulation.

    Envelope detection multiplies noise by signal strength — raspy, weak,
    'compressed' audio. Coherent detection tracks carrier phase through a
    narrow loop and mixes down cleanly: better intelligibility, natural
    loudness, no over-compression.
    """
    import numpy as np
    from scipy import signal as scipy_signal
    from math import gcd

    g = gcd(int(fs), audio_rate)
    real = scipy_signal.resample_poly(np.real(iq), audio_rate // g, int(fs) // g)
    imag = scipy_signal.resample_poly(np.imag(iq), audio_rate // g, int(fs) // g)
    c = real + 1j * imag

    # Channel filter: air-band voice ~6 kHz
    sos = scipy_signal.butter(4, 3000 / (audio_rate / 2.0), 'low', output='sos')
    c = scipy_signal.sosfiltfilt(sos, c)

    # Carrier phase estimate: narrow loop filter at DC
    sos_loop = scipy_signal.butter(2, 25.0, 'low', fs=audio_rate, output='sos')
    carrier = scipy_signal.sosfiltfilt(sos_loop, c)
    phase = np.angle(carrier + 1e-12)

    # Coherent projection
    audio = np.real(c * np.exp(-1j * phase))
    audio = audio - np.mean(audio)  # remove carrier DC AFTER demod
    return audio.astype(np.float32)


def iq_to_audio_wav(raw_file, sample_rate=48000, mode='nfm', output=None, data_format='int8'):
    """Convert IQ raw file to demodulated audio WAV (hackrf int8 interleaved)."""
    if output is None:
        output = tempfile.NamedTemporaryFile(suffix='.wav', prefix='voice_demod_', delete=False).name

    import numpy as np
    if data_format == 'uint8':
        _raw = np.fromfile(raw_file, dtype=np.uint8).astype(np.float32)
        _raw = (_raw - 127.5) / 127.5
    else:
        _raw = np.fromfile(raw_file, dtype=np.int8).astype(np.float32) / 128.0
    data = _raw
    iq = data[::2].astype(np.float32) + 1j * data[1::2].astype(np.float32)
    if len(iq) < 100:
        return None

    # Fine-tune ONCE for ALL modes (AM path lacked it entirely)
    iq = fine_tune(iq, sample_rate)

    if mode in ('nfm', 'wfm', 'auto', 'fm'):
        if mode == 'wfm':
            audio = demod_nfm(iq, sample_rate, channel_bw=180000, deviation=75000,
                              audio_rate=48000, de_emphasis_us=75, already_tuned=True)
        else:
            audio = demod_nfm(iq, sample_rate, channel_bw=12500, deviation=2500,
                              audio_rate=48000, de_emphasis_us=300, already_tuned=True)
    elif mode in ('am', 'usb', 'lsb'):
        # Coherent AM detection — envelope demod sounded compressed/weak
        audio = demod_am_sync(iq, sample_rate)
    else:
        # Digital modes: NFM demod as input for DSD
        audio = demod_nfm(iq, sample_rate, channel_bw=12500, deviation=2500,
                          audio_rate=48000, de_emphasis_us=300)

    # Gentle level: scale by 95th percentile to natural loudness.
    # NO tanh/limiting compression — the previous peak/tanh chain made
    # speech sound over-compressed ('пережатый').
    import numpy as _np
    audio = _np.asarray(audio, dtype=_np.float32)
    audio = audio - _np.mean(audio)
    p95 = float(_np.percentile(_np.abs(audio), 95))
    if p95 > 1e-6:
        audio = audio * (0.72 / p95)
    audio = _np.clip(audio, -1.0, 1.0)  # safety only

    # Write 48 kHz 16-bit mono WAV
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
        # dsdccx prints banner/debug lines ('Digital Speech Decoder DSDcc',
        # 'DSDDecoder::resetFrameSync...') which must NOT count as decoded text.
        out_lines = []
        for line in r.stdout.splitlines():
            s = line.strip()
            if not s:
                continue
            if '::' in s:                       # C++ debug traces
                continue
            if any(m in s for m in ('Digital Speech Decoder', 'DSDcc', 'Opened',
                                     'reset', 'symbol 0', 'FrameSync')):
                continue
            out_lines.append(s)
        return '\n'.join(out_lines)
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


def decode_signal(freq_mhz, mode='auto', duration_s=3, raw_file=None, sample_rate=2000000):
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
    if data_format == 'uint8':
        _raw = np.fromfile(raw_file, dtype=np.uint8).astype(np.float32)
        _raw = (_raw - 127.5) / 127.5
    else:
        _raw = np.fromfile(raw_file, dtype=np.int8).astype(np.float32) / 128.0
    data = _raw
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
    if data_format == 'uint8':
        _raw = np.fromfile(raw_file, dtype=np.uint8).astype(np.float32)
        _raw = (_raw - 127.5) / 127.5
    else:
        _raw = np.fromfile(raw_file, dtype=np.int8).astype(np.float32) / 128.0
    data = _raw
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
