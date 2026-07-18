# 📡 RF Lord — RF Spectrum Monitor

Real-time RF spectrum monitoring with drone detection, voice alerts, and signal analysis. Built for HackRF One and RTL-SDR.

**Author: Ihor Kolodyuk**

## Features

- **Live spectrum table** — static refresh, color-coded by signal strength
- **Drone detection** — 45 RF signatures (DJI OcuSync/O3/O4, HDZero, Walksnail, ELRS, Crossfire, analog FPV)
- **Voice alerts** — TTS announcements with signal type analysis on new detections
- **Signal classification** — Artemis 3 database (426 signatures) + custom drone database
- **Territory detection** — CITY / SUBURBAN / TOWNSHIP / COUNTRYSIDE based on signal density
- **Distance estimation** — FSPL-based distance calculation per signal type
- **Voice decoding** — DSD (DMR, D-STAR, NXDN), multimon-ng (POCSAG, DTMF, Morse)
- **FPV video decode** — Analog NTSC/PAL frame extraction from FPV transmitters
- **IQ capture** — Automatic sample capture and analysis for suspicious signals
- **Duty scan mode** — Continuous monitoring with configurable interval

## Quick Start

```bash
# Install dependencies
sudo apt-get install -y hackrf rtl-sdr dsdcc multimon-ng sox
pip3 install edge-tts numpy

# Clone
git clone https://github.com/ihorman/rflord.git
cd rflord

# Run the monitor
python3 rflord.py

# Or with custom interval
python3 rflord.py --interval 60
```

## Usage

### Clean Table Monitor (rflord)

```bash
# Default — full scan, 120s interval, voice alerts
python3 rflord.py

# Faster updates
python3 rflord.py --interval 60

# Install as system command
sudo cp rflord.py /usr/local/bin/rflord
sudo chmod +x /usr/local/bin/rflord
rflord --interval 60
```

### Full Scanner (scanner.py)

```bash
# Single scan with full report
python3 scanner.py --focus full

# Camera-focused scan (900 MHz, 1.2 GHz, 2.4 GHz, 5.8 GHz)
python3 scanner.py --focus cameras

# Continuous duty monitoring
python3 scanner.py --duty --interval 120

# Specific frequency range
python3 scanner.py --band 2400:2500

# Force device
python3 scanner.py --device hackrf
python3 scanner.py --device rtlsdr
```

### Voice Decoder

```bash
# Decode voice at frequency
python3 voice_decode.py scan 155.0         # Auto-detect mode
python3 voice_decode.py scan 130.0 --mode am   # AM (air band)
python3 voice_decode.py scan 446.0 --mode dmr  # DMR digital
python3 voice_decode.py scan 446.0 --mode pocsag  # POCSAG pagers
```

### FPV Video Decode

```bash
# Capture and decode FPV video
python3 fpv_decode.py capture --freq 5800 --standard NTSC --output frame.png
python3 fpv_decode.py capture --freq 1280 --standard PAL --spectrogram --output frame.png
```

## Color Coding

| Color | Meaning |
|-------|---------|
| 🔴 RED | Top 3 strongest suspicious signals |
| 🟡 YELLOW | Other suspicious signals |
| 🟢 GREEN | Known/identified signals |
| 🟣 MAGENTA | Drone activity detected |
| ⚡ CW | Continuous carrier (possible beacon) |

## Signal Types

| Code | Description |
|------|-------------|
| DP/USB | DisplayPort/USB interference harmonic |
| USB-noise | USB 2.0 clock noise (480 MHz harmonics) |
| DAB | Digital Audio Broadcasting |
| CW | Continuous wave carrier |
| TETRA | Public safety radio |
| WiFi/BT | WiFi or Bluetooth |
| Digital | Digital modulation (bursty) |
| Analog | Analog signal |

## Architecture

```
rflord.py          — Clean table monitor (main UI)
scanner.py         — Full scanner with detailed reports
drone_rf_db.py     — Drone RF signature database (45 signatures)
drone_dsp.py       — Drone signal DSP analysis (OFDM detection)
voice_decode.py    — Voice decoder (DSD + multimon-ng)
rf_analysis.py     — Distance estimation + territory classification
fpv_decode.py      — FPV video frame decoder
tv_capture.py      — TV frame capture (analog + digital)
```

## Dependencies

- **Hardware**: HackRF One or RTL-SDR
- **System**: hackrf_transfer, rtl_sdr, rtl_power, dsdccx, multimon-ng, sox, aplay/paplay
- **Python**: numpy, edge-tts
- **Audio**: PipeWire or PulseAudio

## How It Works

1. Scans all RF bands using hackrf_sweep (1 MHz - 6 GHz)
2. Classifies each signal against known databases
3. Identifies drone activity using 45 RF signatures
4. Estimates distance using Free-Space Path Loss
5. Classifies territory (city/suburban/countryside)
6. Captures IQ samples for deeper analysis
7. Tries voice decode (DSD, multimon-ng) on narrowband signals
8. Announces findings via TTS voice alerts

## Detection Capabilities

- **Drones**: DJI OcuSync/O3/O4, HDZero, Walksnail, ELRS, Crossfire, TBS Tracer, FrSky, Spektrum
- **Analog video**: NTSC/PAL FPV transmitters (900 MHz, 1.2 GHz, 2.4 GHz, 5.8 GHz)
- **Digital voice**: DMR, D-STAR, dPMR, YSF, NXDN, P25
- **Data**: POCSAG, FLEX, EAS, DTMF, Morse, AFSK
- **Cellular**: GSM 900/1800, 3G, 4G LTE
- **Broadcast**: FM, DAB, DVB-T

## Limitations

- WiFi IP cameras are indistinguishable from normal WiFi traffic
- Cameras recording locally have no RF emission
- DJI drones use encrypted video — cannot decode content
- Narrowband signals (<50 kHz) are not video transmitters
- 900 MHz band is GSM cellular in most locations

## License

MIT

## Author

**Ihor Kolodyuk**
