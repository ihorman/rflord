"""Tests for config, history, export, blacklist, scan_accel modules."""
import sys
import os
import time
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfig:
    """Config loading from YAML."""

    def test_load_config_returns_dict(self):
        from config import load_config
        cfg = load_config()
        assert isinstance(cfg, dict)

    def test_config_has_required_keys(self):
        from config import load_config
        cfg = load_config()
        assert 'scan' in cfg
        assert 'voice' in cfg
        assert 'suppress' in cfg
        assert 'history' in cfg

    def test_config_scan_interval(self):
        from config import load_config
        cfg = load_config()
        assert isinstance(cfg['scan']['interval'], int)
        assert cfg['scan']['interval'] > 0

    def test_config_voice_settings(self):
        from config import load_config
        cfg = load_config()
        assert 'voice_name' in cfg['voice']
        assert 'threshold' in cfg['voice']

    def test_config_suppress_targets(self):
        from config import load_config
        cfg = load_config()
        targets = cfg['suppress']['targets']
        names = set(t['name'] for t in targets)
        assert 'cellular' in names
        assert 'bluetooth' in names
        assert 'gps' in names

    def test_config_custom_path(self):
        from config import load_config
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("scan:\n  interval: 60\n")
            f.flush()
            cfg = load_config(f.name)
            assert cfg['scan']['interval'] == 60
        os.unlink(f.name)


class TestBlacklist:
    """Frequency blacklist filtering."""

    def test_load_blacklist_empty(self):
        from blacklist import load_blacklist
        result = load_blacklist('/nonexistent/path')
        assert result == []

    def test_load_blacklist_with_entries(self):
        from blacklist import load_blacklist
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write("# comment\n98.0\n470:790\n")
            f.flush()
            result = load_blacklist(f.name)
        os.unlink(f.name)
        assert len(result) == 2
        assert (98.0, 98.0) in result
        assert (470.0, 790.0) in result

    def test_is_blacklisted(self):
        from blacklist import is_blacklisted
        blacklist = [(470.0, 790.0), (98.0, 98.0)]
        assert is_blacklisted(500.0, blacklist) is True
        assert is_blacklisted(98.0, blacklist) is True
        assert is_blacklisted(150.0, blacklist) is False

    def test_filter_blacklisted(self):
        from blacklist import filter_blacklisted
        signals = [
            {'freq': 500e6, 'peak': -30, 'avg': -35, 'std': 2},
            {'freq': 150e6, 'peak': -40, 'avg': -45, 'std': 3},
        ]
        blacklist = [(470.0, 790.0)]
        result = filter_blacklisted(signals, blacklist)
        assert len(result) == 1
        assert result[0]['freq'] == 150e6

    def test_filter_empty_blacklist(self):
        from blacklist import filter_blacklisted
        signals = [{'freq': 500e6, 'peak': -30, 'avg': -35, 'std': 2}]
        result = filter_blacklisted(signals, [])
        assert len(result) == 1


class TestScanAccelerator:
    """Band skip logic."""

    def test_initially_no_skip(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=3)
        assert accel.should_skip(88, 250) is False

    def test_skip_after_n_empty(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=3)
        for _ in range(3):
            accel.record_band_result(88, 250, 0)
        assert accel.should_skip(88, 250) is True

    def test_reset_on_signal(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=3)
        for _ in range(2):
            accel.record_band_result(88, 250, 0)
        accel.record_band_result(88, 250, 5)  # Signal found
        assert accel.should_skip(88, 250) is False

    def test_get_active_bands(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=2)
        bands = [(88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3)]
        for _ in range(2):
            accel.record_band_result(88, 250, 0)
        active = accel.get_active_bands(bands)
        assert len(active) == 2
        assert active[0][0] == 250

    def test_minimum_two_bands(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=1)
        bands = [(88, 250, 2000000, 3), (250, 600, 2000000, 3), (600, 1000, 2000000, 3)]
        for b in bands:
            accel.record_band_result(b[0], b[1], 0)
        active = accel.get_active_bands(bands)
        assert len(active) == 3  # Minimum 2, so returns all

    def test_reset(self):
        from scan_accel import ScanAccelerator
        accel = ScanAccelerator(skip_after_empty=1)
        accel.record_band_result(88, 250, 0)
        assert accel.should_skip(88, 250) is True
        accel.reset()
        assert accel.should_skip(88, 250) is False


class TestExport:
    """CSV/JSON export."""

    def test_export_csv(self):
        from export import export_csv
        signals = [
            {'freq': 98e6, 'peak': -25.0, 'avg': -30.0, 'std': 5.0,
             'classification': 'ok', 'type': 'FM', 'identification': 'FM Broadcast', 'distance': '30km'},
        ]
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            export_csv(f.name, signals)
            with open(f.name) as r:
                content = r.read()
        os.unlink(f.name)
        assert 'freq' in content
        assert '98000000' in content or '98' in content

    def test_export_json(self):
        from export import export_json
        signals = [
            {'freq': 98e6, 'peak': -25.0, 'avg': -30.0, 'std': 5.0},
        ]
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            export_json(f.name, signals)
            with open(f.name) as r:
                data = json.load(r)
        os.unlink(f.name)
        assert len(data) == 1
        assert data[0]['freq'] == 98e6


class TestSignalHistory:
    """SQLite signal history."""

    def test_init_db(self):
        from history import SignalHistory
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            h = SignalHistory(f.name)
            h.init_db()
        os.unlink(f.name)

    def test_record_scan(self):
        from history import SignalHistory
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            h = SignalHistory(f.name)
            h.init_db()
            signals = [
                {'freq': 98e6, 'peak': -25.0, 'avg': -30.0, 'std': 5.0,
                 'classification': 'ok', 'type': 'FM', 'identification': 'FM', 'distance': '30km'},
            ]
            h.record_scan(signals, 'hackrf')
        os.unlink(f.name)

    def test_get_trend(self):
        from history import SignalHistory
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            h = SignalHistory(f.name)
            h.init_db()
            for i in range(5):
                signals = [{'freq': 98e6, 'peak': -25.0 - i, 'avg': -30.0, 'std': 5.0,
                           'classification': 'ok', 'type': 'FM', 'identification': 'FM', 'distance': '30km'}]
                h.record_scan(signals, 'hackrf')
            trend = h.get_trend(98.0, n=5)
            assert len(trend) == 5
        os.unlink(f.name)

    def test_cleanup(self):
        from history import SignalHistory
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            h = SignalHistory(f.name)
            h.init_db()
            h.cleanup(max_days=30)
        os.unlink(f.name)
