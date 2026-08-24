#!/bin/bash
# HAL 9000 voice effect — makes TTS sound like HAL from 2001: A Space Odyssey
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

# HAL 9000 effect: lower pitch, slight reverb, EQ boost mid-range
ffmpeg -y -i "$INPUT" \
    -af "asetrate=44100*0.82,aresample=44100,atempo=1.22,\
aecho=0.8:0.88:60:0.4,\
equalizer=f=1000:width_type=o:width=2:g=3,\
equalizer=f=3000:width_type=o:width=2:g=2,\
highpass=f=200,lowpass=f=4000,\
volume=1.5" \
    "$OUTPUT" 2>/dev/null

if [ -f "$OUTPUT" ]; then
    echo "OK: $OUTPUT"
else
    # Fallback: just convert without effect
    ffmpeg -y -i "$INPUT" "$OUTPUT" 2>/dev/null
    echo "FALLBACK: $OUTPUT"
fi
