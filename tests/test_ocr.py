"""Runs the actual bundled model on synthetic fixtures, without accessing the screen."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import copy
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend
from PIL import Image, ImageDraw, ImageFont

NAMES = ["老练狙神", "暴击飞弹", "双生火焰"]


def make_fixture(path, size=(1920, 1080), blank=False, title_y=435):
    image = Image.new("RGB", (1920, 1080), (25, 31, 43))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 31)
    for x, name in zip([490, 830, 1170], NAMES):
        draw.rounded_rectangle((x - 20, 350, x + 280, 740), radius=12,
                               fill=(35, 46, 62), outline=(104, 124, 154), width=2)
        if not blank:
            bounds = draw.textbbox((0, 0), name, font=font)
            left = x + (260 - (bounds[2] - bounds[0])) / 2
            draw.text((left, title_y - bounds[1]), name, font=font, fill=(245, 240, 220))
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    image.save(path)
    image.close()


class RealOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)

    def test_three_titles_with_real_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "three.png"
            make_fixture(path)
            result = self.backend.recognize({"hero": "赏金猎人", "image": str(path)})
            self.assertEqual([c["name"] for c in result["choices"]], NAMES)
            self.assertEqual(result["source"], "image")
            self.assertTrue(result["complete"])

    def test_scaled_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scaled.png"
            make_fixture(path, (1280, 720))
            result = self.backend.recognize({"hero": "赏金猎人", "image": str(path)})
            self.assertEqual([c["name"] for c in result["choices"]], NAMES)

    def test_recognizes_shifted_title_layout_without_manual_preset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scaled-ui.png"
            make_fixture(path, title_y=405)
            result = self.backend.recognize({"hero": "赏金猎人", "image": str(path)})
            self.assertEqual([c["name"] for c in result["choices"]], NAMES)

    def test_falls_back_to_alternate_title_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shifted-far.png"
            make_fixture(path, title_y=385)
            result = self.backend.recognize({"hero": "赏金猎人", "image": str(path)})
            self.assertEqual([c["name"] for c in result["choices"]], NAMES)
            self.assertEqual(result["ocrDebug"]["preset"], "1920x1080@125")

    def test_variant_can_replace_wrong_primary_ocr(self):
        candidate = lambda text, confidence, variant: {
            "text": text, "confidence": confidence, "variant_index": variant,
            "title_like": True, "image_quality": 100,
        }
        payload = {
            "preset": {"key": "1920x1080@100"},
            "frame_quality": {"probable_augment_frame": True, "trusted_title_region_count": 3},
            "slot_candidates": [
                [candidate("完全错误", 99, 0), candidate("老练狙神", 90, 1)],
                [candidate("暴击飞弹", 95, 0)],
                [candidate("双生火焰", 95, 0)],
            ],
        }
        result, _ = self.backend._interpret_ocr(payload, "赏金猎人", None, "image")
        self.assertEqual([c["name"] for c in result["choices"]], NAMES)

    def test_known_low_confidence_title_still_has_win_rate(self):
        payload = {
            "frame_quality": {"probable_augment_frame": True},
            "slot_candidates": [[{"text": name, "confidence": 12,
                                  "variant_index": 0, "title_like": True,
                                  "image_quality": 50}] for name in NAMES],
        }
        result, _ = self.backend._interpret_ocr(payload, "赏金猎人", None, "screen")
        self.assertEqual([c["name"] for c in result["choices"]], NAMES)
        self.assertTrue(all(c["hasStats"] for c in result["choices"]))

    def test_rejects_text_when_frame_is_not_augment_selection(self):
        payload = {
            "frame_quality": {"probable_augment_frame": False},
            "slot_candidates": [[{"text": name, "confidence": 99,
                                  "variant_index": 0, "title_like": True,
                                  "image_quality": 100}] for name in NAMES],
        }
        result, _ = self.backend._interpret_ocr(payload, "赏金猎人", None, "screen")
        self.assertEqual(result["matchedCount"], 0)
        self.assertFalse(any(c["recommended"] for c in result["choices"]))

    def test_duplicate_ocr_name_does_not_enable_click_tracking(self):
        payload = {
            "frame_quality": {"probable_augment_frame": True},
            "slot_candidates": [[{"text": name, "confidence": 99,
                                  "variant_index": 0, "title_like": True,
                                  "image_quality": 100}] for name in
                                ("老练狙神", "老练狙神", "双生火焰")],
        }
        result, _ = self.backend._interpret_ocr(payload, "赏金猎人", None, "screen")
        self.assertEqual(result["matchedCount"], 2)

    def test_blank_image_has_no_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank.png"
            make_fixture(path, blank=True)
            result = self.backend.recognize({"hero": "赏金猎人", "image": str(path)})
            self.assertEqual(result["matchedCount"], 0)
            self.assertFalse(any(c["recommended"] for c in result["choices"]))

    def test_live_opening_probes_shifted_titles_and_preserves_capture_window(self):
        from worker import handle_request
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "opening.png"
            make_fixture(path, title_y=385)
            context = {"foreground_matches_lol": True, "lol_window_bbox": [100, 200, 2020, 1280],
                       "lol_window_hwnd": 41, "timestamp_ms": int(time.time() * 1000)}
            modes = []
            def captured(request, runner):
                modes.append(request["mode"])
                is_capture = not request.get("screen_image")
                request = dict(request, screen_image=request.get("screen_image") or str(path))
                result = handle_request(request, runner)
                result["capture_context"] = context if is_capture else {}
                return result
            self.backend.dispatch({"action": "reset_tracking"})
            self.backend.ocr.live_preset = None
            with patch("worker.handle_request", side_effect=captured), \
                    patch('ocr_service.capture_screen', return_value=path), \
                    patch('ocr_service.capture_context_for_image', return_value=context):
                result = self.backend.recognize({"hero": "赏金猎人", "probeTitles": True})
            self.assertEqual([c["name"] for c in result["choices"]], NAMES)
            self.assertTrue(result["augmentPanelVisible"])
            self.assertTrue(result["autoTrackingReady"])
            self.assertEqual(result["trackingGeometry"]["windowHwnd"], 41)
            self.assertEqual(result["capturedAtMs"], context["timestamp_ms"])
            self.assertEqual(result["ocrDebug"]["preset"], "1920x1080@125")
            self.assertTrue(all(mode == "diagnostic" for mode in modes))

    def test_anchor_alone_does_not_keep_closed_panel_visible(self):
        payload = {"stage": "final", "anchor_detection": {"detected": True},
                   "frame_quality": {"probable_augment_frame": False}, "slot_candidates": []}
        with patch.object(self.backend.ocr, '_scan', return_value=payload):
            result = self.backend.recognize({"hero": "赏金猎人"})
        self.assertFalse(result["augmentPanelVisible"])
        self.assertEqual(result["matchedCount"], 0)

    def test_probe_is_periodic_without_live_inventory_and_skipped_capture_does_not_consume_it(self):
        modes = []
        skipped = {"stage": "skipped"}
        normal = {"stage": "final", "frame_quality": {"probable_augment_frame": False}}
        def capture(request, directory, probe):
            modes.append('diagnostic' if probe else 'live')
            return copy.deepcopy(skipped if len(modes) == 1 else normal)
        self.backend.ocr.last_title_probe = -float("inf")
        with patch.object(self.backend.ocr, '_scan', side_effect=capture), patch("ocr_service.time.monotonic", side_effect=[10, 10.5, 11, 13]):
            for _ in range(4):
                self.backend.recognize({"hero": "赏金猎人"})
        self.assertEqual(modes, ["diagnostic", "diagnostic", "live", "diagnostic"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
