"""JSON-lines boundary. Recommendations, OCR and game state have separate owners."""
import json
import sys
import traceback
from hexgod.common import ranking_score
from hexgod.vision.service import OcrService
from hexgod.game.live import read_live_game_data
from hexgod.game.lcu import LcuClient


class Backend:
    def __init__(self, use_remote=True):
        if use_remote:
            from hexgod.recommendation.aramgg import AramggEngine
            self.engine = AramggEngine()
        else:
            from hexgod.recommendation.legacy import RecommendationEngine
            self.engine = RecommendationEngine()
        self.ocr = OcrService(self.engine)
        self.ocr.legacy_capture = not use_remote
        self.lcu = LcuClient()

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def __setattr__(self, name, value):
        engine = self.__dict__.get('engine')
        if engine is not None and name not in ('engine', 'ocr', 'lcu') and hasattr(engine, name):
            setattr(engine, name, value)
        else:
            object.__setattr__(self, name, value)

    def recognize(self, request):
        return self.ocr.recognize(request)

    def _interpret_ocr(self, *args):
        return self.ocr._interpret_ocr(*args)

    def dispatch(self, request):
        action = request.get("action")
        engine = self.engine
        if action == "catalog":
            return engine.catalog()
        if action == "hero":
            return engine.hero_detail(request.get("hero"), request.get("owned"))
        if action == "build_path":
            return engine.build_path(request.get("hero"), request.get("owned"),
                                   request.get("itemIds"), request.get("itemsKnown", False),
                                   request.get("liveContext"))
        if action == "rank":
            return engine.rank_choices(request.get("hero"), request.get("choices", []),
                                     source=request.get("source") or "manual", owned=request.get("owned"))
        if action == "demo":
            return engine.demo(request.get("hero"), request.get("owned"))
        if action == "ocr":
            return self.recognize(request)
        if action == "live_state":
            live = read_live_game_data(self.hero_lookup)
            live["lcu"] = self.lcu.read_state()
            from hexgod.vision.pipeline import find_lol_window_context
            window = find_lol_window_context()
            live['gameWindowPresent'] = bool(window.get('hwnd'))
            live['gameWindowHwnd'] = window.get('hwnd')
            return live
        if action == "click":
            self.ocr.ensure_context(request.get('contextId'))
            self.ocr.journal.start(request.get('sessionKey') or 'offline', hero=request.get('hero'),
                                   evidence_kind=request.get('evidenceKind'))
            pending = self.ocr.selection_tracker.observe_click(
                int(request.get("x")), int(request.get("y")), int(request.get("hwnd")),
                int(request.get("atMs")), geometry=request.get("geometry"))
            self.ocr.journal.summary['clicks'] += 1
            self.ocr.journal.event('click', x=request['x'], y=request['y'], atMs=request['atMs'],
                                   inputSource=request.get('inputSource', 'unknown'),
                                   geometry=request.get('geometry'), pending=pending, reason=self.ocr.selection_tracker.last_event)
            return {'pending': pending, 'reason': self.ocr.selection_tracker.last_event}
        if action == "reset_tracking":
            self.ocr.reset()
            return {"reset": True}
        raise ValueError("不支持的操作。")


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    backend = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                request = {}
                raise ValueError('请求必须为 JSON 对象。')
            if backend is None:
                backend = Backend(use_remote='--legacy-fixture' not in sys.argv)
            response = {"id": request.get("id"), "ok": True, "result": backend.dispatch(request)}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            response = {"id": request.get("id"), "ok": False, "error": str(error)}
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
