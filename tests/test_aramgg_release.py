"""Production provider and real OCR regressions for the 0.11 migration."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend
from aramgg_data import logical_path
from auto_selection import AutomaticSelectionTracker
from match_journal import MatchJournal
from title_reader import title_regions

FIXTURES = ROOT / 'tests/fixtures/augment-ocr'


class AramggReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend()

    def test_production_catalog_comes_from_verified_release(self):
        engine = self.backend.engine
        manifest = engine.read('manifest.json')
        self.assertEqual(engine.meta['dataVersion'], manifest['dataVersion'])
        self.assertEqual(len(engine.heroes), 173)
        self.assertGreaterEqual(len(engine.augments), 211)
        for entry in manifest['files']:
            path = engine.folder / entry['path']
            if path.exists():
                self.assertEqual('sha256-' + hashlib.sha256(path.read_bytes()).hexdigest(), entry['hash'])
        self.assertIsNone(next(h for h in engine.catalog()['heroes'] if h['name']=='祖安怒兽')['games'])

    def test_manifest_paths_cannot_escape_cache(self):
        for value in ('../foo.json', '/foo.json', 'champions/../foo.json', 'C:/foo.json'):
            with self.assertRaises(ValueError): logical_path(value)

    def test_source_rates_and_sample_patch_are_not_relabelled(self):
        engine = self.backend.engine
        for hero in engine.heroes:
            detail = engine.hero_detail(hero)
            for row in detail['items']:
                if row['winRate'] is not None:
                    self.assertTrue(0 <= row['winRate'] <= 1)
            source = next((a for a in engine.detail(hero)['augments'] if (a.get('stats') or {}).get('winRateGamePatch')), None)
            if source:
                choice = engine.rank_choices(hero,[source['name']])['choices'][0]
                self.assertEqual(choice['evidencePatch'], source['stats']['winRateGamePatch'])

    def test_core_order_follows_inventory_components_and_excludes_owned(self):
        engine = self.backend.engine
        base = engine.build_path('复仇焰魂', [], [], True)
        self.assertEqual(base['purchaseOrder']['route'], [2503,3020,6653])
        self.assertEqual(base['nextItems'][0]['id'], 2503)
        bought = engine.build_path('复仇焰魂', [], [2503,3020], True)
        self.assertEqual(bought['nextItems'][0]['id'], 6653)
        unknown = engine.build_path('复仇焰魂', [], [], False)
        self.assertIsNone(unknown['purchaseOrder']['nextId'])
        invested = engine.build_path('复仇焰魂', [], [3147], True)
        self.assertGreater(invested['nextItems'][0]['componentCredit'], 0)

    def test_recommendation_follows_reference_rank_not_an_invented_win_rate_score(self):
        engine = self.backend.engine
        rows = sorted((a for a in engine.detail('祖安怒兽')['augments'] if (a.get('stats') or {}).get('rank')),
                      key=lambda a:a['stats']['rank'])
        selected = [rows[25], rows[0], rows[50]]
        choices = engine.rank_choices('祖安怒兽',[a['name'] for a in selected])['choices']
        self.assertEqual([c['name'] for c in choices], [a['name'] for a in selected])
        self.assertEqual([c['recommended'] for c in choices], [False,True,False])
        if choices[1]['evidencePatch'] != engine.meta['version']:
            self.assertIsNone(choices[1]['delta'])

    def test_low_sample_starter_is_not_promoted_and_stack_counts_survive(self):
        engine = self.backend.engine
        self.assertEqual(engine.starter({'startingItems':[{'itemIds':[2003], 'games':1}]},[])['items'], [])
        plan = engine.starter({'startingItems':[{'itemIds':[2003,2003], 'games':100}]},[2003,2003])
        self.assertTrue(plan['complete'])
        plan = engine.starter({'startingItems':[{'itemIds':[2003,2003], 'games':100}]},[2003],{'2003':2})
        self.assertTrue(plan['complete'])

    def test_selected_task_item_uses_current_augment_description(self):
        engine = self.backend.engine
        result = engine.build_path('祖安怒兽', ['钢化你心'], [], True)
        self.assertIn(3084, [i['id'] for i in result['synergyItems']])
        result = engine.build_path('祖安怒兽', ['钢化你心'], [3084], True)
        self.assertNotIn(3084, [i['id'] for i in result['synergyItems']])

    def test_bright_blank_frame_does_not_look_like_card_borders(self):
        for color in ('white','black','#9e9e9e'):
            self.assertFalse(title_regions(Image.new('RGB',(2560,1440),color))[2])

    def test_real_reference_at_both_monitor_resolutions(self):
        with tempfile.TemporaryDirectory() as directory:
            for size in ((2560,1440),(2560,1600),(1280,720)):
                with self.subTest(size=size), Image.open(FIXTURES/'full-three-cards.png') as image:
                    path = Path(directory)/'frame.png'
                    image.resize(size,Image.Resampling.LANCZOS).save(path)
                    result = self.backend.recognize({'hero':'祖安怒兽','image':str(path)})
                    self.assertEqual([c['name'] for c in result['choices']], ['未收录：裁决使','超凡邪恶','神射法师'])

    def test_six_options_with_each_slot_rerolled_are_logged_and_selection_is_confirmed(self):
        # Actual detector + recognizer, images based on upstream full-panel
        # geometry. Rewrite only title pixels to exercise each independent slot.
        names = ['生机迸发','闪现向前','巨像的勇气','面包和黄油','你摸不到','战争交响乐']
        backend = Backend()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend.ocr.journal = MatchJournal(root/'logs')
            image = Image.open(FIXTURES/'full-three-cards.png').convert('RGB').resize((2560,1440))
            rects = title_regions(image)[0]
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',38)
            choices = names[:3]
            moment = 10000
            for change in (-1,0,1,2):
                if change >= 0: choices[change] = names[3+change]
                frame = image.copy(); draw = ImageDraw.Draw(frame)
                for rect,name in zip(rects,choices):
                    draw.rectangle(rect,fill=(15,24,28))
                    draw.text(((rect[0]+rect[2])/2, (rect[1]+rect[3])/2),name,font=font,anchor='mm',fill=(248,244,220))
                path=root/'frame.png'; frame.save(path)
                context={'foreground_matches_lol':True,'lol_window_bbox':[2560,4,5120,1444],
                         'lol_window_hwnd':41,'timestamp_ms':moment}
                with patch('ocr_service.capture_screen',return_value=path), patch('ocr_service.capture_context_for_image',return_value=context):
                    result=backend.recognize({'hero':'祖安怒兽','sessionKey':'six-options'})
                self.assertEqual([c['name'] for c in result['choices']],choices)
                moment+=1000
            geometry=result['trackingGeometry']; box=geometry['hitboxes'][2]
            pending=backend.dispatch({'action':'click','x':(box[0]+box[2])//2,'y':(box[1]+box[3])//2,
                'hwnd':41,'atMs':moment-500,'geometry':geometry,'sessionKey':'six-options'})
            self.assertEqual(pending['pending'],names[5])
            Image.new('RGB',(2560,1440),'white').save(path)
            for offset in (0,700):
                context['timestamp_ms']=moment+offset
                with patch('ocr_service.capture_screen',return_value=path), patch('ocr_service.capture_context_for_image',return_value=context):
                    result=backend.recognize({'hero':'祖安怒兽','sessionKey':'six-options'})
            self.assertEqual(result['autoSelection'],names[5])
            summary=json.loads((root/'logs/six-options/summary.json').read_bytes())
            self.assertEqual(set(summary['observedTitles']),set(names))
            self.assertEqual(summary['confirmed'],[names[5]])
            events=[json.loads(line) for line in (root/'logs/six-options/events.jsonl').read_text('utf-8').splitlines()]
            self.assertEqual([e['slot'] for e in events if e['event']=='slot_changed'],[1,2,3])
            self.assertTrue(list((root/'logs/six-options').glob('title-*.jpg')))


if __name__ == '__main__': unittest.main()
