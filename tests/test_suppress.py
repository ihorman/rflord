"""Tests for signal suppression feature."""
import pytest
import sys
import os
import tempfile
import subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rflord import (
    _build_suppress_targets, _generate_noise, _suppress_start, _suppress_stop,
    _suppress_active, _suppress_procs, SUPPRESS_TARGETS
)


class TestBuildSuppressTargets:
    """Test config format conversion."""

    def test_dict_passthrough(self):
        """Dict format passes through unchanged."""
        d = {'GPS': {'freqs': [1575420000], 'bw': 2000000}}
        assert _build_suppress_targets(d) == d

    def test_list_conversion(self):
        """List format converts to grouped dict."""
        lst = [
            {'name': 'cellular', 'freq': 900000000, 'bw': 80000000},
            {'name': 'cellular', 'freq': 1800000000, 'bw': 100000000},
            {'name': 'gps', 'freq': 1575420000, 'bw': 2000000},
        ]
        result = _build_suppress_targets(lst)
        assert 'Cellular' in result
        assert len(result['Cellular']['freqs']) == 2
        assert 900000000 in result['Cellular']['freqs']
        assert 1800000000 in result['Cellular']['freqs']
        assert 'GPS' in result
        assert result['GPS']['freqs'] == [1575420000]

    def test_default_targets_exist(self):
        """Default config should have cellular, bluetooth, gps."""
        assert len(SUPPRESS_TARGETS) >= 3
        names = [n.lower() for n in SUPPRESS_TARGETS.keys()]
        assert 'cellular' in names or 'Cellular' in SUPPRESS_TARGETS
        assert 'bluetooth' in names or 'Bluetooth' in SUPPRESS_TARGETS
        assert 'gps' in names or 'Gps' in SUPPRESS_TARGETS or 'GPS' in SUPPRESS_TARGETS


class TestGenerateNoise:
    """Test noise file generation."""

    def test_creates_file(self):
        """Should create a noise file."""
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
            path = f.name
        try:
            _generate_noise(path, duration_s=1, rate=2000000)
            assert os.path.exists(path)
            size = os.path.getsize(path)
            assert size == 2000000  # 1 second at 2 MHz, int8 = 2M bytes
        finally:
            os.unlink(path)

    def test_overwrites_existing(self, tmp_path):
        """Should overwrite existing file."""
        path = str(tmp_path / "noise.bin")
        with open(path, 'wb') as f:
            f.write(b'\x00' * 100)
        _generate_noise(path, duration_s=1, rate=2000000)
        assert os.path.getsize(path) == 2000000

    def test_values_in_range(self, tmp_path):
        """IQ samples should be in [-127, 127]."""
        path = str(tmp_path / "noise.bin")
        _generate_noise(path, duration_s=1, rate=100000)
        data = open(path, 'rb').read()
        for b in data[:1000]:
            assert -128 <= b - (b > 127) * 256 <= 127


class TestSuppressLifecycle:
    """Test start/stop lifecycle."""

    def test_stop_clears_procs(self):
        """_suppress_stop should clear process list."""
        import rflord
        rflord._suppress_procs = []
        rflord._suppress_active = True
        _suppress_stop()
        assert rflord._suppress_procs == []
        assert rflord._suppress_active is False

    def test_stop_terminates_processes(self):
        """_suppress_stop should terminate running processes."""
        import rflord
        # Create a dummy process
        p = subprocess.Popen(['sleep', '60'])
        rflord._suppress_procs = [p]
        rflord._suppress_active = True
        _suppress_stop()
        assert p.poll() is not None  # Process terminated
        assert rflord._suppress_procs == []
        assert rflord._suppress_active is False

    def test_start_with_no_targets_does_nothing(self):
        """_suppress_start with no active targets should not start anything."""
        import rflord
        old_targets = rflord._suppress_targets.copy()
        rflord._suppress_targets = {name: False for name in SUPPRESS_TARGETS}
        rflord._suppress_active = True
        try:
            _suppress_start()
            assert rflord._suppress_active is False
            assert len(rflord._suppress_procs) == 0
        finally:
            rflord._suppress_targets = old_targets
