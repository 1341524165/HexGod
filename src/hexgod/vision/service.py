"""Capture/OCR service, isolated from recommendations and live-state polling."""
from pathlib import Path
import math
import tempfile
import time
from hexgod.paths import CACHE_DIR
from hexgod.common import number
from hexgod.vision.pipeline import (load_presets, match_ocr_texts, normalize_text, capture_screen,
                              capture_context_for_image, CaptureSkipped)
from hexgod.tracking.selection import AutomaticSelectionTracker, live_geometry
from hexgod.vision.titles import read_titles
from hexgod.vision.matching import corrected_title
from hexgod.tracking.journal import MatchJournal


class OcrService:
    def __init__(self, engine):
        self.engine = engine
        self.worker = None
        self.title_worker = None
        self.legacy_capture = False
        self.selection_tracker = AutomaticSelectionTracker()
        self.last_title_probe = -math.inf
        self.live_preset = None
        self.context_id = None
        self.last_slots = [None, None, None]
        self.journal = MatchJournal()

    def reset(self):
        self.selection_tracker.reset()
        self.last_title_probe = -math.inf
        self.live_preset = None
        self.last_slots = [None, None, None]

    def retain_slots(self, result, captured):
        hashes = result.get('titleFingerprints') or []
        carried = False
        for index, choice in enumerate(result['choices']):
            fingerprint = hashes[index] if index < len(hashes) else None
            previous = self.last_slots[index]
            if fingerprint and choice['matched']:
                self.last_slots[index] = (fingerprint, dict(choice), captured)
            elif fingerprint and previous and fingerprint == previous[0] and 0 <= captured - previous[2] <= 1200:
                result['choices'][index] = dict(previous[1], retained=True, recommended=False)
                carried = True
            else:
                self.last_slots[index] = None
        if carried:
            # A partial cached set has not been compared as a fresh whole.
            for choice in result['choices']:
                choice['recommended'] = False
            result['matchedCount'] = sum(c['matched'] for c in result['choices'])
            result['complete'] = all(c['matched'] for c in result['choices'])

    def ensure_context(self, context_id):
        if context_id != self.context_id:
            self.reset()
            self.context_id = context_id

    def _scan(self, request, directory, probe):
        from hexgod.vision.worker import handle_request
        source = request.get('image')
        context = {}
        if not source:
            target = Path(directory) / 'screen.png'
            try:
                source = capture_screen(target, allow_non_lol_foreground=False, performance_mode='performance')
            except CaptureSkipped as error:
                return {'stage': 'skipped', 'capture_context': error.meta,
                        'anchor_detection': {'reason': str(error)}}
            except RuntimeError as error:
                if 'lol_window_not_found' not in str(error) and '未找到' not in str(error):
                    raise
                return {'stage': 'skipped', 'capture_context': capture_context_for_image(target),
                        'anchor_detection': {'reason': str(error)}}
            context = capture_context_for_image(source)
        started = time.perf_counter()
        if self.title_worker is None:
            from hexgod.vision.paddle import TitleOcr
            self.title_worker = TitleOcr()
        titles = read_titles(source, directory, self.title_worker)
        if titles['titleRegionCards']:
            # Border evidence permits a single readable title. Missing slots
            # stay empty even when the rest of the screen contains tooltip text.
            w, h = titles['imageSize']
            rects = []
            for rect in titles['titleRegionRects']:
                if rect is None:
                    rects.append({'x': 0, 'y': 0, 'width': 0, 'height': 0})
                    continue
                x1, y1, x2, y2 = rect
                rects.append({'x': int(x1), 'y': int(y1), 'width': int(x2-x1), 'height': int(y2-y1)})
            return {**titles, 'stage': 'final', 'source': str(source), 'capture_context': context,
                'preset': {'key': 'title-regions'},
                'crop_geometry': {'image_size': [w,h], 'augment_rects': rects,
                                  'card_rects': titles['titleCardRects']},
                'frame_quality': {'probable_augment_frame': True},
                'timings': {'titleRegionMs': round((time.perf_counter()-started)*1000)},
                'anchor_detection': {'detected': True, 'reason': 'title-regions'}}
        if not request.get('image') and not self.legacy_capture:
            return {**titles, 'stage': 'final', 'source': str(source), 'capture_context': context,
                    'preset': {'key': 'title-regions'}, 'frame_quality': {'probable_augment_frame': False},
                    'anchor_detection': {'detected': False, 'reason': 'no_card_borders'},
                    'timings': {'titleRegionMs': round((time.perf_counter()-started)*1000)}}
        payload = handle_request({'preset_key': request.get('preset') or '1920x1080@100',
            'screen_image': str(source), 'output_dir': directory, 'performance_mode': 'performance',
            'mode': 'diagnostic' if probe else 'live', 'capture_policy': 'retain'}, self.worker)
        payload['capture_context'] = context
        return payload

    def _interpret_ocr(self, payload, hero, owned, source):
        quality = payload.get("frame_quality") or {}
        text_evidence = quality.get("text_evidence") or {}
        candidates_by_slot = payload.get("slot_candidates") or []
        known_title_slots = sum(
            any(candidate.get("title_like") and
                self.engine.canonical_augment(candidate.get("text")) in self.engine.augments
                for candidate in group)
            for group in candidates_by_slot[:3]
        )
        frame_valid = (not text_evidence.get("reject_frame") and
                       (quality.get("probable_augment_frame") or
                        (payload.get("ocr_inferred_augment_frame") and
                         known_title_slots >= 2)))
        texts, confidences, selected = [], [], []
        for slot in range(3):
            candidates = candidates_by_slot[slot] if slot < len(candidates_by_slot) else []
            if not candidates:
                payloads = payload.get("payloads") or []
                lines = (payloads[slot].get("lines") or []) if slot < len(payloads) else []
                candidates = [{"text": lines[0].get("text", ""),
                               "confidence": number(lines[0].get("score")) * 100,
                               "title_like": True, "image_quality": 0,
                               "variant_index": 0}] if lines else []
            ranked = []
            for candidate in candidates:
                raw = str(candidate.get("text") or "")
                confidence = number(candidate.get("confidence"))
                if confidence <= 1:
                    confidence *= 100
                match = match_ocr_texts([raw], self.engine.aliases)[0]
                name = self.engine.canonical_augment(raw) or match.get("matched_name")
                if not name and candidate.get('title_region') and confidence >= 85:
                    name = corrected_title(raw, self.engine.augments)
                valid = (frame_valid and
                         bool(candidate.get("title_like")) and name in self.engine.augments and
                         (match.get("mode") == "exact" or
                          len(normalize_text(raw)) >= 3))
                score = ((1000 if match.get("mode") == "exact" else 850) +
                         number(candidate.get("image_quality")) * 0.2 +
                         number(match.get("score")) * 0.1) if valid else 0
                ranked.append((score, confidence, -int(number(candidate.get("variant_index"))),
                               raw, valid, name))
            best = max(ranked, default=(0, 0, 0, "", False, None))
            texts.append(best[5] if best[4] else "")
            confidences.append(best[1] / 100)
            selected.append({"slot": slot + 1, "raw": best[3], "name": best[5] if best[4] else None,
                             "confidence": round(best[1], 1), "valid": best[4],
                             "candidateCount": len(candidates)})
        seen_names = set()
        for index, entry in enumerate(selected):
            if entry["valid"]:
                if entry["name"] in seen_names:
                    entry["valid"] = False
                    entry["name"] = None
                    texts[index] = ""
                else:
                    seen_names.add(entry["name"])
        result = self.engine.rank_choices(hero, texts, confidences, source, owned)
        # Show a clearly identified new title only alongside two catalogued
        # titles. It has no invented stats and is stored with an explicit marker.
        if payload.get('titleRegionCards') and sum(c['matched'] for c in result['choices']) >= 2:
            for index, choice in enumerate(result['choices']):
                if choice['matched']:
                    continue
                candidates = candidates_by_slot[index] if index < len(candidates_by_slot) else []
                title = next((c for c in candidates if c.get('title_region') and
                              c.get('confidence', 0) >= 90 and 2 <= len(c.get('text', '')) <= 12), None)
                if title:
                    choice.update(name='未收录：' + title['text'], raw=title['text'], matched=True,
                                  known=False, hasStats=False, recommended=False,
                                  reason='本地数据未收录此标题；可记录，不参与统计推荐。')
        result['matchedCount'] = sum(c['matched'] for c in result['choices'])
        result['complete'] = all(c['matched'] for c in result['choices'])
        result['titleFingerprints'] = payload.get('titleFingerprints') or []
        matched_names = [entry["name"] for entry in selected if entry["valid"]]
        # A repeated name in different cards is usually a crop or OCR error.
        score = (len(set(matched_names)) * 1000 -
                 (len(matched_names) - len(set(matched_names))) * 200 +
                 (100 if quality.get("probable_augment_frame") else 0) +
                 10 * number(quality.get("trusted_title_region_count")))
        result["ocrDebug"] = {"frameValid": bool(frame_valid),
                              "frameQuality": bool(quality.get("probable_augment_frame")),
                              "selected": selected,
                              "preset": (payload.get("preset") or {}).get("key"),
                              "score": round(score, 1)}
        return result, score

    @staticmethod
    def _alternate_presets(chosen_key):
        presets = load_presets()
        chosen = presets.get(chosen_key)
        if chosen is None:
            return []
        seen = {tuple((rect.x, rect.y, rect.width, rect.height)
                      for rect in chosen.augment_rects)}
        alternatives = []
        for candidate in presets.values():
            if (candidate.width, candidate.height) != (chosen.width, chosen.height):
                continue
            geometry = tuple((rect.x, rect.y, rect.width, rect.height)
                             for rect in candidate.augment_rects)
            if geometry not in seen:
                seen.add(geometry)
                alternatives.append(candidate.key)
        return alternatives

    def recognize(self, request):
        from hexgod.vision.worker import PersistentOcrRunner, handle_request
        self.ensure_context(request.get('contextId'))
        self.journal.start(request.get('sessionKey') or 'offline', hero=request.get('hero'),
                           evidence_kind='offline' if request.get('image') else request.get('evidenceKind'))
        hero = str(request.get("hero", ""))
        self.engine.validate_hero(hero)
        image_path = request.get("image")
        if image_path:
            image_path = str(Path(image_path).resolve(strict=True))
        if self.worker is None:
            self.worker = PersistentOcrRunner()
        now = time.monotonic()
        probe = bool(image_path or request.get("probeTitles") or now - self.last_title_probe >= 2)
        requested_preset = request.get("preset") or "1920x1080@100"
        CACHE_DIR.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="scan-", dir=CACHE_DIR) as temp_dir:
            self.worker.set_performance_mode('performance')
            payload = self._scan(dict(request, image=image_path,
                preset=requested_preset if image_path else self.live_preset or requested_preset), temp_dir, probe)
            source = "image" if image_path else "screen"
            capture_context = dict(payload.get("capture_context") or {})
            if probe and payload.get("stage") != "skipped":
                self.last_title_probe = now
            result, best_score = self._interpret_ocr(payload, hero, request.get("owned"), source)
            chosen_key = (payload.get("preset") or {}).get("key")
            screen_source = payload.get("source")
            if (not payload.get('titleRegionCards') and result["matchedCount"] < 3 and (probe or result["ocrDebug"]["frameValid"])
                    and screen_source and Path(screen_source).exists()):
                for index, preset_key in enumerate(self._alternate_presets(chosen_key), start=1):
                    other = handle_request({
                        "preset_key": preset_key,
                        "screen_image": screen_source,
                        "output_dir": str(Path(temp_dir) / f"alternate-{index}"),
                        "performance_mode": "performance",
                        "mode": "diagnostic" if probe else "live",
                        "capture_policy": "retain",
                    }, self.worker)
                    candidate_result, candidate_score = self._interpret_ocr(
                        other, hero, request.get("owned"), source)
                    if candidate_score > best_score:
                        payload, result, best_score = other, candidate_result, candidate_score
                    if result["matchedCount"] == 3:
                        break
            if not image_path:
                # Alternate layouts analyze the SAME screenshot. Never lose
                # its real window/timestamp by treating it as an imported file.
                payload["capture_context"] = capture_context
                if result["matchedCount"] >= 2 and chosen_key != 'title-regions':
                    self.live_preset = (payload.get("preset") or {}).get("key")
            reason = (payload.get("anchor_detection") or {}).get("reason", "")
            result["captureReason"] = reason
            result["captureSkipped"] = payload.get("stage") == "skipped"
            rejected = ((payload.get("frame_quality") or {}).get("text_evidence") or {}).get("reject_frame")
            result["augmentPanelVisible"] = not rejected and bool(
                (payload.get("frame_quality") or {}).get("probable_augment_frame") or
                payload.get("ocr_inferred_augment_frame"))
            result["titleProbe"] = probe
            result["capturedAtMs"] = capture_context.get("timestamp_ms") or int(time.time() * 1000)
            if not image_path and result['augmentPanelVisible']:
                self.retain_slots(result, result['capturedAtMs'])
            elif not image_path and not result['captureSkipped']:
                self.last_slots = [None, None, None]
            result["timings"] = payload.get("timings") or {}
            if not image_path:
                context = payload.get("capture_context") or {}
                geometry = live_geometry(payload, result["choices"])
                result["trackingGeometry"] = geometry
                frame_detected = result["augmentPanelVisible"]
                result["autoSelection"] = self.selection_tracker.observe_frame(
                    geometry, foreground_game=bool(context.get("foreground_matches_lol")),
                    frame_detected=frame_detected, hwnd=context.get("lol_window_hwnd"),
                    at_ms=result["capturedAtMs"], hover=request.get('hoverCandidate'))
                result['selectionPending'] = self.selection_tracker.pending is not None
                result["gameWindowHwnd"] = context.get("lol_window_hwnd") if context.get("foreground_matches_lol") else None
                result["autoTrackingReady"] = geometry is not None
                result['selectionDiagnostic'] = self.selection_tracker.last_event
                result['revokedSelection'] = self.selection_tracker.revoked_selection
                if result['revokedSelection']:
                    name = result['revokedSelection']
                    if name in self.journal.summary.get('inferred', []):
                        self.journal.summary['inferred'].remove(name)
                    self.journal.event('selection_inference_revoked', name=name)
                if result['autoSelection']:
                    source = self.selection_tracker.selection_source
                    result['selectionSource'] = source
                    if source == 'hover':
                        self.journal.summary.setdefault('inferred', []).append(result['autoSelection'])
                        self.journal.event('selection_inferred', name=result['autoSelection'], source='hover')
                    else:
                        self.journal.summary['confirmed'].append(result['autoSelection'])
                        self.journal.event('selection_confirmed', name=result['autoSelection'])
                self.journal.frame(result, payload)
            if not result["matchedCount"]:
                result["message"] = (
                    "未找到 LoL 游戏窗口。可先使用截图导入或示例模式。" if "lol_window_not_found" in reason or "未找到" in reason else
                    "请切回 LoL 游戏窗口，等待三选一界面出现。" if "foreground" in reason else
                    "等待海克斯选项出现；识别过程自动写入本局日志。"
                )
            return result

