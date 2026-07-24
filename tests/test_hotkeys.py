"""Test hotkey handling — key reading and suppress menu logic."""
import sys
import os
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rflord import (
    _read_key, _show_suppress_menu, _suppress_start, _suppress_stop,
    SUPPRESS_TARGETS,
)


class TestKeyMapping:
    """Verify _read_key maps correct keys to commands."""

    def _read_key_with(self, key_code):
        """Run _read_key with a single simulated keypress."""
        mock_scr = MagicMock()
        mock_scr.getch.return_value = key_code
        return _read_key(mock_scr)

    def test_q_key_quit(self):
        assert self._read_key_with(ord('q')) == 'quit'

    def test_Q_key_quit(self):
        assert self._read_key_with(ord('Q')) == 'quit'

    def test_r_key_rescan(self):
        assert self._read_key_with(ord('r')) == 'rescan'

    def test_R_key_rescan(self):
        assert self._read_key_with(ord('R')) == 'rescan'

    def test_m_key_mute(self):
        assert self._read_key_with(ord('m')) == 'mute'

    def test_v_key_voice(self):
        assert self._read_key_with(ord('v')) == 'voice'

    def test_s_key_suppress(self):
        assert self._read_key_with(ord('s')) == 'suppress'

    def test_S_key_suppress(self):
        assert self._read_key_with(ord('S')) == 'suppress'

    def test_plus_key_interval_up(self):
        assert self._read_key_with(ord('+')) == 'interval_up'

    def test_equals_key_interval_up(self):
        assert self._read_key_with(ord('=')) == 'interval_up'

    def test_minus_key_interval_down(self):
        assert self._read_key_with(ord('-')) == 'interval_down'

    def test_no_key_returns_none(self):
        assert self._read_key_with(-1) is None

    def test_unknown_key_returns_none(self):
        assert self._read_key_with(ord('z')) is None

    def test_getch_exception_returns_none(self):
        mock_scr = MagicMock()
        mock_scr.getch.side_effect = Exception("curses error")
        assert _read_key(mock_scr) is None


class TestSuppressTargets:
    """Suppress target definitions and state management."""

    def test_all_targets_have_required_fields(self):
        for name, info in SUPPRESS_TARGETS.items():
            assert 'freqs' in info, f"{name} missing 'freqs'"
            assert 'bw' in info, f"{name} missing 'bw'"
            assert isinstance(info['freqs'], list)
            assert all(isinstance(f, (int, float)) for f in info['freqs'])
            assert info['bw'] > 0

    def test_cellular_frequencies(self):
        freqs = SUPPRESS_TARGETS['Cellular']['freqs']
        assert 850e6 in freqs
        assert 900e6 in freqs
        assert 1800e6 in freqs

    def test_bluetooth_frequency(self):
        freqs = SUPPRESS_TARGETS['Bluetooth']['freqs']
        assert 2440e6 in freqs

    def test_gps_frequency(self):
        freqs = SUPPRESS_TARGETS['GPS']['freqs']
        assert 1575.42e6 in freqs

    def test_suppress_start_stop(self):
        _suppress_start()
        _suppress_stop()

    def test_suppress_targets_toggle(self):
        import rflord
        rflord._suppress_targets['Cellular'] = True
        assert rflord._suppress_targets['Cellular'] is True
        rflord._suppress_targets['Cellular'] = False
        assert rflord._suppress_targets['Cellular'] is False


class TestSuppressMenu:
    """Suppress menu behavior — mock curses to avoid initscr."""

    def test_menu_enter_closes(self):
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 10  # Enter

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            result = _show_suppress_menu(mock_scr)

        assert result is True

    def test_menu_escape_closes(self):
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27  # ESC

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            result = _show_suppress_menu(mock_scr)

        assert result is True

    def test_menu_arrow_keys_move_cursor(self):
        import curses
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.side_effect = [curses.KEY_DOWN, 10]

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            result = _show_suppress_menu(mock_scr)

        assert result is True

    def test_menu_space_toggles_target(self):
        import rflord
        rflord._suppress_targets = {}

        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.side_effect = [ord(' '), 10]

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            _show_suppress_menu(mock_scr)

        assert any(rflord._suppress_targets.values()), "Space should toggle a target on"
