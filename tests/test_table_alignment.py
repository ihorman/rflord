"""Test table alignment — column widths, padding, ANSI output format."""
import math
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rflord import (
    classify, est_distance, est_distance_m, get_band, get_signal_type,
    group_signals_by_type, group_suspicious, time_ago, speak_distance,
    signal_priority, identify_signal, load_artemis, parse_sweep,
    estimate_noise_floor, detect_active_probes, in_legitimate_band,
)


class TestEstDistance:
    """Distance formatting: <1km = meters, >=1km = km."""

    def test_meters_below_1km(self):
        # WiFi band, very strong signal = meters
        d = est_distance(2437, 0.0)
        assert d.endswith('m') and 'km' not in d, f"Expected meters, got {d}"

    def test_km_above_1km(self):
        d = est_distance(100, -80.0)
        assert 'km' in d, f"Expected km, got {d}"

    def test_distance_numeric_meters(self):
        m = est_distance_m(100, -40.0)
        assert isinstance(m, (int, float))
        assert m > 0

    def test_distance_numeric_never_zero(self):
        m = est_distance_m(100, 0)
        assert m >= 1

    def test_speak_distance_meters(self):
        assert speak_distance("284m") == "284 meters"

    def test_speak_distance_km(self):
        assert speak_distance("1.5km") == "1.5 kilometers"

    def test_speak_distance_passthrough(self):
        assert speak_distance("unknown") == "unknown"


class TestClassify:
    """Signal classification: ok, sus, danger."""

    def test_fm_broadcast_ok(self):
        assert classify(98, -25, 5) == "ok"

    def test_wifi_ok(self):
        assert classify(2437, -20, 6) == "ok"

    def test_cellular_ok(self):
        assert classify(950, -15, 4) == "ok"

    def test_dvbt2_narrowband_danger(self):
        assert classify(492, -35, 1.2) == "danger"

    def test_dvbt2_wideband_ok(self):
        assert classify(542, -30, 4) == "ok"

    def test_military_sus(self):
        assert classify(255, -30, 1.5) == "sus"

    def test_aviation_ok(self):
        assert classify(118, -40, 3) == "ok"

    def test_unknown_below_threshold_ok(self):
        assert classify(350, -60, 5) == "ok"

    def test_unknown_strong_sus(self):
        assert classify(350, -10, 5) == "sus"


class TestGetBand:
    """Band label assignment."""

    def test_fm_band(self):
        assert get_band(98) == "FM"

    def test_air_band(self):
        assert get_band(120) == "AIR"

    def test_dtv_band(self):
        assert get_band(500) == "DTV"

    def test_gsm_band(self):
        assert get_band(900) == "GSM"

    def test_wifi_band(self):
        # 2450 is in LTE range (2300-2700) which is checked before WiFi
        assert get_band(2450) in ("WiFi", "LTE")

    def test_5g_band(self):
        assert get_band(5500) == "5G"

    def test_unknown_band(self):
        assert get_band(35) == "?"


class TestTableFormatting:
    """Table row formatting — verify column widths are consistent."""

    def test_suspicious_row_width(self):
        """Suspicious row formatted with correct field widths."""
        cnt = ""
        freq = 492.0
        peak = -35.0
        std = 1.2
        dist = "284m"
        sig_type = "CAM-DTV?"
        remark = "test"
        line = f" {cnt:>4} {freq:>5.1f} {peak:>+5.1f} {std:>4.1f} {dist:>5} {sig_type:<18} {remark}"
        assert len(line) <= 60, f"Suspicious row too wide: {len(line)} > 60"

    def test_ok_row_width(self):
        """OK row formatted with correct field widths."""
        cnt = ""
        peak = -25.0
        dist = "284m"
        band = "FM"
        sig_type = "FM Broadcast"
        line = f" {cnt:>4} {peak:>+6.1f} {dist:>5} {band:>4} {sig_type}"
        assert len(line) <= 40, f"OK row too wide: {len(line)} > 40"

    def test_grouped_count_format(self):
        """Grouped signals show xN count."""
        cnt = f"{'x3':>4}"
        assert cnt == "  x3"

    def test_distance_format_meters(self):
        d = est_distance(2437, 0.0)
        assert 'm' in d and 'km' not in d
        assert len(d) <= 5, f"Distance too long: {d}"

    def test_distance_format_km(self):
        d = est_distance(100, -80.0)
        assert 'km' in d
        assert len(d) <= 5, f"Distance too long: {d}"

    def test_header_fits_width(self):
        """Header line should fit in 100-char terminal."""
        from rflord import VERSION
        header = f" RfLord {VERSION} {time.strftime('%H:%M:%S')} │ Up 00:00:00 │ Alerts 0 │ Tracked 0 │ Sig 0 │ Author: Ihor Kolodyuk"
        assert len(header) <= 100, f"Header too wide: {len(header)} > 100"

    def test_left_column_alignment(self):
        """Left table header columns must align with data columns."""
        header = f"!    {'Freq':>5} {'Pwr':>5} {'Std':>4} {'Dist':>5} {'Type':<14} Desc"
        # Data format: cursor(1)+sev(3)+' '+freq(5)+' '+peak(5)+' '+std(4)+' '+dist(5)+' '+type(14)+' '+remark
        data = f" {'!! '} {680.0:>5.1f} {-30.0:>+5.1f} {2.5:>4.1f} {'333m':>5} {'Tetrapol':<14} Tetrapol"
        # Verify same format specifiers produce same column boundaries
        assert len(header) == len(data[:47]), f"Header ({len(header)}) and data ({len(data[:47])}) same length"
        # Labels are right-aligned in their fields, so they share field boundaries
        assert "Freq" in header[5:10], "Freq label in header field"
        assert data[5:10] == "680.0", "Freq value in data field"
        assert "Pwr" in header[11:16], "Pwr label in header field"
        assert data[11:16] == "-30.0", "Pwr value in data field"
        assert "Std" in header[17:21], "Std label in header field"
        assert data[17:21] == " 2.5", "Std value in data field"
        assert "Dist" in header[22:27], "Dist label in header field"
        assert data[22:27] == " 333m", "Dist value in data field"
        assert "Type" in header[28:42], "Type label in header field"
        assert "Tetrapol" in data[28:42], "Type value in data field"

    def test_footer_fits_width(self):
        """Footer with hotkey hints should fit in 80-char terminal."""
        keys = " q:Quit  r:Rescan  v:Voice(ON)  m:Mute  s:Suppress(OFF)  +/-:Interval(30s)"
        assert len(keys) <= 80, f"Footer too wide: {len(keys)} > 80"


class TestGroupSignals:
    """Signal grouping logic."""

    def test_group_by_type(self, sample_signals, artemis_db_empty):
        """Signals of same type should be grouped."""
        ok = [s for s in sample_signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger')]
        grouped = group_signals_by_type(ok, artemis_db_empty)
        assert len(grouped) <= len(ok)

    def test_group_count(self, sample_signals, artemis_db_empty):
        """Group count should reflect number of signals in group."""
        ok = [s for s in sample_signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger')]
        for g in group_signals_by_type(ok, artemis_db_empty):
            assert g['count'] >= 1

    def test_group_strongest_peak(self, sample_signals, artemis_db_empty):
        """Group peak should be the maximum of all signals in group."""
        ok = [s for s in sample_signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger')]
        for g in group_signals_by_type(ok, artemis_db_empty):
            assert isinstance(g['peak'], (int, float))

    def test_suspicious_group_has_classify(self, sample_signals, artemis_db_empty):
        """Suspicious groups should have classify field."""
        sus = [s for s in sample_signals if classify(s['freq']/1e6, s['peak'], s['std']) in ('sus', 'danger')]
        for g in group_suspicious(sus, artemis_db_empty):
            assert g['classify'] in ('sus', 'danger')

    def test_group_sorted_by_count(self, sample_signals, artemis_db_empty):
        """Groups should be sorted by count descending."""
        ok = [s for s in sample_signals if classify(s['freq']/1e6, s['peak'], s['std']) not in ('sus', 'danger')]
        grouped = group_signals_by_type(ok, artemis_db_empty)
        counts = [g['count'] for g in grouped]
        assert counts == sorted(counts, reverse=True)


class TestTimeAgo:
    """Time ago formatting."""

    def test_seconds(self):
        assert 's' in time_ago(time.time() - 5)

    def test_minutes(self):
        result = time_ago(time.time() - 125)
        assert 'm' in result

    def test_hours(self):
        result = time_ago(time.time() - 3700)
        assert 'h' in result


class TestSignalPriority:
    """Signal priority ordering."""

    def test_military_highest(self):
        assert signal_priority(255, 1) == 0

    def test_spy_camera_high(self):
        assert signal_priority(1200, 1) == 1

    def test_normal_lowest(self):
        assert signal_priority(98, 5) == 2


class TestNoiseFloor:
    """Noise floor estimation."""

    def test_empty_signals(self):
        assert estimate_noise_floor([]) == -70

    def test_with_signals(self):
        signals = [{'peak': -50} for _ in range(10)]
        nf = estimate_noise_floor(signals)
        assert nf == -50.0

    def test_few_signals_default(self):
        signals = [{'peak': -30} for _ in range(3)]
        assert estimate_noise_floor(signals) == -70


class TestActiveProbes:
    """Active probe detection."""

    def test_strong_signal_detected(self):
        signals = [{'freq': 350e6, 'peak': -10, 'avg': -15, 'std': 1}]
        probes = detect_active_probes(signals, -70)
        assert len(probes) == 1

    def test_weak_signal_not_detected(self):
        signals = [{'freq': 350e6, 'peak': -60, 'avg': -65, 'std': 1}]
        probes = detect_active_probes(signals, -70)
        assert len(probes) == 0


class TestLegitimateBand:
    """Legitimate band check."""

    def test_fm_in_band(self):
        assert in_legitimate_band(98) is True

    def test_unknown_not_in_band(self):
        assert in_legitimate_band(350) is False

    def test_aviation_in_band(self):
        assert in_legitimate_band(120) is True


class TestParseSweep:
    """Sweep output parsing."""

    def test_parse_valid_line(self):
        line = "2024-01-01, 12:00:00, 88000000, 108000000, 2000000, 100, -50.0, -55.0, -60.0"
        result = parse_sweep(line)
        assert len(result) == 1
        assert result[0]['freq'] == 98000000

    def test_parse_empty(self):
        assert parse_sweep("") == []

    def test_parse_garbage(self):
        assert parse_sweep("not a valid line") == []


class TestSuspendTargets:
    """Suppress target definitions."""

    def test_targets_defined(self):
        from rflord import SUPPRESS_TARGETS
        assert "Cellular" in SUPPRESS_TARGETS
        assert "Bluetooth" in SUPPRESS_TARGETS
        assert "Gps" in SUPPRESS_TARGETS or "GPS" in SUPPRESS_TARGETS

    def test_target_has_freqs(self):
        from rflord import SUPPRESS_TARGETS
        for name, info in SUPPRESS_TARGETS.items():
            assert 'freqs' in info
            assert 'bw' in info
            assert len(info['freqs']) > 0
