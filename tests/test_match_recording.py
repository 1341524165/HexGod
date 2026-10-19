"""Replay a real logged panel through the production selection/journal protocol.

This checks correlation and confirmation after input delivery; it deliberately
does not claim to reproduce delivery of physical game clicks by Windows.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Backend
from match_journal import MatchJournal


class MatchRecordingTests(unittest.TestCase):
    def test_oct19_xinzhao_click_confirmation_drives_actual_synergy_candidates(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/xinzhao-spell-on-hit-panel.json').read_text('utf-8'))
        geometry, context = fixture['geometry'], fixture['capture']
        backend = Backend()
        with tempfile.TemporaryDirectory() as directory:
            backend.ocr.journal = MatchJournal(Path(directory))
            baseline = backend.build_path('德邦总管', [], [], True)
            self.assertEqual(baseline['synergyItems'], [])
            box = geometry['hitboxes'][1]
            pending = backend.dispatch({'action':'click', 'x':(box[0]+box[2])//2, 'y':(box[1]+box[3])//2,
                'hwnd':geometry['windowHwnd'], 'atMs':geometry['capturedAtMs']+100, 'geometry':geometry,
                'contextId':19, 'sessionKey':'oct19-replay', 'inputSource':'button_state'})
            self.assertEqual(pending['pending'], '虚幻武器')
            blanks = [{'capture_context':dict(context,timestamp_ms=geometry['capturedAtMs']+delta),
                       'frame_quality':{'probable_augment_frame':False}, 'anchor_detection':{'detected':False}}
                      for delta in (700,1300)]
            request = {'action':'ocr','hero':'德邦总管','contextId':19,'sessionKey':'oct19-replay'}
            with patch.object(backend.ocr, '_scan', side_effect=blanks):
                self.assertIsNone(backend.dispatch(request)['autoSelection'])
                selected = backend.dispatch(request)['autoSelection']
            self.assertEqual(selected, '虚幻武器')
            plan = backend.dispatch({'action':'build_path','hero':'德邦总管','owned':[selected],
                                     'itemIds':[], 'itemsKnown':True})
            self.assertIn('spell_on_hit',plan['buildProfile']['features'])
            self.assertTrue(plan['synergyItems'])
            self.assertIn(3153, [i['id'] for i in plan['synergyItems']])
            summary = json.loads((Path(directory)/'oct19-replay/summary.json').read_text('utf-8'))
            self.assertEqual(summary['confirmed'], ['虚幻武器'])
            self.assertEqual(summary['clicks'], 1)
            self.assertIn('button_state',(Path(directory)/'oct19-replay/events.jsonl').read_text('utf-8'))

    def test_production_record_from_real_panel_geometry(self):
        fixture_path = Path(__file__).parent / 'fixtures' / 'selection-panel.json'
        frame = json.loads(fixture_path.read_text('utf-8'))
        backend = Backend(use_remote=True)
        geometry = frame['geometry']
        captured = geometry['capturedAtMs']
        context = frame['capture']
        shown = {'capture_context': context, 'titleRegionCards': True,
                 'frame_quality': {'probable_augment_frame': True},
                 'anchor_detection': {'detected': True},
                 'slot_candidates': [[{'text': name, 'confidence': 99, 'title_like': True,
                                      'title_region': True}] for name in geometry['names']]}
        blanks = [{'capture_context': dict(context, timestamp_ms=captured+delta),
                   'frame_quality': {'probable_augment_frame': False},
                   'anchor_detection': {'detected': False}} for delta in (600, 1200)]
        with tempfile.TemporaryDirectory() as directory:
            backend.ocr.journal = MatchJournal(Path(directory))
            request = {'action': 'ocr', 'hero': '齐天大圣', 'contextId': 1, 'sessionKey': 'replay'}
            with patch.object(backend.ocr, '_scan', side_effect=[shown, *blanks]), \
                    patch('ocr_service.live_geometry', side_effect=[geometry, None, None]):
                self.assertTrue(backend.dispatch(request)['autoTrackingReady'])
                box = geometry['hitboxes'][1]
                click = backend.dispatch({'action': 'click', 'contextId': 1, 'sessionKey': 'replay',
                                          'geometry': geometry, 'hwnd': geometry['windowHwnd'],
                                          'atMs': captured+100, 'x': (box[0]+box[2])//2,
                                          'y': (box[1]+box[3])//2})
                self.assertEqual(click['pending'], geometry['names'][1])
                self.assertIsNone(backend.dispatch(request)['autoSelection'])
                self.assertEqual(backend.dispatch(request)['autoSelection'], geometry['names'][1])
            summary = json.loads((Path(directory)/'replay'/'summary.json').read_text('utf-8'))
            self.assertEqual(summary['clicks'], 1)
            self.assertEqual(summary['confirmed'], [geometry['names'][1]])
            events = (Path(directory)/'replay'/'events.jsonl').read_text('utf-8')
            self.assertIn('selection_confirmed', events)


if __name__ == '__main__':
    unittest.main()
