#!/bin/bash
# rflord test runner — execute all tests
# Usage: ./tests/run_tests.sh [--verbose] [--coverage]
set -e

cd "$(dirname "$0")/.."

echo "=== RfLord Test Suite ==="
echo "Running at: $(date)"
echo ""

ARGS="-x --tb=short"

if [[ "$1" == "--verbose" || "$1" == "-v" ]]; then
    ARGS="-v --tb=long"
fi

echo "--- Table Alignment Tests ---"
python3 -m pytest tests/test_table_alignment.py $ARGS

echo ""
echo "--- Hotkey Tests ---"
python3 -m pytest tests/test_hotkeys.py $ARGS

echo ""
echo "--- HackRF Switcher Tests ---"
python3 -m pytest tests/test_hackrf_switcher.py $ARGS

echo ""
echo "=== All tests passed ==="
