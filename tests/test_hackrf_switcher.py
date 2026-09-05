"""Test HackRF/PortaPack/RTL-SDR device detection and mode switching."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeviceDetection:
    """Device detection based on lsusb output."""

    def test_hackrf_detected(self):
        def mock_sub_run(cmd, **kwargs):
            if any('hackrf_info' in str(c) for c in cmd):
                return MagicMock(returncode=0, stdout="Found HackRF\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")
        with patch('rflord.run_cmd') as mock_run, \
             patch('rflord.subprocess.run', side_effect=mock_sub_run):
            mock_run.return_value = "Bus 001 Device 005: ID 1d50:6089 OpenMoko, Inc. Great Scott Gadgets HackRF One SDR"
            from rflord import detect_device
            assert detect_device() == ["hackrf"]

    def test_rtlsdr_detected(self):
        def mock_sub_run(cmd, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="")
        with patch('rflord.run_cmd') as mock_run, \
             patch('rflord.subprocess.run', side_effect=mock_sub_run):
            mock_run.return_value = "Bus 001 Device 005: ID 0bda:2838 Realtek Semiconductor Corp. RTL2838"
            from rflord import detect_device
            assert detect_device() == ["rtlsdr"]

    def test_no_device_returns_empty(self):
        def mock_sub_run(cmd, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="")
        with patch('rflord.run_cmd') as mock_run, \
             patch('rflord.subprocess.run', side_effect=mock_sub_run):
            mock_run.return_value = "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"
            from rflord import detect_device
            assert detect_device() == []

    def test_both_devices_detected(self):
        def mock_sub_run(cmd, **kwargs):
            if any('hackrf_info' in str(c) for c in cmd):
                return MagicMock(returncode=0, stdout="Found HackRF\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")
        with patch('rflord.run_cmd') as mock_run, \
             patch('rflord.subprocess.run', side_effect=mock_sub_run):
            mock_run.return_value = (
                "Bus 001 Device 005: ID 1d50:6089 OpenMoko HackRF\n"
                "Bus 001 Device 006: ID 0bda:2838 Realtek RTL2838"
            )
            from rflord import detect_device
            assert detect_device() == ["hackrf", "rtlsdr"]


class TestPortaPackSwitch:
    """PortaPack (1d50:6018) to HackRF mode switch logic."""

    def test_portapack_tries_acm1_first(self):
        with patch('rflord.run_cmd') as mock_run, \
             patch('serial.Serial') as mock_serial, \
             patch('time.sleep'):
            # lsusb: PortaPack, then after switch: HackRF
            mock_run.side_effect = [
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",
                "Bus 001 Device 005: ID 1d50:6089 OpenMoko HackRF",
            ]
            mock_conn = MagicMock()
            mock_serial.return_value = mock_conn

            from rflord import detect_device
            detect_device()

            calls = mock_serial.call_args_list
            assert len(calls) >= 1
            first_port = str(calls[0][0][0]) if calls[0][0] else str(calls[0][1].get('port', ''))
            assert 'ACM1' in first_port, f"First port should be ACM1, got {first_port}"

    def test_portapack_fallback_to_acm0(self):
        with patch('rflord.run_cmd') as mock_run, \
             patch('serial.Serial') as mock_serial, \
             patch('time.sleep'):
            mock_run.side_effect = [
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",
                "Bus 001 Device 005: ID 1d50:6089 OpenMoko HackRF",
            ]
            def side_effect(port, *args, **kwargs):
                if 'ACM1' in str(port):
                    raise Exception("Permission denied")
                return MagicMock()
            mock_serial.side_effect = side_effect

            from rflord import detect_device
            detect_device()

            assert mock_serial.call_count >= 2
            ports = [str(c[0][0]) for c in mock_serial.call_args_list]
            assert any('ACM0' in p for p in ports), f"Should try ACM0 after ACM1 fails, got {ports}"

    def test_portapack_switch_sends_hackrf_command(self):
        with patch('rflord.run_cmd') as mock_run, \
             patch('serial.Serial') as mock_serial, \
             patch('time.sleep'):
            mock_run.side_effect = [
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",
                "Bus 001 Device 005: ID 1d50:6089 OpenMoko HackRF",
            ]
            mock_conn = MagicMock()
            mock_serial.return_value = mock_conn

            from rflord import detect_device
            detect_device()

            write_calls = mock_conn.write.call_args_list
            written = [str(c) for c in write_calls]
            assert any('hackrf' in w for w in written), f"Should send hackrf command, got {written}"

    def test_portapack_switch_checks_result(self):
        """After switch fails, should check lsusb again (retry), then return None."""
        with patch('rflord.run_cmd') as mock_run, \
             patch('serial.Serial') as mock_serial, \
             patch('time.sleep'):
            # Initial lsusb -> after ACM1 -> after ACM0 -> in else branch -> after usbreset
            mock_run.side_effect = [
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",  # initial
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",  # after ACM1
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",  # after ACM0
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",  # else retry
                "Bus 001 Device 005: ID 1d50:6018 OpenMoko PortaPack",  # after usbreset
            ]
            mock_conn = MagicMock()
            mock_serial.return_value = mock_conn

            from rflord import detect_device
            result = detect_device()
            assert result == [], f"Should return empty list when switch fails, got {result}"


class TestRTLSDRSweep:
    """RTL-SDR sweep function."""

    def test_rtlsdr_sweep_command(self):
        with patch('rflord.run_cmd') as mock_run:
            mock_run.return_value = ""
            from rflord import rtlsdr_sweep
            rtlsdr_sweep(88, 108)

            cmd = mock_run.call_args[0][0]
            assert 'rtl_power' in cmd
            assert '88M' in cmd
            assert '108M' in cmd

    def test_hackrf_sweep_command(self):
        with patch('rflord.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="2024-01-01, 12:00:00, 88000000, 108000000, 2000000, 100, -30.0\n", stderr="")
            from rflord import hackrf_sweep
            hackrf_sweep(88, 108)

            cmd = mock_run.call_args[0][0]
            assert 'hackrf_sweep' in cmd
            assert '-f 88:108' in cmd
