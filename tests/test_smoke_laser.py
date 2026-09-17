"""Tests for signal identification — no wild guesses.

Only deterministic identifications allowed:
- Satellite constellations (Iridium, Inmarsat, Globalstar)
- Radar bands (X-band)
- Generic band names (ISM433, ISM868, WiFi, GSM, etc.)

Previously had smoke detector/laser perimeter entries — removed because
433 MHz and 868 MHz ISM bands are used by many devices (key fobs,
garage doors, weather stations, etc.). Can't identify as smoke detector
without signal analysis (bandwidth, modulation, duty cycle).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spy_db import identify_spy_device
from rflord import get_signal_type

class TestDeterministicIdentification:
    """Only truly deterministic identifications should work."""

    def test_iridium(self):
        """Iridium sat phone — unique frequency band."""
        name, icon, threat = identify_spy_device(1620.0, 1.0)
        assert name == "Iridium Sat Phone"
        assert icon == "🛰"

    def test_inmarsat(self):
        """Inmarsat — unique frequency band."""
        name, icon, threat = identify_spy_device(1990.0, 1.0)
        assert name == "Inmarsat Sat Phone"
        assert icon == "🛰"

    def test_globalstar(self):
        """Globalstar — unique frequency band."""
        name, icon, threat = identify_spy_device(1640.0, 1.0)
        assert name == "Globalstar Sat Phone"
        assert icon == "🛰"

    def test_xband_radar(self):
        """X-band radar — unique frequency band."""
        name, icon, threat = identify_spy_device(10525.0, 1.0)
        assert name == "X-band Radar"
        assert icon == "🚨"


class TestGenericBandNames:
    """Frequencies should show generic band names, not device guesses."""

    def test_433mhz_ism(self):
        """433 MHz ISM — could be key fob, smoke detector, weather station."""
        sig_type = get_signal_type(433.92, 0, 0, 1.0)
        assert sig_type == "ISM433"

    def test_868mhz_ism(self):
        """868 MHz ISM — could be LoRa, Z-Wave, smoke detector."""
        sig_type = get_signal_type(868.95, 0, 0, 1.0)
        assert sig_type == "ISM868"

    def test_915mhz_ism(self):
        """915 MHz ISM band."""
        sig_type = get_signal_type(915.0, 0, 0, 1.0)
        assert sig_type == "ISM915"

    def test_5800mhz_ism(self):
        """5.8 GHz ISM — could be FPV, WiFi, drone video."""
        sig_type = get_signal_type(5800.0, 0, 0, 1.0)
        assert sig_type == "5.8GHz ISM"

    def test_gsm900(self):
        """GSM900 downlink band."""
        sig_type = get_signal_type(945.0, 0, 0, 1.0)
        assert sig_type == "GSM900"

    def test_military(self):
        """Military UHF band."""
        sig_type = get_signal_type(300.0, 0, 0, 1.0)
        assert sig_type == "Mil UHF"

    def test_fm_radio(self):
        """FM broadcast band."""
        sig_type = get_signal_type(99.5, 0, 0, 1.0)
        assert sig_type == "FM Radio"

    def test_air_band(self):
        """Aviation band."""
        sig_type = get_signal_type(120.0, 0, 0, 1.0)
        assert sig_type == "Air Band"

    def test_wifi_24ghz(self):
        """WiFi 2.4 GHz channel."""
        sig_type = get_signal_type(2412.0, 0, 0, 1.0)
        assert sig_type == "WiFi Ch1"

    def test_2m_ham(self):
        """2m amateur radio band."""
        sig_type = get_signal_type(145.0, 0, 0, 1.0)
        assert sig_type == "2m Ham"


class TestNoFalsePositives:
    """Verify no wild guesses remain in spy_db."""

    def test_fm_not_bug(self):
        """FM broadcast should not be identified as 'FM Band Bug'."""
        name, icon, threat = identify_spy_device(99.5, 1.0)
        assert name is None  # Not a spy device

    def test_wifi_not_camera(self):
        """WiFi should not be identified as 'WiFi Spy Camera'."""
        name, icon, threat = identify_spy_device(2412.0, 1.0)
        assert name is None

    def test_gsm_not_imsi_catcher(self):
        """GSM should not be identified as 'IMSI Catcher'."""
        name, icon, threat = identify_spy_device(945.0, 1.0)
        assert name is None

    def test_ism900_not_hidden_camera(self):
        """ISM 900 MHz should not be identified as 'Hidden Camera'."""
        name, icon, threat = identify_spy_device(915.0, 1.0)
        assert name is None
