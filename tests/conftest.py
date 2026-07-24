"""Shared fixtures for rflord tests."""
import sys
import os
import pytest

# Add project root to path so we can import rflord
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_signals():
    """Sample signals for testing table rendering."""
    return [
        # OK signals (FM broadcast)
        {'freq': 98e6, 'peak': -25.0, 'avg': -30.0, 'std': 5.0},
        {'freq': 101e6, 'peak': -30.0, 'avg': -35.0, 'std': 4.5},
        # WiFi
        {'freq': 2437e6, 'peak': -20.0, 'avg': -25.0, 'std': 6.0},
        # Suspicious (narrowband in DVB-T2 band)
        {'freq': 492e6, 'peak': -35.0, 'avg': -40.0, 'std': 1.2},
        # Danger (narrowband in DVB-T2 band, very close to known mux)
        {'freq': 472.5e6, 'peak': -40.0, 'avg': -45.0, 'std': 0.8},
        # Military
        {'freq': 255e6, 'peak': -30.0, 'avg': -35.0, 'std': 1.5},
        # Cellular
        {'freq': 950e6, 'peak': -15.0, 'avg': -20.0, 'std': 4.0},
    ]


@pytest.fixture
def artemis_db_empty():
    """Empty Artemis database."""
    return []


@pytest.fixture
def artemis_db_sample():
    """Sample Artemis database entries."""
    return [
        {
            'name': 'FM Broadcast',
            'freq_low': 88e6,
            'freq_high': 108e6,
            'modulation': 'FM',
            'bandwidth': '200000',
            'country': 'UA',
            'description': 'FM Radio Broadcasting',
        },
        {
            'name': 'GSM 900 DL',
            'freq_low': 935e6,
            'freq_high': 960e6,
            'modulation': 'GMSK',
            'bandwidth': '200000',
            'country': '',
            'description': 'GSM 900 Downlink',
        },
    ]
