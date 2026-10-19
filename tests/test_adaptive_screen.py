"""Regression cases using the unmodified upstream game capture as UI pixels."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend
from title_reader import detect_card_layout, read_titles
from match_journal import MatchJournal

FIXTURE = ROOT / 'tests/fixtures/augment-ocr/full-three-cards.png'


@unittest.skipUnless(FIXTURE.exists(), 'Reference screenshot unavailable')
class AdaptiveScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend()
        cls.source = Image.open(FIXTURE).convert('RGB')

    def transformed(self, canvas, scale, shift=(0, 0)):
        # Move and scale the actual card group independently of the capture
        # bounds, as game UI scale and letterboxing do in practice.
        group = self.source.crop((420, 140, 1490, 790))
        group = group.resize((round(group.width*scale), round(group.height*scale)),
                             Image.Resampling.LANCZOS)
        frame = Image.new('RGB', canvas, (23, 31, 29))
        x = (canvas[0]-group.width)//2 + shift[0]
        y = (canvas[1]-group.height)//2 + shift[1]
        frame.paste(group, (x, y))
        return frame, [(x+round((center-420)*scale)) for center in (604, 960, 1314)]

    def test_cards_follow_ui_scale_position_and_ultrawide_canvas(self):
        for size, scale, shift in [((1920, 1080), .75, (0, 0)),
                                   ((1920, 1080), 1.12, (30, -55)),
                                   ((2560, 1440), 1.0, (160, 90)),
                                   ((3440, 1440), 1.0, (400, 90))]:
            with self.subTest(size=size, scale=scale, shift=shift):
                frame, expected = self.transformed(size, scale, shift)
                cards = detect_card_layout(frame)
                self.assertIsNotNone(cards)
                self.assertEqual(len(cards), 3)
                for rect, center in zip(cards, expected):
                    self.assertLess(abs((rect[0]+rect[2])/2-center), 28)

    def test_scaled_moved_titles_are_read_in_correct_order(self):
        frame, _ = self.transformed((1920, 1080), .75, (0, 0))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frame.png'
            frame.save(path)
            result = self.backend.recognize({'hero': '祖安怒兽', 'image': str(path)})
        self.assertEqual([c['name'] for c in result['choices']],
                         ['未收录：裁决使', '超凡邪恶', '神射法师'])

    def test_partial_outline_without_readable_title_is_not_a_panel(self):
        source = FIXTURE.parent/'partial-one-card.png'
        with tempfile.TemporaryDirectory() as directory:
            result = read_titles(source, directory, lambda paths: [{'lines': []} for _ in paths])
        self.assertFalse(result['titleRegionCards'])

    def test_window_offset_translates_detected_card_and_reroll_hits(self):
        frame, expected = self.transformed((3440, 1440), 1.0, (400, 90))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frame.png'
            frame.save(path)
            backend = Backend()
            backend.ocr.journal = MatchJournal(Path(directory)/'logs')
            context = {'foreground_matches_lol': True,
                       'lol_window_bbox': [4000, 200, 7440, 1640],
                       'lol_window_hwnd': 41, 'timestamp_ms': 10000}
            with patch('ocr_service.capture_screen', return_value=path), \
                 patch('ocr_service.capture_context_for_image', return_value=context):
                result = backend.recognize({'hero': '祖安怒兽', 'sessionKey': 'offset'})
        geometry = result['trackingGeometry']
        self.assertIsNotNone(geometry)
        for box, reroll, center in zip(geometry['hitboxes'], geometry['rerollHitboxes'], expected):
            self.assertLess(abs((box[0]+box[2])/2-(4000+center)), 28)
            self.assertGreater(reroll[1], box[3])


if __name__ == '__main__':
    unittest.main()
