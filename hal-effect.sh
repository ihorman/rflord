#!/bin/bash
# HAL 9000 voice effect — calm, measured, slightly robotic
# Usage: hal-effect.sh input.mp3 output.wav
# Requires: ffmpeg

INPUT="$1"
OUTPUT="$2"

if [ -z "$INPUT" ] || [ -z "$OUTPUT" ]; then
    echo "Usage: hal-effect.sh input.mp3 output.wav"
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    echo "ffmpeg not found"
    exit 1
fi

# HAL 9000 effect:
# - Slight pitch lowering (0.92x) for deeper, more authoritative tone
# - Slow tempo (0.85x) for measured, deliberate speech
# - Subtle reverb for that "room" feel
# - EQ: boost low-mids (warmth), cut highs (remove sibilance)
# - Light compression for consistent volume
ffmpeg -y -i "$INPUT" \
    -af "asetrate=44100*0.92,aresample=44100,\
atempo=0.85,\
aecho=0.8:0.7:40:0.3,\
equalizer=f=250:width_type=o:width=2:g=2,\
equalizer=f=800:width_type=o:width=2:g=3,\
equalizer=f=2500:width_type=o:width=2:g=-2,\
highpass=f=120,lowpass=f=5000,\
acompressor=threshold=-20dB:ratio=3:attack=5:release=50,\
volume=1.3" \
    "$OUTPUT" 2>/dev/null

if [ -f "$OUTPUT" ]; then
    echo "OK: $OUTPUT"
else
    # Fallback: just convert without effect
    ffmpeg -y -i "$INPUT" "$OUTPUT" 2>/dev/null
    echo "FALLBACK: $OUTPUT"
fi
