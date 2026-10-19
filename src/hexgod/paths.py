"""The only Python owner of project, resource and writable runtime paths."""
import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get('HEXGOD_SOURCE_DIR', Path(__file__).resolve().parents[2])).resolve()
RESOURCE_DIR = PROJECT_ROOT / 'resources'
RULES_DIR = RESOURCE_DIR / 'rules'
PRESET_PATH = RESOURCE_DIR / 'presets/augment_presets.json'
LEGACY_DIR = RESOURCE_DIR / 'legacy'
DATA_DIR = LEGACY_DIR / 'data'
SNAPSHOT_DIR = RESOURCE_DIR / 'snapshots'
CLIENT_DATA_DIR = RESOURCE_DIR / 'client-data'
MODEL_DIR = PROJECT_ROOT / 'assets/models'
CACHE_DIR = PROJECT_ROOT / 'artifacts/cache'
REPORT_DIR = PROJECT_ROOT / 'artifacts/reports'
LOG_DIR = PROJECT_ROOT / 'var/logs'
STATE_DIR = PROJECT_ROOT / 'var/state'


def ensure_runtime_directories():
    for directory in (CACHE_DIR, REPORT_DIR, LOG_DIR, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
