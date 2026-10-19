"""ARAMGG public data adapter. Keeps source patches and missing samples explicit."""
from collections import Counter
from functools import cmp_to_key
import json
import re
import time
from pathlib import Path
from aramgg_data import active
from recommendations import RecommendationEngine, number, reliability, DATA_DIR
from purchase_policy import PurchasePolicy
from starter_builds import OpeningTracker
from augment_mechanics import AugmentMechanics
from synergy import wilson_lower_bound


def sample_quality(games, evidence_patch, ranking_patch):
    if games is None:
        return '样本量未公开'
    count = number(games)
    label = ('小样本' if count < 1000 else
             '样本有限' if count < 10000 else '样本较多')
    if evidence_patch and evidence_patch != ranking_patch:
        label += ' · 跨版本'
    return label


class AramggEngine:
    canonical_augment = RecommendationEngine.canonical_augment
    normalize_owned = RecommendationEngine.normalize_owned
    validate_hero = RecommendationEngine.validate_hero

    def __init__(self, folder=None):
        self.folder = folder or active()
        if self.folder is None:
            raise ValueError('ARAMGG 数据未就绪，请先运行数据同步。')
        self.config = self.read('config.json')
        self.meta = {'version': self.config['gamePatch'], 'dataVersion': self.config['dataVersion'],
                     'report_date': self.config['generatedAt'][:10], 'source': 'ARAMGG / data.dtodo.cn'}
        self.augments = {a['name']: a for a in self.read('augments.json')['data']}
        self.aliases = {name: [name] for name in self.augments}
        self.heroes = {c['title']: c for c in self.read('champions.json')['data']}
        self.hero_lookup = {str(alias).casefold(): name for name, c in self.heroes.items()
                            for alias in (name, c['name'], c['alias'], str(c['id']))}
        self.details = {}
        self.items = {str(i['id']): i for i in self.read('items.json')['data']}
        self.item_catalog = {key: {'name': i['name'], 'from': [str(n) for n in i.get('from', [])],
            'gold': {'total': i.get('priceTotal', 0)}, 'tags': i.get('categories', [])} for key, i in self.items.items()}
        self.policy = PurchasePolicy(self.item_catalog, {})
        resource_file = Path(__file__).resolve().parent / 'hero_resources.json'
        resource_data = json.loads(resource_file.read_text('utf-8-sig')) if resource_file.exists() else {}
        self.mechanics = AugmentMechanics(self.augments, self.items, self.policy,
            resource_data.get('resources', {}) if resource_data.get('patch') == self.meta['version']+'.1' else {})
        self.opening = OpeningTracker()

    def read(self, path):
        return json.loads((self.folder / path).read_bytes())

    def detail(self, hero):
        champion = self.validate_hero(hero)
        if hero not in self.details:
            self.details[hero] = self.read(f"champions/{champion['id']}.json")
        return self.details[hero]

    def catalog(self):
        rows = sorted(self.heroes.items(), key=lambda pair: number((pair[1].get('stats') or {}).get('winRate')), reverse=True)
        return {'meta': self.meta, 'heroes': [{'name': name, 'fullName': c['name'],
            'aliases': c['alias'], 'winRate': (c.get('stats') or {}).get('winRate'),
            'games': (c.get('stats') or {}).get('games'), 'pickRate': (c.get('stats') or {}).get('pickRate')}
            for name, c in rows], 'augments': sorted(self.augments),
            'presets': json.loads((DATA_DIR / 'augment_presets.json').read_text('utf-8')), 'jointDataStatus': 'unavailable'}

    def combination_evidence(self, hero, owned, candidate):
        # Exact three-ID lookup only. Published trios may overlap; never sum
        # them to infer missing pair rates or a baseline for marginal gains.
        names = set(owned) | {candidate}
        if len(owned) != 2 or len(names) != 3 or any(n not in self.augments for n in names): return None
        ids = {self.augments[n]['id'] for n in names}
        matches = []
        for trio in self.detail(hero).get('augmentTrios', []):
            stats = trio.get('stats') or {}
            games, wins = stats.get('games'), stats.get('wins')
            if set(trio.get('augmentIds', [])) == ids and type(games) is int and type(wins) is int and games > 0 and 0 <= wins <= games:
                matches.append({'winRate': wins/games, 'wins': wins, 'games': games,
                    'patch': stats.get('gamePatch'), 'date': stats.get('date'), 'source': stats.get('source'),
                    'label': '历史三项组合 · 无选择轮次', 'reliable': games >= 1000 and stats.get('gamePatch') == self.meta['version']})
        return max(matches, key=lambda r: (r['patch'] == self.meta['version'], r['date'] or '', r['games'])) if matches else None

    def rank_choices(self, hero, texts, confidences=None, source='screen', owned=None):
        self.validate_hero(hero)
        owned = self.normalize_owned(owned)
        stats_by_name = {a['name']: a.get('stats') or {} for a in self.detail(hero).get('augments', [])}
        baseline = number((self.heroes[hero].get('stats') or {}).get('winRate'), .5)
        choices = []
        for index in range(3):
            raw = texts[index] if index < len(texts) else ''
            name = self.canonical_augment(raw)
            row = stats_by_name.get(name, {})
            games = row.get('games')
            rate = row.get('winRate')
            known_rate = type(rate) in (int, float) and 0 <= rate <= 1
            patch = row.get('winRateGamePatch') or row.get('gamePatch') or self.meta['version']
            date = row.get('winRateDate') or row.get('date') or ''
            rank = row.get('rank')
            tier = row.get('tier')
            priority_patch = row.get('gamePatch') or self.meta['version']
            reason = ('ARAMGG 排名 #' + str(rank) + '（' + priority_patch + '） · ') if rank else 'ARAMGG · '
            reason += ('胜率样本 ' + patch + ' / ' + date) if known_rate else '未提供该英雄的胜率样本'
            if owned:
                reason += ' · 单项统计，未冒充已选组合胜率'
            choices.append({'slot': index + 1, 'raw': raw, 'name': name or '待识别', 'matched': bool(name),
                'known': bool(name), 'hasStats': known_rate, 'winRate': rate if known_rate else None,
                'games': games, 'sampleKnown': games is not None, 'pickRate': row.get('pickRate'),
                'rarity': self.augments.get(name, {}).get('rarityDisplayName', ''),
                'delta': number(rate) - baseline if patch == self.meta['version'] and known_rate else None, 'reason': reason,
                'reliability': sample_quality(games, patch, priority_patch),
                'recommended': False, 'score': number(rate) * number(games) / (number(games) + 200),
                'priorityRank': rank, 'priorityTier': tier, 'priorityPatch': priority_patch,
                'augmentId': self.augments.get(name, {}).get('id'),
                'evidencePatch': patch, 'evidenceDate': date, 'source': row.get('winRateSource') or row.get('source'),
                'combinationEvidence': self.combination_evidence(hero, owned, name) if name else None})
        eligible = [c for c in choices if c['matched'] and
                    (number(c['priorityRank']) > 0 or number(c['priorityTier']) > 0 or
                     (c['hasStats'] and number(c['games']) >= 30))]
        # aramgg_client/src/shared/augment-ranking.ts compareAugmentPriority:
        # authoritative rank, otherwise tier then pick rate then stable ID.
        def compare(left, right):
            a, b = number(left['priorityRank']), number(right['priorityRank'])
            if a > 0 and b > 0 and a != b:
                return a-b
            for key, default, direction in [('priorityTier',float('inf'),1), ('pickRate',-1,-1), ('augmentId',float('inf'),1)]:
                a, b = number(left.get(key),default), number(right.get(key),default)
                if a != b: return direction*(a-b)
            return 0
        if eligible:
            min(eligible, key=cmp_to_key(compare))['recommended'] = True
        return {'hero': hero, 'choices': choices, 'source': source, 'owned': owned,
                'matchedCount': sum(c['matched'] for c in choices), 'complete': all(c['matched'] for c in choices),
                'time': time.strftime('%H:%M:%S'), 'conditionalStatus': 'unavailable' if owned else 'not_needed',
                'dataSource': self.meta}

    def demo(self, hero, owned=None):
        names = [a['name'] for a in sorted(self.detail(hero).get('augments', []),
                 key=lambda a: number((a.get('stats') or {}).get('games')), reverse=True)][:3]
        return self.rank_choices(hero, names, source='demo', owned=owned)

    def usable(self, value):
        item = self.items.get(str(value), {})
        return item and item.get('inStore') is not False and number(item.get('priceTotal')) > 0

    def augment_items(self, owned, build, ids, known):
        # Compatibility surface; build_path supplies actual hero/resource data.
        route = [i for core in build.get('coreItems', [])[:1] for i in core.get('itemIds', []) if self.usable(i)]
        return self.mechanics.plan(owned, route, build, ids, known, '')['candidates']

    def row(self, item_id, stats=None):
        item = self.items[str(item_id)]
        stats = stats or {}
        games, wins = stats.get('games'), stats.get('wins')
        # Some upstream situational winRate fields are signed scores. Use
        # explicit wins/games where supplied; never display a negative rate.
        rate = wins / games if type(games) is int and games > 0 and type(wins) is int and 0 <= wins <= games else None
        return {'id': int(item_id), 'name': item['name'], 'games': games, 'winRate': rate,
                'pickRate': stats.get('pickRate'), 'reliability': reliability(number(games)) if games is not None else '未公布样本',
                'reason': 'ARAMGG 装备组合参考', 'joint': None, 'preferred': False}

    def route(self, hero, ids):
        candidates = []
        owned = self.policy.owned_routes(ids)
        for build in self.detail(hero).get('builds', []):
            for core in build.get('coreItems', []):
                route = list(dict.fromkeys(int(i) for i in core.get('itemIds', []) if self.usable(i)))
                if not route:
                    continue
                overlap = sum(str(i) in owned for i in route)
                investment = sum(self.policy.credit(i, ids) for i in route if str(i) not in owned)
                candidates.append(((overlap, investment, number(core.get('games'))), route, core, build))
        return max(candidates, key=lambda c: c[0])[1:] if candidates else ([], {}, {})

    def starter(self, build, ids, counts=None):
        candidates = []
        for row in build.get('startingItems', []):
            values = row.get('itemIds', [])
            if not values or not all(self.usable(i) for i in values):
                continue
            cost = sum(number(self.items[str(i)].get('priceTotal')) for i in values)
            if 0 < cost <= 1400 and number(row.get('games')) >= 30:
                candidates.append((number(row.get('games')), cost, row))
        if not candidates:
            return {'items': [], 'totalCost': 0, 'remainingGold': 1400, 'remainingCost': 0, 'complete': True,
                    'reason': '来源没有样本足够且在 1400 金预算内的出门组合，显示核心装备参考', 'basis': 'unavailable'}
        _, cost, source = max(candidates, key=lambda c: c[0])
        owned = Counter(ids)
        if isinstance(counts, dict):
            for item in owned:
                value = counts.get(str(item))
                if type(value) is int and 1 <= value <= 10:
                    owned[item] = value
        available = list(owned.elements())
        items = []
        for item, count in Counter(source['itemIds']).items():
            unit = self.items[str(item)]['priceTotal']
            credit = 0
            for _ in range(count):
                credit += self.policy.credit(item, available)
                available = [int(i) for i in self.policy.credit(item, available, remaining=True)]
            remaining_cost = max(0, count * unit - credit)
            items.append({**self.row(item), 'quantity': count, 'ownedQuantity': min(count, owned[item]),
                          'remainingCost': remaining_cost, 'consumable': 'Consumable' in self.items[str(item)].get('categories', [])})
        return {'items': items, 'totalCost': int(cost), 'remainingGold': int(1400-cost),
                'complete': all(i['remainingCost'] == 0 for i in items),
                'remainingCost': sum(i['remainingCost'] for i in items), 'reason': 'ARAMGG 出门组合 · ' + str(source.get('games', '未公布')) + ' 场',
                'basis': 'aramgg', 'games': source.get('games')}

    def regular_items(self, build, route, ids, known):
        # Only explicit single-item wins/games. A core trio's rate must not be
        # copied onto each constituent item, nor summed across overlapping trios.
        rows = {row['id']: {**self.row(row['id'], row), 'evidenceBasis': 'source_item',
                           'reason': '英雄常规出装统计 · 未计入已选海克斯'}
                for row in build.get('situationalItems', []) if self.usable(row.get('id'))}
        for item in route:
            rows.setdefault(item, {**self.row(item), 'evidenceBasis': 'source_core_reference',
                                   'reason': '来源核心装备 · 未公布单件胜场和场数'})
        candidates = self.policy.decorate(list(rows.values()), ids, known)
        # Restore the previous 400-game prior and sub-250 sample penalty.
        # The prior must come from this build's queue, not the Mayhem hero rate.
        # Rank by the conservative bound of the shrunk estimate so a few hundred
        # observations do not routinely beat thousands on a tiny rate difference.
        baseline = number((build.get('stats') or {}).get('winRate'), .5)
        for row in candidates:
            games, rate = number(row['games']), row['winRate']
            row['sortScore'] = None
            row['adjustedWinRate'] = None
            if rate is not None and games > 0:
                row['adjustedWinRate'] = (games * rate + 400 * baseline) / (games + 400)
                row['sortScore'] = wilson_lower_bound(games * rate + 400 * baseline, games + 400)
                if games < 250:
                    row['sortScore'] -= .12 * (250 - games) / 250
        substantial = [c for c in candidates if c['winRate'] is not None and number(c['games']) >= 250]
        # Count at most one shoe when deciding whether three robust choices exist.
        robust_count = sum('Boots' not in self.items[str(c['id'])].get('categories', []) for c in substantial)
        robust_count += int(any('Boots' in self.items[str(c['id'])].get('categories', []) for c in substantial))
        ranked = sorted(substantial if robust_count >= 3 else candidates, key=lambda c: (
            -(c['sortScore'] if c['sortScore'] is not None else -1), -number(c['games']), c['id']))
        result, boots = [], False
        for row in ranked:
            is_boot = 'Boots' in self.items[str(row['id'])].get('categories', [])
            if is_boot and boots: continue
            boots = boots or is_boot
            result.append(row)
            if len(result) == 3: break
        return result

    def build_path(self, hero, owned=None, item_ids=None, items_known=False, live_context=None):
        owned = self.normalize_owned(owned)
        ids = [i for i in (item_ids or []) if type(i) is int and i > 0] if items_known else []
        route, core, build = self.route(hero, ids)
        original_route = list(route)
        regular = self.regular_items(build, original_route, ids, items_known)
        profile = self.mechanics.plan(owned, route, build, ids, items_known, self.heroes[hero]['alias'])
        route = profile['route']
        mechanism = profile['basis'] == 'mechanism_rule'
        tips_by_id = {row['id']: row for row in profile['candidates']}
        rows = [tips_by_id.get(i, {**self.row(i, core if not mechanism else None),
                'evidenceBasis': 'mechanism_route' if mechanism else 'source_combo',
                'reason': '海克斯调整后的路线 · 未提供组合胜率' if mechanism else 'ARAMGG 装备组合参考'}) for i in route]
        candidates = self.policy.decorate(rows, ids, items_known)
        # Quest-specific exceptions have already been validated by the planner.
        for row in profile['candidates']:
            if row['required'] and row['id'] in route and not any(c['id'] == row['id'] for c in candidates): candidates.append(row)
        if candidates:
            candidates.sort(key=lambda c: (-int(c.get('required', False)), -c['componentCredit'], route.index(c['id'])))
        extra = [self.row(row['id'], row) for row in build.get('situationalItems', []) if self.usable(row.get('id'))]
        if not candidates and not mechanism:
            candidates = sorted(self.policy.decorate(extra, ids, items_known), key=lambda r: number(r['games']), reverse=True)[:3]
        live = live_context or {}
        starter = self.starter(build, ids, live.get('itemCounts'))
        stage, opening_reason = self.opening.stage(hero, starter, ids, items_known, live,
            self.item_catalog, {i for i in self.items if self.items[i].get('priceTotal', 0) >= 1800})
        order = {'basis': 'inventory_unknown' if not items_known else 'mechanism_rule' if mechanism else 'aramgg_core' if route else 'situational',
                 'route': route, 'routeNames': [self.items[str(i)]['name'] for i in route],
                 'nextId': candidates[0]['id'] if candidates and items_known else None,
                 'reason': profile['reason'] if mechanism else '来源核心组合顺序参考，不代表逐步购买胜率',
                 'originalRoute': original_route}
        return {'hero': hero, 'owned': owned, 'itemsKnown': items_known, 'nextItems': candidates[:1] if items_known else candidates[:3],
                'regularItems': regular, 'regularSort': 'sample_adjusted_win_rate',
                'laterItems': candidates[1:], 'purchaseOrder': order, 'starterPlan': starter,
                'recommendationStage': stage, 'openingReason': opening_reason,
                'synergyItems': profile['candidates'],
                'synergySort': 'mechanism_priority', 'itemConditionalStatus': 'unavailable',
                'buildProfile': {k:v for k,v in profile.items() if k != 'candidates'}, 'dataSource': self.meta,
                'sourceNote': 'ARAMGG 基础装备统计 · 来源队列 ' + str(build.get('queueId', '未公布'))}

    def hero_detail(self, hero, owned=None):
        c = self.validate_hero(hero)
        stats = c.get('stats') or {}
        path = self.build_path(hero, owned)
        route = path['purchaseOrder']['route']
        augments = sorted(self.detail(hero).get('augments', []), key=lambda a: number((a.get('stats') or {}).get('games')), reverse=True)
        return {'hero': hero, 'winRate': stats.get('winRate'), 'games': stats.get('games'), 'sampleKnown': stats.get('games') is not None,
                'pickRate': stats.get('pickRate'), 'owned': self.normalize_owned(owned), 'coreItems': path['nextItems'] + path['laterItems'],
                'items': path['regularItems'], 'synergyItems': path['synergyItems'][:3],
                'starterPlan': path['starterPlan'], 'starterItems': path['starterPlan']['items'],
                'buildProfile': path['buildProfile'], 'jointDataStatus': 'unavailable', 'jointContextAvailable': False,
                'requiredItems': [self.items[str(i)]['name'] for i in path['buildProfile']['requiredItemIds']],
                'mechanismItems': [i['name'] for i in path['synergyItems'][:3]],
                'topAugments': [{'name': a['name'], 'winRate': (a.get('stats') or {}).get('winRate'), 'games': (a.get('stats') or {}).get('games')} for a in augments[:5]],
                'dataSource': self.meta, 'sourceNote': path['sourceNote']}
