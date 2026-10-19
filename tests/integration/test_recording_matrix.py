"""Production replay coverage; simulated clicks are never called live evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hexgod.api.backend import Backend
from hexgod.paths import PROJECT_ROOT
from hexgod.tracking.journal import MatchJournal


class RecordingMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend()
        cls.cases = json.loads((PROJECT_ROOT/'tests/fixtures/recordings/selection-cases.json').read_text('utf-8'))

    def replay(self, case, hero, directory, context_id, deliver_click=True):
        backend = self.backend
        backend.ocr.journal = MatchJournal(Path(directory), evidence_kind='replay')
        backend.ocr.ensure_context(context_id)
        geometry, capture = case['geometry'], case['capture']
        at = geometry['capturedAtMs']
        backend.ocr.selection_tracker.observe_frame(geometry, foreground_game=True,
            frame_detected=True, hwnd=geometry['windowHwnd'], at_ms=at)
        session = 'replay-'+str(context_id)
        if deliver_click:
            slot = geometry['names'].index(case['augment'])
            box = geometry['hitboxes'][slot]
            result = backend.dispatch({'action':'click','contextId':context_id,'sessionKey':session,
                'hero':hero,'evidenceKind':'replay','geometry':geometry,'hwnd':geometry['windowHwnd'],
                'x':(box[0]+box[2])//2,'y':(box[1]+box[3])//2,'atMs':at+100,'inputSource':'button_state'})
            self.assertEqual(result['pending'],case['augment'])
        blanks = [{'capture_context':dict(capture,timestamp_ms=at+delta),
                   'frame_quality':{'probable_augment_frame':False},
                   'anchor_detection':{'detected':False}} for delta in (700,1300)]
        request = {'action':'ocr','contextId':context_id,'sessionKey':session,'hero':hero,'evidenceKind':'replay'}
        with patch.object(backend.ocr,'_scan',side_effect=blanks):
            self.assertIsNone(backend.dispatch(request)['autoSelection'])
            selected = backend.dispatch(request)['autoSelection']
        summary = json.loads((Path(directory)/session/'summary.json').read_text('utf-8'))
        self.assertEqual(summary['evidenceKind'],'replay')
        self.assertEqual(summary['hero'],hero)
        self.assertEqual(summary['confirmed'],[case['augment']] if deliver_click else [])
        return selected

    def test_four_recorded_panels_cover_different_dates_and_augment_mechanisms(self):
        for index,case in enumerate(self.cases):
            with self.subTest(session=case['session'],augment=case['augment']), tempfile.TemporaryDirectory() as directory:
                selected = self.replay(case,case['hero'],directory,1000+index)
                plan = self.backend.build_path(case['hero'],[selected],[],True)
                self.assertTrue(plan['synergyItems'])
                if case['augment']=='升级：死亡之舞':
                    self.assertEqual([i['id'] for i in plan['synergyItems']],[6333])
                if case['augment']=='钢化你心':
                    self.assertIn(3084,[i['id'] for i in plan['synergyItems']])

    def test_spell_on_hit_confirmation_and_synergy_for_every_catalog_hero(self):
        for index,hero in enumerate(self.backend.engine.heroes):
            with self.subTest(hero=hero), tempfile.TemporaryDirectory() as directory:
                selected = self.replay(self.cases[0],hero,directory,2000+index)
                plan = self.backend.build_path(hero,[selected],[],True)
                self.assertIn('spell_on_hit',plan['buildProfile']['features'])
                self.assertTrue(plan['synergyItems'],hero)
                self.assertTrue(all(i['winRate'] is None and i['games'] is None for i in plan['synergyItems']))

    def test_observed_cards_without_input_do_not_invent_a_selection(self):
        for index,case in enumerate(self.cases):
            with self.subTest(session=case['session']), tempfile.TemporaryDirectory() as directory:
                self.assertIsNone(self.replay(case,case['hero'],directory,3000+index,deliver_click=False))
