"""Tests for smoke detector and laser perimeter alarm detection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from spy_db import identify_spy_device

class TestSmokeDetectors:
    """Smoke detectors must be detected as critical (threat=0)."""

    def test_433mhz(self):
        name, icon, threat = identify_spy_device(433.92, 1.0)
        assert name == "Wireless Smoke Detector"
        assert icon == "🔥"
        assert threat == 0

    def test_315mhz(self):
        name, icon, threat = identify_spy_device(315.0, 1.0)
        assert name == "Wireless Smoke Detector 315"
        assert threat == 0

    def test_345mhz(self):
        name, icon, threat = identify_spy_device(345.0, 1.0)
        assert name == "Wireless Smoke Detector 345"
        assert threat == 0

    def test_868_95(self):
        name, icon, threat = identify_spy_device(868.95, 1.0)
        assert name == "Wireless Smoke Detector 868"
        assert threat == 0

    def test_869_26(self):
        name, icon, threat = identify_spy_device(869.26, 1.0)
        assert name == "Wireless Smoke Detector 869"
        assert threat == 0


class TestLaserPerimeter:
    """Laser/IR perimeter alarms must be detected as critical (threat=0)."""

    def test_433_5(self):
        name, icon, threat = identify_spy_device(433.5, 1.0)
        assert name == "Laser Perimeter Alarm"
        assert icon == "🔴"
        assert threat == 0

    def test_434_2(self):
        name, icon, threat = identify_spy_device(434.2, 1.0)
        assert name == "Laser Perimeter Alarm"
        assert threat == 0

    def test_868_5(self):
        name, icon, threat = identify_spy_device(868.5, 1.0)
        # 868.5 is in Smoke Detector range (868.5-869.0), more specific match
        assert name == "Wireless Smoke Detector 868"
        assert threat == 0

    def test_868_3(self):
        name, icon, threat = identify_spy_device(868.3, 1.0)
        assert name == "IR Perimeter Beam 868"
        assert threat == 0

    def test_869_1(self):
        name, icon, threat = identify_spy_device(869.1, 1.0)
        assert name == "IR Perimeter Beam 868"
        assert threat == 0