"""Test hotkey handling — key reading, log view, history view, and main loop integration."""
import sys
import os
import time
import tempfile
import sqlite3
from unittest.mock import MagicMock, patch, mock_open, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rflord import (
    _read_key, _show_suppress_menu, _suppress_start, _suppress_stop,
    _show_log_view, _show_history_view,
    SUPPRESS_TARGETS, LOG_DIR,
)
from history import SignalHistory


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

    def test_M_key_mute(self):
        assert self._read_key_with(ord('M')) == 'mute'

    def test_v_key_voice(self):
        assert self._read_key_with(ord('v')) == 'voice'

    def test_V_key_voice(self):
        assert self._read_key_with(ord('V')) == 'voice'

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

    def test_l_key_log(self):
        assert self._read_key_with(ord('l')) == 'log'

    def test_L_key_log(self):
        assert self._read_key_with(ord('L')) == 'log'

    def test_h_key_history(self):
        assert self._read_key_with(ord('h')) == 'history'

    def test_H_key_history(self):
        assert self._read_key_with(ord('H')) == 'history'

    def test_d_key_details(self):
        assert self._read_key_with(ord('d')) == 'details'

    def test_D_key_details(self):
        assert self._read_key_with(ord('D')) == 'details'

    def test_e_key_export(self):
        assert self._read_key_with(ord('e')) == 'export'

    def test_E_key_export(self):
        assert self._read_key_with(ord('E')) == 'export'

    def test_up_arrow_cursor_up(self):
        import curses
        result = self._read_key_with(curses.KEY_UP)
        assert result == 'cursor_up'

    def test_down_arrow_cursor_down(self):
        import curses
        result = self._read_key_with(curses.KEY_DOWN)
        assert result == 'cursor_down'

    def test_esc_cursor_off(self):
        result = self._read_key_with(27)
        assert result == 'cursor_off'

    def test_no_key_returns_none(self):
        assert self._read_key_with(-1) is None

    def test_unknown_key_returns_none(self):
        assert self._read_key_with(ord('z')) is None

    def test_getch_exception_returns_none(self):
        mock_scr = MagicMock()
        mock_scr.getch.side_effect = Exception("curses error")
        assert _read_key(mock_scr) is None


class TestCursorActiveFlag:
    """Verify _cursor_active is set by arrow keys and cleared by ESC."""

    def test_up_arrow_sets_cursor_active(self):
        import rflord
        rflord._cursor_active = False
        mock_scr = MagicMock()
        import curses
        mock_scr.getch.return_value = curses.KEY_UP
        _read_key(mock_scr)
        assert rflord._cursor_active is True

    def test_down_arrow_sets_cursor_active(self):
        import rflord
        rflord._cursor_active = False
        mock_scr = MagicMock()
        import curses
        mock_scr.getch.return_value = curses.KEY_DOWN
        _read_key(mock_scr)
        assert rflord._cursor_active is True

    def test_esc_clears_cursor_active(self):
        import rflord
        rflord._cursor_active = True
        mock_scr = MagicMock()
        mock_scr.getch.return_value = 27
        _read_key(mock_scr)
        assert rflord._cursor_active is False


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


class TestLogView:
    """Test _show_log_view popup."""

    def test_log_view_reads_log_file(self):
        """Log view should read from LOG_DIR/rflord.log."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        # ESC to close immediately
        mock_scr.getch.return_value = 27

        log_content = "2026-07-18 10:00:00 | INFO | Scan #1 started\n2026-07-18 10:00:05 | WARNING | SUSPICIOUS: 680.0 MHz\n"

        with patch('builtins.open', mock_open(read_data=log_content)), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        # Should have called addstr at least once (drawn the popup)
        assert mock_scr.addstr.called
        # Should have called refresh
        assert mock_scr.refresh.called

    def test_log_view_handles_missing_file(self):
        """Log view should handle missing log file gracefully."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27  # ESC

        with patch('builtins.open', side_effect=FileNotFoundError), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        # Should still draw the popup with "No log file found"
        assert mock_scr.addstr.called

    def test_log_view_scroll_down(self):
        """Log view should handle scroll down."""
        import curses
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (10, 80)  # Small screen
        # Down arrow, then ESC
        mock_scr.getch.side_effect = [curses.KEY_DOWN, 27]

        lines = [f"Log line {i}" for i in range(50)]
        log_content = "\n".join(lines) + "\n"

        with patch('builtins.open', mock_open(read_data=log_content)), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.refresh.called

    def test_log_view_scroll_up(self):
        """Log view should handle scroll up."""
        import curses
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (10, 80)
        # Up arrow (should be no-op since we start at bottom), then ESC
        mock_scr.getch.side_effect = [curses.KEY_UP, 27]

        lines = [f"Log line {i}" for i in range(50)]
        log_content = "\n".join(lines) + "\n"

        with patch('builtins.open', mock_open(read_data=log_content)), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.refresh.called

    def test_log_view_l_key_closes(self):
        """Log view should close on 'l' key."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = ord('l')

        with patch('builtins.open', mock_open(read_data="test\n")), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.refresh.called

    def test_log_view_enter_closes(self):
        """Log view should close on Enter."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 10

        with patch('builtins.open', mock_open(read_data="test\n")), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.refresh.called

    def test_log_view_empty_log(self):
        """Log view should handle empty log."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27

        with patch('builtins.open', mock_open(read_data="")), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.addstr.called

    def test_log_view_io_error(self):
        """Log view should handle IO errors."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27

        with patch('builtins.open', side_effect=IOError("disk error")), \
             patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_log_view(mock_scr)

        assert mock_scr.addstr.called


class TestHistoryView:
    """Test _show_history_view popup."""

    def _make_history(self, tmpdir):
        """Create a SignalHistory with some test data."""
        db_path = os.path.join(tmpdir, 'test.db')
        h = SignalHistory(db_path)
        h.init_db()
        # Record some scans
        now = time.time()
        signals = [
            {'freq': 680e6, 'peak': -30.0, 'avg': -35.0, 'std': 2.5, 'classification': 'sus'},
            {'freq': 942e6, 'peak': -10.0, 'avg': -15.0, 'std': 1.0, 'classification': 'ok'},
        ]
        h.record_scan(signals, 'hackrf')
        # Record a second scan 1 minute later
        signals2 = [
            {'freq': 680e6, 'peak': -28.0, 'avg': -33.0, 'std': 3.0, 'classification': 'sus'},
        ]
        h.record_scan(signals2, 'hackrf')
        return h

    def test_history_view_no_history_module(self):
        """History view should show 'disabled' popup when history is None."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27

        with patch('curses.color_pair', return_value=0), \
             patch('curses.A_BOLD', 0, create=True):
            _show_history_view(mock_scr, 0, [], None, None)

        assert mock_scr.addstr.called

    def test_history_view_no_suspicious_signals(self):
        """History view should show 'no signal selected' when no suspicious signals."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27

        with tempfile.TemporaryDirectory() as tmpdir:
            h = self._make_history(tmpdir)
            with patch('curses.color_pair', return_value=0), \
                 patch('curses.A_BOLD', 0, create=True):
                _show_history_view(mock_scr, 0, [], None, h)

        assert mock_scr.addstr.called

    def test_history_view_with_data(self):
        """History view should display signal history."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (40, 120)
        mock_scr.getch.return_value = 27

        with tempfile.TemporaryDirectory() as tmpdir:
            h = self._make_history(tmpdir)
            # Create signals list with a suspicious signal at 680 MHz
            signals = [
                {'freq': 680e6, 'peak': -30.0, 'avg': -35.0, 'std': 2.5},
            ]
            with patch('curses.color_pair', return_value=0), \
                 patch('curses.A_BOLD', 0, create=True):
                _show_history_view(mock_scr, 0, signals, None, h)

        assert mock_scr.addstr.called
        assert mock_scr.refresh.called

    def test_history_view_h_key_closes(self):
        """History view should close on 'h' key."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = ord('h')

        with tempfile.TemporaryDirectory() as tmpdir:
            h = self._make_history(tmpdir)
            signals = [{'freq': 680e6, 'peak': -30.0, 'avg': -35.0, 'std': 2.5}]
            with patch('curses.color_pair', return_value=0), \
                 patch('curses.A_BOLD', 0, create=True):
                _show_history_view(mock_scr, 0, signals, None, h)

        assert mock_scr.refresh.called

    def test_history_view_invalid_cursor(self):
        """History view should handle out-of-range cursor position."""
        mock_scr = MagicMock()
        mock_scr.getmaxyx.return_value = (24, 80)
        mock_scr.getch.return_value = 27

        with tempfile.TemporaryDirectory() as tmpdir:
            h = self._make_history(tmpdir)
            with patch('curses.color_pair', return_value=0), \
                 patch('curses.A_BOLD', 0, create=True):
                # Cursor 99 with empty signals list
                _show_history_view(mock_scr, 99, [], None, h)

        assert mock_scr.addstr.called


class TestHistoryInitDB:
    """Test that init_db() creates the required tables."""

    def test_init_db_creates_tables(self):
        """init_db should create scans, signals, and signal_trends tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            h = SignalHistory(db_path)
            h.init_db()

            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()

            assert 'scans' in tables
            assert 'signals' in tables
            assert 'signal_trends' in tables

    def test_init_db_creates_indexes(self):
        """init_db should create the required indexes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            h = SignalHistory(db_path)
            h.init_db()

            conn = sqlite3.connect(db_path)
            indexes = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()]
            conn.close()

            assert 'idx_signals_freq' in indexes
            assert 'idx_signals_scan' in indexes
            assert 'idx_trends_freq' in indexes

    def test_init_db_idempotent(self):
        """init_db should be safe to call multiple times."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            h = SignalHistory(db_path)
            h.init_db()
            h.init_db()  # Should not raise

            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            conn.close()
            assert len(tables) >= 3

    def test_record_scan_after_init_db(self):
        """record_scan should work after init_db is called."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            h = SignalHistory(db_path)
            h.init_db()

            signals = [
                {'freq': 680e6, 'peak': -30.0, 'avg': -35.0, 'std': 2.5, 'classification': 'sus'},
            ]
            scan_id = h.record_scan(signals, 'hackrf')
            assert scan_id is not None

            # Verify data was stored
            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            conn.close()
            assert count == 1

    def test_get_history_after_init_db(self):
        """get_history should return data after init_db + record_scan."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            h = SignalHistory(db_path)
            h.init_db()

            signals = [
                {'freq': 680e6, 'peak': -30.0, 'avg': -35.0, 'std': 2.5, 'classification': 'sus'},
            ]
            h.record_scan(signals, 'hackrf')

            rows = h.get_history(680.0, days=1)
            assert len(rows) == 1
            assert abs(rows[0]['freq_mhz'] - 680.0) < 0.1


class TestMainLoopKeyIntegration:
    """Test that the main loop handles all keys returned by _read_key."""

    def test_all_read_key_returns_handled(self):
        """Every command returned by _read_key must be handled in the main loop."""
        # These are all possible return values from _read_key
        all_commands = {
            'quit', 'rescan', 'mute', 'voice', 'interval_up', 'interval_down',
            'suppress', 'cursor_up', 'cursor_down', 'cursor_off',
            'details', 'export', 'log', 'history',
        }
        # Read the main loop source and check each command is handled
        rflord_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rflord.py')
        with open(rflord_path) as f:
            source = f.read()

        # Find the main loop section (between "while True:" for scan and the LOG section)
        loop_start = source.find('# Wait with non-blocking key reads')
        loop_end = source.find('# === LOGGING WITH WEEKLY ROTATION ===')
        assert loop_start > 0, "Could not find main loop"
        assert loop_end > 0, "Could not find LOG section"
        loop_source = source[loop_start:loop_end]

        for cmd in all_commands:
            assert f"key == '{cmd}'" in loop_source, \
                f"Command '{cmd}' returned by _read_key but NOT handled in main loop"

    def test_no_orphan_commands(self):
        """No commands handled in main loop that _read_key doesn't return."""
        all_read_key_commands = {
            'quit', 'rescan', 'mute', 'voice', 'interval_up', 'interval_down',
            'suppress', 'cursor_up', 'cursor_down', 'cursor_off',
            'details', 'export', 'log', 'history',
        }
        rflord_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rflord.py')
        with open(rflord_path) as f:
            source = f.read()

        loop_start = source.find('# Wait with non-blocking key reads')
        loop_end = source.find('# === LOGGING WITH WEEKLY ROTATION ===')
        loop_source = source[loop_start:loop_end]

        import re
        handled = set(re.findall(r"key == '(\w+)'", loop_source))
        # Remove 'quit' which is handled differently (return)
        orphan = handled - all_read_key_commands
        assert not orphan, f"Commands handled in loop but not returned by _read_key: {orphan}"


class TestTimeoutConsistency:
    """Verify timeout is consistent across all popup views."""

    def test_all_popups_restore_timeout_200(self):
        """Every popup that changes timeout must restore to 200ms."""
        rflord_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rflord.py')
        with open(rflord_path) as f:
            source = f.read()

        # Find all timeout() calls
        import re
        timeout_calls = re.findall(r'stdscr\.timeout\((\d+|-\d+)\)', source)

        # After every popup's getch, timeout should be restored to 200
        # Check that no popup restores to 100
        assert '100' not in timeout_calls, \
            f"Found timeout(100) — should be timeout(200). All calls: {timeout_calls}"

    def test_main_loop_timeout_200(self):
        """Main loop should use timeout(200) for non-blocking getch."""
        rflord_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rflord.py')
        with open(rflord_path) as f:
            source = f.read()

        # The main loop setup should have timeout(200)
        assert 'stdscr.timeout(200)  # getch() returns -1 after 200ms' in source
