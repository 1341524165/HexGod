"""Inventory-aware purchase planning, independent of marginal win-rate ranking.

Core lists are explicitly heuristic plans, not observed purchase sequences.
Recipe credit consumes a multiset so one component cannot pay for two branches.
"""
from collections import Counter


class PurchasePolicy:
    def __init__(self, catalog, route_ids):
        self.catalog, self.route_ids = catalog, route_ids
        self.alternative_groups = [
            {'3078', '3100', '6662', '3508', '2510'},  # Spellblade alternatives.
            {'3036', '3033', '6694'},  # Last Whisper alternatives.
            {'3135', '3137'},          # Percent magic penetration alternatives.
            {'3053', '3156', '6673'},  # Lifeline alternatives.
            {'3074', '3748', '6698'},  # Tiamat alternatives.
        ]

    def ancestors(self, item_id, seen=None):
        seen = set() if seen is None else seen
        item_id = str(item_id)
        if item_id in seen:
            return set()
        seen.add(item_id)
        parents = set(self.catalog.get(item_id, {}).get('from', []))
        return parents | {ancestor for parent in parents for ancestor in self.ancestors(parent, seen)}

    def owned_routes(self, ids):
        result = {self.route_ids.get(str(i), str(i)) for i in ids}
        for item in ids:
            result.update(self.ancestors(str(item)))
        return result

    def alternative_owned(self, target, purchased):
        target = str(target)
        for group in self.alternative_groups:
            if target in group and (group - {target}) & purchased:
                return True
        if 'Boots' in self.catalog.get(target, {}).get('tags', []):
            for item in purchased:
                if 'Boots' in self.catalog.get(item, {}).get('tags', []) and item != '1001':
                    if item != target and item not in self.ancestors(target):
                        return True
        return False

    def credit(self, target, ids, remaining=False):
        available = Counter(str(i) for i in ids)
        def consume(item_id, stack=()):
            if item_id in stack:
                return 0
            item = self.catalog.get(item_id, {})
            if available[item_id] > 0:
                available[item_id] -= 1
                return int(item.get('gold', {}).get('total', 0))
            return sum(consume(child, stack+(item_id,)) for child in item.get('from', []))
        value = consume(str(target))
        return list(available.elements()) if remaining else value

    def decorate(self, candidates, ids, known):
        result = []
        purchased = self.owned_routes(ids) if known else set()
        for row in candidates:
            item_id = str(row['id'])
            if known and (item_id in purchased or self.alternative_owned(item_id, purchased)):
                continue
            total = int(self.catalog[item_id]['gold']['total'])
            credit = self.credit(item_id, ids) if known else 0
            if known and len(ids) >= 6 and credit == 0:
                continue
            result.append({**row, 'totalCost': total, 'componentCredit': credit,
                           'remainingCost': max(0, total-credit), 'orderReason': ''})
        return result

    def plan(self, candidates, formula_ids, purchased, completed_count, known, explicit):
        if not candidates:
            return [], explicit
        by_id = {row['id']: row for row in candidates}
        if not known:
            return candidates, {**explicit, 'basis': 'inventory_unknown', 'nextId': None,
                                'reason': '库存未同步，展示方案参考，暂不判断下一件。'}
        if explicit['basis'] == 'curated' and explicit['nextId'] in by_id:
            by_id[explicit['nextId']]['orderReason'] = '明确核心顺序参考'
            return candidates, explicit
        if explicit.get('route'):
            return candidates, explicit  # An explicit plan has finished.
        route = list(dict.fromkeys(int(self.route_ids.get(str(i), str(i))) for i in formula_ids))
        remaining = [i for i in route if str(i) not in purchased and i in by_id]
        if not route:
            return candidates, explicit
        # Finish a substantial investment before starting an unrelated item.
        invested = [row for row in candidates if row['componentCredit'] >= 1100 and
                    row['componentCredit'] / max(1, row['totalCost']) >= 0.35 and
                    (row['games'] >= 250 or row['id'] in route)]
        invested.sort(key=lambda row: (row['componentCredit']/row['totalCost'],
                                      row['id'] in remaining[:1], row['games']), reverse=True)
        route_owned = sum(str(i) in purchased for i in route)
        follow_core = bool(remaining) and (completed_count == 0 or route_owned > 0 or completed_count == 1)
        wanted = invested[0]['id'] if invested else remaining[0] if follow_core else None
        if wanted is not None:
            by_id[wanted]['orderReason'] = '先合成已投入的小件' if invested else '本地核心方案 · 启发式顺序'
            # Later core steps are a preview, not substitutes for this step.
            later = set(remaining[remaining.index(wanted)+1:]) if wanted in remaining else set()
            candidates = [row for row in candidates if row['id'] not in later]
            candidates.sort(key=lambda row: row['id'] == wanted, reverse=True)
            return candidates, {'basis': 'components' if invested else 'formula_reference',
                'route': route, 'nextId': wanted, 'games': None,
                'reason': '按已有小件合成；不代表路径胜率。' if invested else
                          '沿用本地精选核心表的顺序作为启发式方案；没有购买位置样本。'}
        return candidates, {**explicit, 'basis': 'situational', 'route': [], 'nextId': None,
            'reason': '核心已完成或当前出装偏离方案；以下为可选补充，不假定固定顺序。'}
