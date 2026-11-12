"""Regression tests: NFM voice demodulation must recover a tone through noise.

Bug history: play_voice_sample demodulated the FULL 2 MHz capture (80 PMR
channels of noise over the voice) and a 300-3400 Hz bandpass at fs=2 MHz
produced NaN (numerical instability). Both fixed in voice_decode.demod_nfm.
"""
import numpy as np
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_nfm_raw(path, voice_freq=1000.0, deviation=2500.0, carrier_offset=350000.0,
                  fs=2000000, duration=0.3, noise_amp=0.7):
    """hackrf-style int8 interleaved IQ: FM tone + heavy out-of-channel noise."""
    t = np.arange(int(fs * duration)) / fs
    phase = 2 * np.pi * carrier_offset * t + (deviation / voice_freq) * np.sin(2 * np.pi * voice_freq * t)
    signal = np.exp(1j * phase)
    rng = np.random.default_rng(42)
    noise = (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t))) * noise_amp
    iq = signal + noise
    iq_int8 = np.empty(len(iq) * 2, dtype=np.int8)
    iq_int8[0::2] = np.clip(np.real(iq) * 60, -127, 127).astype(np.int8)
    iq_int8[1::2] = np.clip(np.imag(iq) * 60, -127, 127).astype(np.int8)
    iq_int8.tofile(path)


def test_nfm_recovers_tone_through_noise():
    from voice_decode import iq_to_audio_wav
    raw = tempfile.NamedTemporaryFile(suffix='.raw', delete=False).name
    wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
    try:
        _make_nfm_raw(raw)
        out = iq_to_audio_wav(raw, sample_rate=2000000, mode='nfm', output=wav)
        assert out and os.path.exists(out), "demod produced no wav"
        with wave.open(out) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767
            rate = w.getframerate()
        assert rate == 48000
        assert not np.isnan(audio).any(), "audio contains NaN"
        n = len(audio)
        spec = np.abs(np.fft.rfft(audio * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1.0 / rate)
        peak_f = freqs[np.argmax(spec)]
        ratio = np.max(spec) / (np.mean(spec) + 1e-10)
        assert abs(peak_f - 1000) < 50, f"dominant freq {peak_f:.0f} != 1000"
        assert ratio > 5, f"peak/mean {ratio:.1f} = noise, not tone"
    finally:
        for f in (raw, wav):
            if os.path.exists(f):
                os.unlink(f)


def test_nfm_responds_to_offcenter_carrier():
    """Channel at +350 kHz must still decode after fine-tune mixing."""
    from voice_decode import iq_to_audio_wav
    raw = tempfile.NamedTemporaryFile(suffix='.raw', delete=False).name
    wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
    try:
        _make_nfm_raw(raw, carrier_offset=350000.0)
        out = iq_to_audio_wav(raw, sample_rate=2000000, mode='nfm', output=wav)
        assert out and os.path.getsize(out) > 1000
    finally:
        for f in (raw, wav):
            if os.path.exists(f):
                os.unlink(f)


def test_no_nan_in_wfm_mode():
    from voice_decode import iq_to_audio_wav
    raw = tempfile.NamedTemporaryFile(suffix='.raw', delete=False).name
    wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False).name
    try:
        _make_nfm_raw(raw, deviation=75000.0, carrier_offset=0.0)
        out = iq_to_audio_wav(raw, sample_rate=2000000, mode='wfm', output=wav)
        assert out and os.path.exists(out)
        with wave.open(out) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        assert not np.isnan(audio).any(), "WFM audio contains NaN"
    finally:
        for f in (raw, wav):
            if os.path.exists(f):
                os.unlink(f)
