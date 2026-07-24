"""Test hotkey handling — key-to-command mapping and suppress menu logic."""
import sys
import os
import time
import threading
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rflord import (
    _key_listener, _show_suppress_menu, _suppress_start, _suppress_stop,
    SUPPRESS_TARGETS,
)


class TestKeyMapping:
    """Verify key listener maps correct keys to commands."""

    def _run_listener_with_keys(self, keys, timeout=1.5):
        """Run key listener with simulated key sequence, return _key_cmd."""
        import rflord
        rflord._key_cmd = None
        rflord._menu_active = False

        mock_scr = MagicMock()
        key_queue = list(keys) + [-1] * 20
        call_count = [0]

        def mock_getch():
            call_count[0] += 1
            if call_count[0] <= len(key_queue):
                return key_queue[call_count[0] - 1]
            time.sleep(0.1)
            return -1

        mock_scr.getch = mock_getch
        mock_scr.nodelay = MagicMock()
        mock_scr.timeout = MagicMock()

        t = threading.Thread(target=_key_listener, args=(mock_scr,), daemon=True)
        t.start()
        time.sleep(timeout)
        return rflord._key_cmd

    def test_q_key_quit(self):
        cmd = self._run_listener_with_keys([ord('q')])
        assert cmd == 'quit', f"Expected 'quit', got {cmd}"

    def test_Q_key_quit(self):
        cmd = self._run_listener_with_keys([ord('Q')])
        assert cmd == 'quit', f"Expected 'quit', got {cmd}"

    def test_r_key_rescan(self):
        cmd = self._run_listener_with_keys([ord('r')])
        assert cmd == 'rescan', f"Expected 'rescan', got {cmd}"

    def test_m_key_mute(self):
        cmd = self._run_listener_with_keys([ord('m')])
        assert cmd == 'mute', f"Expected 'mute', got {cmd}"

    def test_v_key_voice(self):
        cmd = self._run_listener_with_keys([ord('v')])
        assert cmd == 'voice', f"Expected 'voice', got {cmd}"

    def test_s_key_suppress(self):
        cmd = self._run_listener_with_keys([ord('s')])
        assert cmd == 'suppress', f"Expected 'suppress', got {cmd}"

    def test_plus_key_interval_up(self):
        cmd = self._run_listener_with_keys([ord('+')])
        assert cmd == 'interval_up', f"Expected 'interval_up', got {cmd}"

    def test_equals_key_interval_up(self):
        cmd = self._run_listener_with_keys([ord('=')])
        assert cmd == 'interval_up', f"Expected 'interval_up', got {cmd}"

    def test_minus_key_interval_down(self):
        cmd = self._run_listener_with_keys([ord('-')])
        assert cmd == 'interval_down', f"Expected 'interval_down', got {cmd}"

    def test_unknown_key_no_change(self):
        cmd = self._run_listener_with_keys([ord('z')])
        assert cmd is None or cmd != 'quit'

    def test_menu_active_blocks_keys(self):
        """When _menu_active is True, key listener should not process keys."""
        import rflord
        rflord._key_cmd = None
        rflord._menu_active = True

        mock_scr = MagicMock()
        mock_scr.getch = lambda: ord('q')
        mock_scr.nodelay = MagicMock()
        mock_scr.timeout = MagicMock()

        t = threading.Thread(target=_key_listener, args=(mock_scr,), daemon=True)
        t.start()
        time.sleep(0.5)

        assert rflord._key_cmd != 'quit'
        rflord._menu_active = False


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
        assert 1575.42e6 in freqs  # L1

    def test_suppress_start_stop(self):
        """Start and stop should not raise."""
        _suppress_start()
        _suppress_stop()

    def test_suppress_targets_toggle(self):
        """Target toggle should work."""
        import rflord
        rflord._suppress_targets['Cellular'] = True
        assert rflord._suppress_targets['Cellular'] is True
        rflord._suppress_targets['Cellular'] = False
        assert rflord._suppress_targets['Cellular'] is False


class TestSuppressMenu:
    """Suppress menu behavior — mock curses to avoid initscr."""

    def test_menu_sets_menu_active_false_after_enter(self):
        """Menu should set _menu_active to False after Enter."""
        import rflord
        rflord._menu_active = False

        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 10  # Enter

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            _show_suppress_menu(mock_scr)

        assert rflord._menu_active is False

    def test_menu_escape_closes(self):
        """ESC should close the menu."""
        import rflord
        rflord._menu_active = False

        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27  # ESC

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            _show_suppress_menu(mock_scr)

        assert rflord._menu_active is False

    def test_menu_arrow_keys_move_cursor(self):
        """Down arrow should move cursor, Enter should close."""
        import rflord
        rflord._menu_active = False

        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        # Down, then Enter
        import curses
        mock_scr.getch.side_effect = [curses.KEY_DOWN, 10]

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            _show_suppress_menu(mock_scr)

        assert rflord._menu_active is False

    def test_menu_space_toggles_target(self):
        """Space should toggle the current target."""
        import rflord
        rflord._menu_active = False
        rflord._suppress_targets = {}

        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        # Space (toggle), then Enter
        mock_scr.getch.side_effect = [ord(' '), 10]

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True), \
             patch('curses.A_REVERSE', 0, create=True):
            _show_suppress_menu(mock_scr)

        # At least one target should be toggled on
        assert any(rflord._suppress_targets.values()), "Space should toggle a target on"
