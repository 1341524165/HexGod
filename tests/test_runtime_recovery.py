import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Backend
from augment_pipeline import CaptureSkipped
from lcu import auth_from_paths


class RuntimeRecoveryTests(unittest.TestCase):
    def test_foreground_skip_is_not_an_ocr_error(self):
        backend = Backend(use_remote=False)
        backend.ocr.worker = Mock()
        meta = {'timestamp_ms': 1000, 'lol_window_hwnd': 55, 'foreground_matches_lol': False}
        with patch('ocr_service.capture_screen', side_effect=CaptureSkipped('assistant_window_foreground', Path('unused'), meta)):
            result = backend.recognize({'hero': '异画师'})
        self.assertTrue(result['captureSkipped'])
        self.assertEqual(result['captureReason'], 'assistant_window_foreground')
        self.assertFalse(result['augmentPanelVisible'])
        self.assertIsNone(result['autoSelection'])

    def test_missing_window_is_a_recoverable_wait(self):
        backend = Backend(use_remote=False)
        backend.ocr.worker = Mock()
        with patch('ocr_service.capture_screen', side_effect=RuntimeError('lol_window_not_found')), patch('ocr_service.capture_context_for_image', return_value={}):
            result = backend.recognize({'hero': '异画师'})
        self.assertTrue(result['captureSkipped'])

    def test_unexpected_capture_error_remains_visible(self):
        backend = Backend(use_remote=False)
        with tempfile.TemporaryDirectory() as directory, patch('ocr_service.capture_screen', side_effect=RuntimeError('capture failure')):
            with self.assertRaisesRegex(RuntimeError, 'capture failure'):
                backend.ocr._scan({}, directory, False)

    def test_process_path_lockfile_works_without_command_line_access(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'LeagueClient').mkdir()
            (base / 'LeagueClient' / 'lockfile').write_text('LeagueClient:42:2999:local-test:https')
            self.assertEqual(auth_from_paths([str(base / 'Game' / 'League of Legends.exe')]), (2999, 'local-test'))
            self.assertIsNone(auth_from_paths([str(base / 'unrelated' / 'nested' / 'app.exe')]))
