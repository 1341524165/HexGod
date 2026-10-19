"""Run against the unmodified upstream fixtures when the reference clone exists.

Fixtures are vendored unchanged under tests/fixtures/augment-ocr.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend import Backend
from PIL import Image

FIXTURES=ROOT/'tests/fixtures/augment-ocr'


@unittest.skipUnless((FIXTURES/'manifest.json').exists(),'Reference fixture checkout not present')
class ReferenceOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.backend=Backend()

    def scan(self,name):
        return self.backend.recognize({'hero':'赏金猎人','image':str(FIXTURES/name)})

    def test_full_slots_and_unlisted_augment_do_not_invent_win_rate(self):
        result=self.scan('full-three-cards.png')
        self.assertEqual([c['name'] for c in result['choices']],['未收录：裁决使','超凡邪恶','神射法师'])
        unknown=result['choices'][0]
        self.assertFalse(unknown['hasStats'])
        self.assertFalse(unknown['recommended'])
        self.assertEqual(self.backend.normalize_owned([unknown['name']]),[unknown['name']])
        self.assertEqual(result['ocrDebug']['preset'],'title-regions')

    def test_tooltip_occlusion_preserves_one_card_in_its_actual_slot(self):
        result=self.scan('partial-one-card.png')
        self.assertEqual(result['matchedCount'],1)
        self.assertEqual([c['name'] for c in result['choices']],['不祥契约','待识别','待识别'])
        self.assertTrue(result['augmentPanelVisible'])

    def test_description_activity_cannot_be_promoted_to_unknown_augments(self):
        self.assertEqual(self.scan('no-match-title-activity.png')['matchedCount'],0)

    def test_reference_capture_scaled_to_720p(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'scaled.png'
            with Image.open(FIXTURES/'partial-one-card.png') as image:
                image.resize((1280,720),Image.Resampling.LANCZOS).save(path)
            result=self.backend.recognize({'hero':'赏金猎人','image':str(path)})
            self.assertEqual(result['choices'][0]['name'],'不祥契约')
            self.assertFalse(result['choices'][1]['matched'])

    def test_live_fast_path_supplies_click_geometry_and_rejects_reroll(self):
        self.backend.ocr.reset()
        context={'foreground_matches_lol':True,'lol_window_bbox':[100,200,2020,1280],
                 'lol_window_hwnd':41,'timestamp_ms':10000}
        with patch('ocr_service.capture_screen',return_value=FIXTURES/'full-three-cards.png'), \
             patch('ocr_service.capture_context_for_image',return_value=context):
            result=self.backend.recognize({'hero':'赏金猎人'})
        geometry=result['trackingGeometry']
        self.assertEqual(geometry['windowHwnd'],41)
        tracker=self.backend.ocr.selection_tracker
        self.assertIsNone(tracker.observe_click(1060,950,41,10100)) # Reroll below card.
        self.assertEqual(tracker.observe_click(1060,670,41,10100),'超凡邪恶')

    def test_partial_cache_requires_identical_slot_pixels_and_expires(self):
        result=self.scan('full-three-cards.png')
        service=self.backend.ocr
        service.reset(); service.retain_slots(result,1000)
        blank=copy.deepcopy(result)
        blank['choices'][1].update(name='待识别',matched=False)
        service.retain_slots(blank,1500)
        self.assertEqual(blank['choices'][1]['name'],'超凡邪恶')
        self.assertFalse(any(c['recommended'] for c in blank['choices']))
        changed=copy.deepcopy(result)
        changed['choices'][1].update(name='待识别',matched=False)
        changed['titleFingerprints'][1]='new'
        service.retain_slots(changed,1600)
        self.assertFalse(changed['choices'][1]['matched'])
        service.reset(); service.retain_slots(result,1000)
        expired=copy.deepcopy(result); expired['choices'][1]['matched']=False
        service.retain_slots(expired,2500)
        self.assertFalse(expired['choices'][1]['matched'])
