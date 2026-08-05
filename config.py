import os
import yaml

DEFAULTS = {
    'scan': {
        'bands': [
            {'start': 2400000000, 'stop': 2500000000, 'step': 1000000},
            {'start': 5150000000, 'stop': 5850000000, 'step': 1000000},
            {'start': 860000000,  'stop': 960000000,  'step': 1000000},
            {'start': 1574000000, 'stop': 1576000000, 'step': 100000},
            {'start': 1710000000, 'stop': 1785000000, 'step': 100000},
            {'start': 1805000000, 'stop': 1880000000, 'step': 100000},
            {'start': 2110000000, 'stop': 2170000000, 'step': 100000},
        ],
        'interval': 30,
        'n_sweeps': 3,
    },
    'voice': {
        'enabled': True,
        'threshold': -50,
        'voice_name': 'en-US-SteffanNeural',
        'rate': '-15%',
        'hal_effect': '~/.local/bin/hal-effect.sh',
    },
    'display': {
        'theme': 'dark',
        'max_rows': 19,
    },
    'suppress': {
        'targets': [
            {'name': 'cellular', 'freq': 900000000,  'bw': 80000000},
            {'name': 'cellular', 'freq': 1800000000, 'bw': 100000000},
            {'name': 'cellular', 'freq': 2100000000, 'bw': 60000000},
            {'name': 'bluetooth', 'freq': 2440000000, 'bw': 80000000},
            {'name': 'gps',      'freq': 1575420000, 'bw': 2000000},
        ],
    },
    'history': {
        'enabled': True,
        'db_path': '~/.local/share/rflord/signals.db',
        'max_days': 30,
    },
    'export': {
        'enabled': False,
        'format': 'csv',
        'path': '~/.local/share/rflord/exports/',
    },
    'web': {
        'enabled': True,
        'port': 8080,
    },
    'blacklist': {
        'file': '~/.config/rflord/ignore.conf',
    },
    'scan_acceleration': {
        'enabled': True,
        'skip_after_empty': 3,
    },
}


def load_config(path=None):
    if path is None:
        path = os.path.expanduser('~/.config/rflord/config.yaml')
    config = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
            config.update(user)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            yaml.dump(DEFAULTS, f, default_flow_style=False)
    return config
