import sys
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from hexgod.tracking.selection import AutomaticSelectionTracker, live_geometry
from hexgod.api.backend import Backend


def fixture():
    payload = {
        "anchor_detection": {"detected": True},
        "capture_context": {"foreground_matches_lol": True, "lol_window_bbox": [100, 200, 2020, 1280],
                            "lol_window_hwnd": 41, "timestamp_ms": 1000},
        "crop_geometry": {"augment_rects": [
            {"x": x, "y": 403, "width": 260, "height": 45} for x in (490, 830, 1170)]},
    }
    choices = [{"matched": True, "name": name} for name in ("虚幻武器", "炼狱导管", "老练狙神")]
    return payload, choices


class AutomaticSelectionTests(unittest.TestCase):
    def test_hover_inference_waits_five_seconds_and_revokes_when_panel_returns(self):
        geometry = live_geometry(*fixture())
        hover = dict(x=1060, y=670, hwnd=41, atMs=1200, geometry=geometry)
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1300, hover=hover))
        for at in (2300, 3300, 4300, 5300, 6299):
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=at))
        self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=6300), '炼狱导管')
        self.assertEqual(tracker.selection_source, 'hover')
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=7000)
        self.assertEqual(tracker.revoked_selection, '炼狱导管')
        for invalid in (dict(hover, hwnd=99), dict(hover, atMs=1), dict(hover, y=1200)):
            tracker = AutomaticSelectionTracker()
            tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1800, hover=invalid))
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=2300))

    def test_hover_pending_is_cancelled_if_panel_returns(self):
        geometry = live_geometry(*fixture())
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
        hover = dict(x=1060, y=670, hwnd=41, atMs=1200, geometry=geometry)
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1300, hover=hover)
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1400)
        self.assertIsNone(tracker.pending)
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=2000))
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=2600))

    def test_hover_gap_restarts_five_second_window(self):
        geometry = live_geometry(*fixture())
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
        hover = dict(x=1060, y=670, hwnd=41, atMs=1200, geometry=geometry)
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1300, hover=hover)
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=5500))
        for at in (6500, 7500, 8500, 9500, 10499):
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=at))
        self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=10500), '炼狱导管')

    def test_stale_or_repeated_capture_cannot_confirm_a_click(self):
        geometry=live_geometry(*fixture())
        tracker=AutomaticSelectionTracker()
        tracker.observe_frame(geometry,foreground_game=True,frame_detected=True,hwnd=41,at_ms=1000)
        tracker.observe_click(1060,670,41,1200)
        for timestamp in [1100,1100,1500,1500]:
            self.assertIsNone(tracker.observe_frame(None,foreground_game=True,frame_detected=False,hwnd=41,at_ms=timestamp))
        self.assertEqual(tracker.observe_frame(None,foreground_game=True,frame_detected=False,hwnd=41,at_ms=2000),'炼狱导管')

    def test_partial_ocr_is_not_a_reroll_but_another_window_cancels(self):
        geometry=live_geometry(*fixture())
        tracker=AutomaticSelectionTracker()
        tracker.observe_frame(geometry,foreground_game=True,frame_detected=True,hwnd=41,at_ms=1000)
        tracker.observe_click(1060,670,41,1200)
        partial=dict(geometry,names=[None,'炼狱导管',None])
        tracker.observe_frame(partial,foreground_game=True,frame_detected=True,hwnd=41,at_ms=1500)
        self.assertIsNotNone(tracker.pending)
        tracker.observe_frame(None,foreground_game=True,frame_detected=False,hwnd=42,at_ms=2000)
        self.assertIsNone(tracker.pending)

    def test_click_then_disappearance_records_only_selected_slot(self):
        geometry = live_geometry(*fixture())
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1050)
        self.assertEqual(tracker.observe_click(1060, 670, 41, 1200), "炼狱导管")
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False,
                                                hwnd=41, at_ms=3800))
        self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False,
                                               hwnd=41, at_ms=6400), "炼狱导管")
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False,
                                                hwnd=41, at_ms=9000))

    def test_wrong_window_outside_card_and_reroll_do_not_record(self):
        geometry = live_geometry(*fixture())
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1050)
        self.assertIsNone(tracker.observe_click(1060, 670, 99, 1200))
        self.assertIsNone(tracker.observe_click(1060, 1200, 41, 1200))
        self.assertEqual(tracker.observe_click(1060, 670, 41, 1200), "炼狱导管")
        changed = dict(geometry, names=["虚幻武器", "双生火焰", "老练狙神"])
        self.assertIsNone(tracker.observe_frame(changed, foreground_game=True, frame_detected=True,
                                                hwnd=41, at_ms=3800))
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False,
                                                hwnd=41, at_ms=6400))
        self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False,
                                                hwnd=41, at_ms=9000))

    def test_skipped_capture_does_not_confirm(self):
        geometry = live_geometry(*fixture())
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1050)
        tracker.observe_click(1060, 670, 41, 1200)
        for at in (3800, 6400):
            self.assertIsNone(tracker.observe_frame(None, foreground_game=False, frame_detected=False,
                                                    hwnd=41, at_ms=at))

    def test_geometry_requires_live_game_and_a_known_choice(self):
        payload, choices = fixture()
        self.assertEqual(len(live_geometry(payload, choices)["hitboxes"]), 3)
        self.assertLess(live_geometry(payload, choices)["hitboxes"][1][3], 930)
        payload["capture_context"]["foreground_matches_lol"] = False
        self.assertIsNone(live_geometry(payload, choices))
        payload["capture_context"]["foreground_matches_lol"] = True
        choices[1]["matched"] = False
        partial = live_geometry(payload, choices)
        self.assertIsNotNone(partial)
        self.assertIsNone(partial["names"][1])
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(partial, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1050)
        self.assertIsNone(tracker.observe_click(1060, 670, 41, 1200))
        self.assertEqual(tracker.observe_click(720, 670, 41, 1200), "虚幻武器")
        choices[0]["matched"] = choices[2]["matched"] = False
        self.assertIsNone(live_geometry(payload, choices))

    def test_backend_protocol_confirms_clicked_choice_after_two_blank_frames(self):
        backend = Backend(use_remote=False)
        timestamp = int(time.time() * 1000)
        base, _ = fixture()
        base["capture_context"]["timestamp_ms"] = timestamp
        shown = dict(base, payloads=[{"lines": [{"text": name, "score": 0.99}]}
                                     for name in ("虚幻武器", "炼狱导管", "老练狙神")],
                     frame_quality={"probable_augment_frame": True},
                     slot_candidates=[[{"text": name, "confidence": 99,
                                        "title_like": True, "image_quality": 100,
                                        "variant_index": 0}] for name in
                                      ("虚幻武器", "炼狱导管", "老练狙神")])
        blank = {"capture_context": dict(base["capture_context"], timestamp_ms=timestamp+500),
                 "anchor_detection": {"detected": False}, "frame_quality": {"probable_augment_frame": False},
                 "payloads": []}
        later_blank = dict(blank, capture_context=dict(base['capture_context'], timestamp_ms=timestamp+1100))
        with patch.object(backend.ocr, '_scan', side_effect=[shown, blank, later_blank]):
            first = backend.dispatch({"action": "ocr", "hero": "复仇焰魂"})
            self.assertTrue(first["autoTrackingReady"])
            self.assertEqual(backend.dispatch({"action": "click", "x": 1060, "y": 670,
                                               "hwnd": 41, "atMs": timestamp + 100})["pending"], "炼狱导管")
            self.assertIsNone(backend.dispatch({"action": "ocr", "hero": "复仇焰魂"})["autoSelection"])
            self.assertEqual(backend.dispatch({"action": "ocr", "hero": "复仇焰魂"})["autoSelection"],
                             "炼狱导管")

    def test_backend_journals_and_revokes_hover_inference(self):
        import tempfile
        from hexgod.tracking.journal import MatchJournal
        backend = Backend(use_remote=False)
        timestamp = int(time.time() * 1000)
        base, _ = fixture()
        base['capture_context']['timestamp_ms'] = timestamp
        names = ('虚幻武器', '炼狱导管', '老练狙神')
        shown = dict(base, payloads=[{'lines': [{'text': name, 'score': .99}]} for name in names],
                     frame_quality={'probable_augment_frame': True},
                     slot_candidates=[[{'text': name, 'confidence': 99, 'title_like': True,
                                        'image_quality': 100, 'variant_index': 0}] for name in names])
        blanks = [dict(capture_context=dict(base['capture_context'], timestamp_ms=timestamp + delta),
                       anchor_detection={'detected': False}, frame_quality={'probable_augment_frame': False}, payloads=[])
                  for delta in (500, 1500, 2500, 3500, 4500, 5500)]
        restored = dict(shown, capture_context=dict(base['capture_context'], timestamp_ms=timestamp + 6500))
        with tempfile.TemporaryDirectory() as folder:
            backend.ocr.journal = MatchJournal(root=Path(folder))
            with patch.object(backend.ocr, '_scan', side_effect=[shown, *blanks, restored]):
                request = {'action': 'ocr', 'hero': '复仇焰魂', 'contextId': 'match|hero', 'sessionKey': 'match'}
                initial = backend.dispatch(request)
                hover = {'x': 1060, 'y': 670, 'hwnd': 41, 'atMs': timestamp + 100,
                         'geometry': initial['trackingGeometry']}
                first = backend.dispatch(dict(request, hoverCandidate=hover))
                self.assertIsNone(first['autoSelection'])
                for _ in range(4): self.assertIsNone(backend.dispatch(request)['autoSelection'])
                inferred = backend.dispatch(request)
                self.assertEqual(inferred['autoSelection'], '炼狱导管')
                self.assertEqual(inferred['selectionSource'], 'hover')
                self.assertEqual(backend.ocr.journal.summary['inferred'], ['炼狱导管'])
                restored_result = backend.dispatch(dict(request, owned=['炼狱导管']))
                self.assertEqual(restored_result['revokedSelection'], '炼狱导管')
                self.assertEqual(backend.ocr.journal.summary['inferred'], [])
                self.assertEqual(backend.ocr.journal.summary['confirmed'], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
