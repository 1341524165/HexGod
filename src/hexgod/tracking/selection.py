"""Passive click-to-card correlation for live OCR frames.

No game input is sent. A click is only recorded after the same game window
subsequently shows no augment panel on two scans.
"""
from __future__ import annotations


def live_geometry(payload, choices):
    context = payload.get("capture_context") or {}
    crop = payload.get("crop_geometry") or {}
    box = context.get("lol_window_bbox")
    rects = crop.get("augment_rects") or []
    cards = crop.get("card_rects") or []
    panel_evidence = (bool((payload.get("anchor_detection") or {}).get("detected")) or
                      bool((payload.get("frame_quality") or {}).get("probable_augment_frame")) or
                      bool(payload.get("ocr_inferred_augment_frame")))
    if (not context.get("foreground_matches_lol") or not isinstance(box, list) or len(box) != 4
            or not isinstance(context.get("lol_window_hwnd"), int) or len(rects) != 3
            or not panel_evidence or len(choices) != 3 or
            (not any(c.get("matched") for c in choices) and not payload.get('titleRegionCards'))):
        return None
    try:
        left, top = int(box[0]), int(box[1])
        right, bottom = int(box[2]), int(box[3])
        image_size = crop.get("image_size") or [right - left, bottom - top]
        sx, sy = (right-left) / image_size[0], (bottom-top) / image_size[1]
        scale = max(0.5, min(2.0, int(image_size[1]) / 1080))
        hitboxes, rerolls = [], []
        for rect in rects:
            x, y = int(rect["x"]), int(rect["y"])
            width, height = int(rect["width"]), int(rect["height"])
            if width <= 0 or height <= 0:
                return None
            # Exclude the per-card reroll button below the actual card.
            center_y = y + height / 2
            center_x = x + width / 2
            hitboxes.append([max(left, int(left + (x - 20 * scale) * sx)),
                             max(top, int(top + (center_y - 245 * scale) * sy)),
                             min(right, int(left + (x + width + 20 * scale) * sx)),
                             min(bottom, int(top + (center_y + 260 * scale) * sy))])
            if payload.get('titleRegionCards'):
                index = len(hitboxes)-1
                if len(cards) != 3:
                    return None
                card = cards[index]
                x1, y1, x2, y2 = (int(value) for value in card)
                cw, ch = x2-x1, y2-y1
                hitboxes[-1] = [int(left+x1*sx), int(top+y1*sy),
                                int(left+x2*sx), int(top+y2*sy)]
                rerolls.append([int(left+(x1+cw*.39)*sx), int(top+(y2+ch*.025)*sy),
                                int(left+(x1+cw*.61)*sx), int(top+(y2+ch*.145)*sy)])
        return {"windowHwnd": context["lol_window_hwnd"],
                "capturedAtMs": int(context.get("timestamp_ms") or 0),
                "hitboxes": hitboxes,
                "rerollHitboxes": rerolls,
                "names": [c["name"] if c.get("matched") else None for c in choices]}
    except (ValueError, TypeError, KeyError):
        return None


class AutomaticSelectionTracker:
    def __init__(self):
        self.frame = None
        self.pending = None
        self.selection_source = None
        self.selection_evidence = None
        self.provisional = None
        self.revoked_selection = None
        self.hover_missing_at = 0
        self.deferred = []
        self.hover_diagnostic = {}
        self.misses = 0
        self.first_miss = 0
        self.last_frame_at = 0
        self.last_event = 'waiting_for_panel'

    def observe_click(self, x, y, hwnd, at_ms, geometry=None):
        frame = geometry if isinstance(geometry, dict) else self.frame
        self.last_event = 'no_frame' if frame is None else 'pending_exists' if self.pending else 'wrong_window'
        if frame is None or self.pending is not None or hwnd != frame["windowHwnd"]:
            return None
        self.last_event = 'stale_frame'
        if at_ms < frame["capturedAtMs"] or at_ms > frame["capturedAtMs"] + 5000:
            return None
        slots = [index for index, (x1, y1, x2, y2) in enumerate(frame["hitboxes"])
                 if x1 <= x <= x2 and y1 <= y <= y2]
        if len(slots) != 1:
            self.last_event = 'outside_card_or_reroll'
            return None
        if not frame["names"][slots[0]]:
            self.last_event = 'clicked_title_unrecognized'
            return None
        self.pending = {"name": frame["names"][slots[0]], "atMs": at_ms,
                        "windowHwnd": hwnd, "slot": slots[0], "signature": tuple(frame["names"]),
                        "x": x, "y": y, "source": "click"}
        self.last_event = 'click_accepted_waiting_for_disappearance'
        self.frame = frame
        self.misses = 0
        self.first_miss = 0
        return self.pending["name"]

    def _history_candidate(self, history, missing_at, hwnd):
        samples = [s for s in history if isinstance(s, dict) and s.get('hwnd') == hwnd
                   and isinstance(s.get('atMs'), (int, float)) and 0 <= missing_at - s['atMs'] <= 1500]
        self.hover_diagnostic = {'sampleCount': len(history), 'eligibleSamples': len(samples),
                                 'missingAtMs': missing_at, 'reason': 'no_recent_cursor_sample'}
        if not samples:
            return None
        sample = max(samples, key=lambda s: s['atMs'])
        frame = self.frame
        self.hover_diagnostic.update(x=sample.get('x'), y=sample.get('y'), hoverAtMs=sample['atMs'])
        if frame is None or frame['windowHwnd'] != hwnd:
            self.hover_diagnostic['reason'] = 'no_same_window_geometry'
            return None
        if any(x1 <= sample['x'] <= x2 and y1 <= sample['y'] <= y2 for x1,y1,x2,y2 in frame.get('rerollHitboxes', [])):
            self.hover_diagnostic['reason'] = 'cursor_on_reroll'
            return None
        slots = [i for i,(x1,y1,x2,y2) in enumerate(frame['hitboxes']) if x1 <= sample['x'] <= x2 and y1 <= sample['y'] <= y2]
        snapshot = sample.get('geometry') or {}
        previous_names = snapshot.get('names') or []
        if len(slots) == 1 and len(previous_names) == 3 and previous_names[slots[0]] != frame['names'][slots[0]]:
            self.hover_diagnostic['reason'] = 'cursor_sample_precedes_slot_change'
            return None
        self.hover_diagnostic['reason'] = 'candidate_available'
        # Resolve coordinates against the last visible offer, including card
        # borders. Other slots may have rerolled since the cursor sample.
        return dict(sample, geometry=frame)

    def observe_frame(self, geometry, *, foreground_game, frame_detected, hwnd, at_ms, hover=None, hover_history=None):
        if at_ms <= self.last_frame_at:
            return None
        previous_at = self.last_frame_at
        self.last_frame_at = at_ms
        self.revoked_selection = None
        deferred_selection = None
        for old in list(self.deferred):
            if not foreground_game or (previous_at and at_ms - previous_at > 3000):
                old['missingAtMs'] = at_ms
            same_offer = geometry is not None and sum(bool(a and b and a == b) for a,b in zip(old['signature'], geometry['names'])) >= 2
            if hwnd != old['windowHwnd'] or same_offer or at_ms - old['atMs'] > 30000:
                self.deferred.remove(old)
            elif foreground_game and at_ms - old['missingAtMs'] >= 5000 and deferred_selection is None:
                self.deferred.remove(old)
                self.provisional = old
                self.selection_evidence = old
                self.selection_source = 'hover'
                deferred_selection = old['name']
        # A later offer can appear before five seconds expire. Keep the prior
        # candidate on its own timer instead of cancelling it as a reopen.
        prior_missing = self.hover_missing_at
        if (geometry is not None and prior_missing and self.frame and not self.pending
                and foreground_game and isinstance(hover_history, list)):
            hover = self._history_candidate(hover_history, prior_missing, hwnd)
            if hover and self.observe_click(hover['x'], hover['y'], hwnd, hover['atMs'], geometry=hover['geometry']):
                self.pending['source'] = 'hover'
                self.first_miss = prior_missing
            elif hover:
                self.hover_diagnostic['reason'] = self.last_event
        if geometry is not None or frame_detected or not foreground_game or (previous_at and at_ms - previous_at > 3000):
            self.hover_missing_at = 0
        elif not self.pending and self.frame is not None and isinstance(hover_history, list):
            if not self.hover_missing_at:
                self.hover_missing_at = at_ms
            else:
                missing_at = self.hover_missing_at
                hover = self._history_candidate(hover_history, missing_at, hwnd)
        if self.pending and self.pending.get('source') == 'hover' and (not foreground_game or (previous_at and at_ms - previous_at > 3000)):
            self.misses = 0
            self.first_miss = 0
        # A cursor position is only an inference: never create one while a
        # panel is visible, or override a real click already in flight.
        if (not self.pending and geometry is None and not frame_detected and foreground_game
                and isinstance(hover, dict) and hwnd == hover.get('hwnd')
                and 0 <= (self.hover_missing_at or at_ms) - hover.get('atMs', 0) <= 1500):
            frame = hover.get('geometry')
            if isinstance(frame, dict) and self.observe_click(
                    hover.get('x'), hover.get('y'), hwnd, hover['atMs'], geometry=frame):
                self.pending['source'] = 'hover'
                if self.hover_missing_at:
                    self.misses = 1
                    self.first_miss = self.hover_missing_at
                self.last_event = 'hover_inferred_waiting_for_disappearance'
            else:
                self.hover_diagnostic['reason'] = self.last_event
        pending = self.pending
        if pending and at_ms - pending["atMs"] > 10000:
            self.last_event = 'confirmation_timeout'
            self.pending = None
            pending = None
        if pending and hwnd and hwnd != pending['windowHwnd']:
            self.reset()
            self.last_event = 'game_window_changed'
            pending = None
        if pending and at_ms <= pending['atMs']:
            return None
        if geometry is not None:
            if self.provisional and hwnd == self.provisional['windowHwnd']:
                old = self.provisional['signature']
                matches = sum(bool(a and b and a == b) for a, b in zip(old, geometry['names']))
                if matches >= 2:
                    self.revoked_selection = self.provisional['name']
                    self.provisional = None
                    self.last_event = 'selection_inference_revoked'
                elif sum(bool(n) for n in geometry['names']) >= 2:
                    self.provisional = None
            if pending and pending.get('source') == 'hover':
                overlap = sum(bool(a and b and a == b) for a,b in zip(pending['signature'], geometry['names']))
                if overlap == 0 and all(geometry['names']) and self.first_miss:
                    self.deferred.append(dict(pending, missingAtMs=self.first_miss))
                    self.last_event = 'prior_offer_waiting_for_delay'
                self.pending = None
                pending = None
                if overlap:
                    self.last_event = 'hover_panel_returned'
            if pending and geometry['names'][pending['slot']] and geometry['names'][pending['slot']] != pending['name']:
                # Reroll or another round: the clicked card was not confirmed.
                self.pending = None
                self.last_event = 'clicked_slot_changed'
                self.misses = 0
            self.misses = 0
            self.frame = geometry
            return deferred_selection
        if not foreground_game or self.frame is None or hwnd != self.frame["windowHwnd"]:
            return deferred_selection
        if pending and not frame_detected:
            self.misses += 1
            if self.misses == 1:
                self.first_miss = at_ms
            wait = 5000 if pending.get('source') == 'hover' else 400
            if self.misses >= 2 and at_ms - self.first_miss >= wait and deferred_selection is None:
                name = pending["name"]
                self.selection_source = pending.get('source', 'click')
                self.selection_evidence = dict(pending)
                if self.selection_source == 'hover':
                    self.provisional = dict(pending)
                self.pending = None
                self.frame = None
                self.misses = 0
                self.last_event = 'selection_inferred' if self.selection_source == 'hover' else 'selection_confirmed'
                return name
        elif pending:
            self.misses = 0
        else:
            self.misses += 1
            if self.misses >= 2:
                self.last_event = 'hover_no_candidate'
                self.frame = None
                self.misses = 0
        return deferred_selection

    def reset(self):
        self.frame = None
        self.pending = None
        self.selection_source = None
        self.selection_evidence = None
        self.provisional = None
        self.revoked_selection = None
        self.hover_missing_at = 0
        self.deferred = []
        self.hover_diagnostic = {}
        self.misses = 0
        self.first_miss = 0
        self.last_frame_at = 0
        self.last_event = 'waiting_for_panel'
