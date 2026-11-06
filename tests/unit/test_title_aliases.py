import unittest

from hexgod.paths import CLIENT_DATA_DIR
from hexgod.recommendation.aramgg import AramggEngine
from hexgod.vision.matching import corrected_title
from hexgod.vision.service import OcrService


class TitleAliasTests(unittest.TestCase):
    def test_repeated_glyph_error_matches_full_title_only(self):
        names = ['喂呜喂呜', '咏叹奏鸣']
        self.assertEqual(corrected_title('喂鸣喂鸣', names), '喂呜喂呜')
        self.assertIsNone(corrected_title('说明里写了喂鸣喂鸣', names))

    def test_real_catalog_ocr_does_not_emit_unknown_title(self):
        service = OcrService(AramggEngine(folder=CLIENT_DATA_DIR / '16.21.5'))
        payload = dict(titleRegionCards=True, frame_quality={'probable_augment_frame': True},
            slot_candidates=[[dict(text=name, confidence=99, title_like=True, title_region=True,
                                   image_quality=100, variant_index=0)]
                             for name in ('喂鸣喂鸣', '急急小子', '神圣干预')])
        result, _ = service._interpret_ocr(payload, '天启者', [], 'screen')
        self.assertEqual(result['choices'][0]['name'], '喂呜喂呜')
        self.assertTrue(result['choices'][0]['matched'])


if __name__ == '__main__':
    unittest.main()
