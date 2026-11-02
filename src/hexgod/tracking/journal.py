"""Bounded local match diagnostics; only title crops, no desktop screenshots."""
import json
from pathlib import Path
import re
import time
from PIL import Image


class MatchJournal:
    def __init__(self, root=None, evidence_kind='unclassified'):
        from hexgod.paths import LOG_DIR
        self.root = root or LOG_DIR / 'matches'
        self.session = None
        self.folder = None
        self.previous = None
        self.summary = {}
        self.crops = 0
        self.last_selection = None
        self.evidence_kind = evidence_kind

    def start(self, session, *, hero=None, evidence_kind=None):
        session = re.sub(r'[^A-Za-z0-9_-]', '_', str(session or 'unassigned'))[:90]
        if session == self.session:
            if hero:
                self.summary['hero'] = hero
            return
        self.session = session
        self.folder = self.root / session
        self.folder.mkdir(parents=True, exist_ok=True)
        self.previous = None
        self.crops = 0
        kind = evidence_kind or ('offline' if session == 'offline' else self.evidence_kind)
        self.summary = {'session': session, 'hero': hero, 'evidenceKind': kind,
                        'frames': 0, 'panelFrames': 0, 'completeFrames': 0,
                        'partialFrames': 0, 'clicks': 0, 'confirmed': [], 'inferred': [], 'unresolved': 0, 'observedTitles': []}
        self.last_selection = None
        from hexgod.platform.display import monitors
        self.event('session_started', monitors=monitors(), backendVersion='0.18',
                   hero=hero, evidenceKind=kind)

    def event(self, kind, **values):
        if self.folder is None:
            self.start('unassigned')
        record = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'event': kind, **values}
        path = self.folder / 'events.jsonl'
        # Bound an individual match even if a process is left running for days.
        if path.exists() and path.stat().st_size > 8*1024*1024:
            path.replace(self.folder / 'events.previous.jsonl')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        (self.folder / 'summary.json').write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding='utf-8')
        confirmed = self.summary['confirmed']
        (self.folder / '本局识别摘要.txt').write_text(
            f"本局：{self.session}\n选项画面：{self.summary['panelFrames']} 帧；完整识别：{self.summary['completeFrames']} 帧；"
            f"部分识别：{self.summary['partialFrames']} 帧\n卡片点击：{self.summary['clicks']} 次\n"
            f"已确认选择：{len(confirmed)} 项 {'、'.join(confirmed)}\n"
            f"悬停推测选择：{len(self.summary['inferred'])} 项 {'、'.join(self.summary['inferred'])}\n"
            f"未完成确认：{self.summary['unresolved']} 次\n"
            "events.jsonl 中包含每张卡原文、刷新、点击判定和屏幕坐标；title-*.jpg 是诊断用标题裁图。\n",
            encoding='utf-8-sig')

    def frame(self, result, payload):
        if result.get('hero'):
            self.summary['hero'] = result['hero']
        self.summary['frames'] += 1
        panel = result['augmentPanelVisible']
        self.summary['panelFrames'] += bool(panel)
        self.summary['completeFrames'] += bool(panel and result['matchedCount'] == 3)
        self.summary['partialFrames'] += bool(panel and result['matchedCount'] < 3)
        choices = result.get('choices', [])
        names = [c['name'] if c['matched'] else None for c in choices]
        for name in names:
            if name and name not in self.summary['observedTitles']:
                self.summary['observedTitles'].append(name)
        diagnostic = result.get('selectionDiagnostic')
        if diagnostic != self.last_selection:
            if diagnostic in ('confirmation_timeout', 'clicked_slot_changed', 'game_window_changed'):
                self.summary['unresolved'] += 1
                self.event('selection_unresolved', reason=diagnostic)
            self.last_selection = diagnostic
        changed = panel and names != self.previous
        if changed:
            if self.previous is not None:
                for slot, (before, after) in enumerate(zip(self.previous, names), 1):
                    if before and after and before != after:
                        self.event('slot_changed', slot=slot, before=before, after=after)
            self.previous = names
        crops = []
        if panel and (changed or (result['matchedCount'] < 3 and self.summary['panelFrames'] % 10 == 1)) and self.crops < 90:
            source = payload.get('source')
            rects = payload.get('titleRegionRects') or []
            if source and Path(source).exists() and rects:
                with Image.open(source) as image:
                    for slot, rect in enumerate(rects, 1):
                        if rect:
                            path = self.folder / f"title-{self.summary['frames']:05}-{slot}.jpg"
                            image.crop(rect).save(path, quality=90)
                            crops.append(path.name)
                            self.crops += 1
        self.event('frame', panel=panel, hero=self.summary.get('hero'), matched=result['matchedCount'], names=names,
                   slots=(result.get('ocrDebug') or {}).get('selected'),
                   capture=payload.get('capture_context'), layout=(payload.get('preset') or {}).get('key'),
                   imageSize=payload.get('imageSize'), detectedCards=payload.get('titleCardRects'),
                   reason=result.get('captureReason'), geometry=result.get('trackingGeometry'),
                   selection=result.get('selectionDiagnostic'), crops=crops, timings=result.get('timings'))
