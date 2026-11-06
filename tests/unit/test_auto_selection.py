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
    def test_latest_outside_or_reroll_sample_does_not_fall_back_to_old_card(self):
        g = dict(live_geometry(*fixture()), rerollHitboxes=[[1000,1100,1100,1200]])
        for x,y,reason in [(50,50,'outside_card_or_reroll'), (1050,1150,'cursor_on_reroll')]:
            tracker = AutomaticSelectionTracker()
            tracker.observe_frame(g, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
            history = [dict(x=1060,y=670,hwnd=41,atMs=1100,geometry=g), dict(x=x,y=y,hwnd=41,atMs=1200,geometry=g)]
            tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1300, hover_history=history)
            tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1800, hover_history=history)
            self.assertIsNone(tracker.pending)
            self.assertEqual(tracker.hover_diagnostic['reason'], reason)

    def test_user_reported_misses_accept_full_recorded_card_bounds(self):
        import json
        cases = json.loads((Path(__file__).parents[1] / 'fixtures/reported-missed-selections.json').read_text('utf-8'))['cases']
        for case in cases:
            with self.subTest(selection=case['reportedSelection']):
                t = 1000
                cards = case['cards']
                payload = dict(titleRegionCards=True, anchor_detection={'detected': True},
                    capture_context=dict(foreground_matches_lol=True, lol_window_bbox=[0,0,2560,1440], lol_window_hwnd=41, timestamp_ms=t),
                    crop_geometry=dict(card_rects=cards, image_size=[2560,1440], augment_rects=[dict(x=c[0],y=400,width=c[2]-c[0],height=40) for c in cards]))
                g = live_geometry(payload, [dict(name=n,matched=True) for n in case['names']])
                tracker = AutomaticSelectionTracker()
                tracker.observe_frame(g, foreground_game=True, frame_detected=True, hwnd=41, at_ms=t)
                slot = case['names'].index(case['reportedSelection'])
                card = cards[slot]
                sample = dict(x=(card[0]+card[2])//2, y=card[3]-2, hwnd=41, atMs=1100, geometry=g)
                tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1500, hover_history=[sample])
                for at in (2000,3000,4000,5000,6000):
                    self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=at, hover_history=[sample]))
                self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=6500), case['reportedSelection'])

    def test_next_offer_does_not_erase_a_selection_waiting_five_seconds(self):
        g = dict(live_geometry(*fixture()), names=['术士果汁盒','尖端发明家','未收录：过量延伸者'])
        next_offer = dict(g, names=['科学狂人','不祥契约','死之舞'], capturedAtMs=2000)
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(g, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
        history = [dict(x=720,y=670,hwnd=41,atMs=1200,geometry=g)]
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1500, hover_history=history)
        for at in (2000,3000,4000,5000,6000):
            self.assertIsNone(tracker.observe_frame(next_offer, foreground_game=True, frame_detected=True, hwnd=41, at_ms=at, hover_history=history))
        self.assertEqual(tracker.observe_frame(next_offer, foreground_game=True, frame_detected=True, hwnd=41, at_ms=6500), '术士果汁盒')
        self.assertEqual(tracker.frame['names'], next_offer['names'])
        self.assertEqual(tracker.selection_evidence['slot'], 0)
        next_offer['capturedAtMs'] = 6600
        tracker.observe_frame(next_offer, foreground_game=True, frame_detected=True, hwnd=41, at_ms=6600)
        next_history = [dict(x=720,y=670,hwnd=41,atMs=6650,geometry=next_offer)]
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=6800, hover_history=next_history)
        for at in (7300,8300,9300,10300,11300):
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=at, hover_history=next_history))
        self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=11800), '科学狂人')

    def test_hover_during_capture_supersedes_request_time_snapshot(self):
        geometry = dict(live_geometry(*fixture()), names=['侵蚀', '神射法师', '海克斯科技龙魂'])
        tracker = AutomaticSelectionTracker()
        tracker.observe_frame(geometry, foreground_game=True, frame_detected=True, hwnd=41, at_ms=1000)
        earlier = dict(x=1060, y=670, hwnd=41, atMs=1200, geometry=geometry)
        selected = dict(x=720, y=670, hwnd=41, atMs=1700, geometry=geometry)
        # The first request began before the move; the second contains the
        # samples collected during screenshot capture and OCR.
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=1800, hover_history=[earlier])
        tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=2300, hover_history=[earlier, selected])
        for at in (3300, 4300, 5300, 6300):
            self.assertIsNone(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=at, hover_history=[]))
        self.assertEqual(tracker.observe_frame(None, foreground_game=True, frame_detected=False, hwnd=41, at_ms=6800, hover_history=[]), '侵蚀')
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
                first = backend.dispatch(dict(request, hoverHistory=[hover]))
                self.assertIsNone(first['autoSelection'])
                for _ in range(4): self.assertIsNone(backend.dispatch(dict(request, hoverHistory=[hover]))['autoSelection'])
                inferred = backend.dispatch(dict(request, hoverHistory=[hover]))
                self.assertEqual(inferred['autoSelection'], '炼狱导管')
                self.assertEqual(inferred['selectionSource'], 'hover')
                self.assertEqual(backend.ocr.journal.summary['inferred'], ['炼狱导管'])
                events = (Path(folder) / 'match' / 'events.jsonl').read_text(encoding='utf-8')
                self.assertIn('"hoverAtMs"', events)
                self.assertIn('"slot": 2', events)
                restored_result = backend.dispatch(dict(request, owned=['炼狱导管']))
                self.assertEqual(restored_result['revokedSelection'], '炼狱导管')
                self.assertEqual(backend.ocr.journal.summary['inferred'], [])
                self.assertEqual(backend.ocr.journal.summary['confirmed'], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
