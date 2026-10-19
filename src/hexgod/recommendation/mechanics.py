"""Current-release mechanism analysis and inventory-aware item planning.

Scores are deterministic mechanism preferences, not measured win probabilities.
Descriptions, item IDs and eligibility always come from the active data release.
"""
from collections import defaultdict
from html import unescape
from functools import lru_cache
import re


def plain(text):
    return unescape(re.sub(r'%i:[^%]+%|<[^>]+>', '', text or ''))


class AugmentMechanics:
    def __init__(self, augments, items, policy, resources=None):
        self.augments, self.items, self.policy = augments, items, policy
        self.resources = resources or {}
        self.by_name = defaultdict(list)
        for key, item in items.items():
            if item.get('inStore') is not False and item.get('priceTotal', 0) >= 1800:
                self.by_name[item['name']].append(int(key))
        # Prefer the ordinary purchasable entry to mode-specific duplicates.
        self.names = {name: min(ids) for name, ids in self.by_name.items()}

    @lru_cache(maxsize=512)
    def analyze(self, name):
        augment = self.augments.get(name)
        if augment is None:
            return {'name': name, 'status': 'unknown', 'features': [], 'required': [],
                    'alternatives': [], 'reason': '当前数据没有此强化的机制描述'}
        text = plain(augment.get('description'))
        features, required, alternatives = [], [], []
        refs = [item for item in self.names if item in text]
        # Upgrade names remain authoritative when upstream tooltip contains '?'.
        if name.startswith('升级：'):
            target = name.split('：', 1)[1]
            if target == '中娅': target = '中娅沙漏'
            if target in self.names:
                required = [self.names[target]]
            elif target == '耀光':
                alternatives = [self.names[n] for n in self.names if '咒刃' in plain(self.items[str(self.names[n])].get('description'))]
            elif target == '献祭':
                alternatives = [self.names[n] for n in ('日炎圣盾', '璀璨回响') if n in self.names]
            features.append('item_upgrade')
            # An item upgrade changes that item, not every item with a related
            # stat or passive. Other selected augments can supply broader rules.
            return {'name': name, 'status': 'mechanism' if required or alternatives else 'hero_conditional',
                    'features': features, 'required': sorted(set(required)),
                    'alternatives': sorted(set(alternatives)),
                    'reason': '按升级指定装备提供候选' if required or alternatives else
                              '当前数据缺少可购买的指定装备，暂不强推未知装备'}
        if '任务：' in text and refs:
            # Reward items are not tasks to buy. Only the requirement clause.
            requirements = text.split('任务：', 1)[1].split('奖励：', 1)[0]
            required = [self.names[n] for n in self.names if n in requirements]
            features.append('item_quest')
        if '攻击特效' in text:
            features.append('on_hit')
            if '技能可施加' in text: features.append('spell_on_hit')
            if '暴击施加' in text: features.append('crit_repeats_on_hit')
            if '消耗' in text and '法力' in text: features.append('mana_on_hit')
        patterns = {
            'spell_crit': r'技能可以造成暴击',
            'effect_crit': r'装备效果和持续伤害效果可以暴击',
            'heal_crit': r'治疗和护盾可以暴击',
            'crit_scaling': r'暴击.*(治疗|飞弹|攻击速度)|暴击几率.*(技能急速|会心防御)',
            'ap_scaling': r'相当于.*法术强度|每拥有.*法术强度|基于.*法术强度',
            'health_scaling': r'相当于.*最大生命值|基于.*最大生命值|受益于.*最大生命值',
            'mana_scaling': r'最大法力值|一半的法力值',
            'heal_shield': r'治疗和护盾强度|治疗效果和护盾效果|治疗.*友军|友军.*治疗|回复.*友军',
            'burn': r'灼烧|持续伤害效果',
            'item_haste': r'装备急速',
            'attack_speed_to_haste': r'攻击速度转化为技能急速',
            'no_ultimate': r'不能使用.*终极技能',
            'ultimate_only_haste': r'技能急速仅作用于终极技能',
            'mobility_scaling': r'基于你的移动速度|比目标多.*移动速度',
        }
        features += [key for key, pattern in patterns.items() if re.search(pattern, text)]
        if '额外攻击力转化为法术强度' in text: features.append('ad_to_ap')
        if '法术强度转化为额外攻击力' in text: features.append('ap_to_ad')
        uncertain = ('变为随机' in text or '随机' in text and '强化符文' in text or
                     '【?】技能' in text or '召唤物' in text or '旋转类技能' in text)
        if not features:
            status = 'hero_conditional' if uncertain else 'no_item_direction'
            reason = '依赖英雄技能或随机结果，保留英雄基础路线' if uncertain else '描述没有明确装备要求，保留英雄基础路线'
        elif 'item_upgrade' in features and not required and not alternatives:
            status = 'hero_conditional'
            reason = '当前数据缺少可购买的指定装备，暂不强推未知装备'
        else:
            status = 'mechanism'
            reason = '按当前描述识别机制；触发频率与收益仍取决于英雄和对局'
        return {'name': name, 'status': status, 'features': sorted(set(features)),
                'required': sorted(set(required)), 'alternatives': sorted(set(alternatives)), 'reason': reason}

    @lru_cache(maxsize=512)
    def item_features(self, key):
        item = self.items[str(key)]
        text = plain(item.get('description'))
        tags = set(item.get('categories', []))
        # Category OnHit alone includes Cleaver and support buffs: inspect the
        # passive to distinguish a real damage proc from unrelated metadata.
        proc = '攻击特效' in text and ('造成' in text or '附带' in text) and '友方英雄' not in text
        return {'tags': tags, 'text': text, 'proc': proc, 'spellblade': '咒刃' in text,
                'attack_only': bool(re.search(r'每第三次攻击|每第五次攻击|攻击对目标附近', text)),
                'heal_power': '治疗和护盾强度' in text,
                'damage_type': 'magic' if '魔法伤害' in text else 'physical' if '物理伤害' in text else None}

    def profile(self, route, build, ids, alias):
        values = [self.items.get(str(i), {}) for i in [*route, *ids]]
        tags = str((build.get('tags') or {}).get('primary_tags_f3pie', '')).upper()
        ap = sum('SpellDamage' in i.get('categories', []) for i in values)
        ad = sum('Damage' in i.get('categories', []) for i in values)
        lane = 'support' if 'HEAL' in tags else 'ap' if ap > ad else 'tank' if 'TANK' in tags and not ad else 'ad'
        resource = self.resources.get(alias)
        return lane, resource == 'Mana' if resource is not None else None

    def plan(self, owned, route, build, ids, known, alias):
        analyses = [self.analyze(n) for n in sorted(set(owned))]
        features = set(f for a in analyses for f in a['features'])
        required = set(i for a in analyses for i in a['required'])
        alternatives = set(i for a in analyses for i in a['alternatives'])
        lane, mana = self.profile(route, build, ids, alias)
        diagnostics = [a['name'] + '：' + a['reason'] for a in analyses if a['status'] != 'mechanism']
        contradictory = {'ad_to_ap', 'ap_to_ad'} <= features
        if contradictory:
            diagnostics.append('同时记录了相反属性转换，无法确定当前生效方向；不按转换重排')
        if 'ad_to_ap' in features and not contradictory: lane = 'ap'
        if 'no_ultimate' in features and 'ultimate_only_haste' in features:
            diagnostics.append('禁用终极技能与仅终极技能急速冲突，后者不作为出装收益')
        hydra_quest = any(a['name'] == '终极九头蛇' for a in analyses)
        def eligible(key):
            item = self.items[str(key)]
            if mana is False and 'Mana' in item.get('categories', []): return False
            if 'no_ultimate' in features and ('终极技能' in plain(item.get('description')) and key not in required): return False
            return True
        # Conversion changes the scaling stat, NOT the damage type: penetration
        # keeps the hero route's existing evidence instead of swapping blindly.
        scores, reasons = {}, {}
        route_set = set(route)
        def add(key, score, reason):
            if key in scores:
                scores[key] = max(scores[key], score)
                reasons[key].add(reason)
            else: scores[key], reasons[key] = score, {reason}
        for name, key in self.names.items():
            if not eligible(key): continue
            info = self.item_features(key); tags, text = info['tags'], info['text']
            match_stat = ('SpellDamage' in tags if lane in ('ap', 'support') else
                          'Health' in tags if lane == 'tank' else 'Damage' in tags)
            if key in required: add(key, 1000, '完成已选海克斯的升级或任务要求')
            if key in alternatives: add(key, 700 + 20 * (key in route_set), '已选强化指定的可选装备')
            if 'spell_on_hit' in features:
                if info['proc'] and not info['attack_only'] and (match_stat or lane == 'ad' and 'AttackSpeed' in tags):
                    # No assumption that every DoT tick or summoned attack procs.
                    affinity = 70 if lane == 'ap' and name == '纳什之牙' else 40 if lane == 'ap' and name == '巫妖之祸' else 0
                    penalty = 35 if lane == 'tank' and ('SpellDamage' in tags or 'Damage' in tags) else 25 if '盈能' in text else 0
                    add(key, 240 + 30 * match_stat + 25 * info['spellblade'] + 20 * (key in route_set) + affinity - penalty,
                        '技能施加攻击特效；保留装备自身触发条件')
                if lane == 'tank' and name == '心之钢': add(key, 315, '前排攻击特效候选；仍需满足距离和充能条件')
            elif 'on_hit' in features and 'AttackSpeed' in tags and match_stat:
                add(key, 200 + 20 * info['proc'] + 20 * (key in route_set), '普攻特效机制配合攻击频率')
            if ('crit_repeats_on_hit' in features or 'crit_scaling' in features or 'spell_crit' in features or 'effect_crit' in features) and 'CriticalStrike' in tags:
                if match_stat:
                    add(key, 230 + 40 * match_stat + 20 * (key in route_set), '已选强化的暴击机制候选')
            if 'spell_crit' in features and name == '无尽之刃': add(key, 250, '技能暴击的暴击伤害候选')
            if 'heal_crit' in features and 'CriticalStrike' in tags and 'SpellDamage' in tags: add(key, 250, '治疗护盾暴击的法强暴击候选')
            if 'mana_scaling' in features or 'mana_on_hit' in features:
                if mana and 'Mana' in tags and (match_stat or lane == 'tank' and 'Health' in tags):
                    add(key, 210 + 20 * (key in route_set), '已选强化依赖法力；按英雄资源与主属性筛选')
            if 'ap_scaling' in features and 'SpellDamage' in tags and lane in ('ap', 'support'):
                add(key, 180 + 30 * (name == '灭世者的死亡之帽') + 20 * (key in route_set), '已选强化增加法强衍生收益')
            if 'ad_to_ap' in features and not contradictory and 'SpellDamage' in tags:
                add(key, 220 + 20 * (key in route_set) + 25 * (name == '灭世者的死亡之帽'), '额外攻击力转法强；优先直接法强及适配被动')
            if 'ap_to_ad' in features and not contradictory and 'SpellDamage' in tags:
                # Avoid AP-dependent procs, which lose their AP after conversion.
                if name == '灭世者的死亡之帽': add(key, 220, '法强转额外攻击力的属性候选；伤害类型保持不变')
                elif 'Damage' in tags: add(key, 200, '混合属性转额外攻击力候选')
            if 'health_scaling' in features and 'Health' in tags and (match_stat or lane == 'tank'):
                add(key, 180 + 30 * (name in ('心之钢', '巨型九头蛇', '霸王血铠')) + 20 * (key in route_set), '最大生命值衍生收益')
            if 'heal_shield' in features and lane == 'support' and info['heal_power']:
                add(key, 200 + 20 * (key in route_set), '友军治疗护盾路线的治疗护盾强度')
            if 'burn' in features and '灼烧' in text and match_stat:
                add(key, 210 + 20 * (key in route_set), '灼烧或持续伤害联动')
            if 'item_haste' in features and ('Active' in tags or info['spellblade']) and match_stat:
                add(key, 200 + 20 * (key in route_set), '装备急速配合有触发冷却的装备')
            if 'attack_speed_to_haste' in features and lane == 'ap' and 'SpellDamage' in tags and 'AttackSpeed' in tags:
                add(key, 210, '攻击速度转技能急速的法强候选')
        candidates = []
        for key, score in scores.items():
            row = {'id': key, 'name': self.items[str(key)]['name'], 'mechanismScore': score,
                   'reason': '；'.join(sorted(reasons[key])), 'mechanismOnly': True,
                   'preferred': True, 'required': key in required, 'winRate': None, 'games': None,
                   'reliability': '机制规则 · 无联合样本', 'evidenceBasis': 'mechanism', 'joint': None}
            # A three-hydra task explicitly requires otherwise alternative items.
            if hydra_quest and key in required:
                decorated = self.policy.decorate([row], [], False)
                if known and (str(key) in self.policy.owned_routes(ids) or len(ids) >= 6 and self.policy.credit(key, ids) == 0): decorated = []
                if decorated:
                    decorated[0]['componentCredit'] = self.policy.credit(key, ids) if known else 0
                    decorated[0]['remainingCost'] -= decorated[0]['componentCredit']
            else:
                decorated = self.policy.decorate([row], ids, known)
            candidates += decorated
        for key in sorted(required):
            if key not in scores:
                diagnostics.append(self.items[str(key)]['name'] + '：装备要求与英雄资源限制不符，暂不强推')
            elif known and str(key) not in self.policy.owned_routes(ids) and not any(c['id'] == key for c in candidates):
                diagnostics.append(self.items[str(key)]['name'] + '：当前库存或装备冲突阻止购买')
        if mana is False and ({'mana_scaling', 'mana_on_hit'} & features):
            diagnostics.append('该英雄不使用法力，法力联动不用于出装排序')
        # Required items precede optional synergy; among equal mechanisms finish
        # owned components, then reuse the hero route and deterministic item ID.
        candidates.sort(key=lambda c: (-int(c['required']), -c['componentCredit'], -c['mechanismScore'],
                                       -int(c['id'] in route_set), c['id']))
        preferred = [c['id'] for c in candidates]
        constrained_route = [i for i in route if eligible(i)]
        if not contradictory and 'ad_to_ap' in features:
            constrained_route = [i for i in constrained_route if 'Damage' not in self.items[str(i)].get('categories', []) or 'SpellDamage' in self.items[str(i)].get('categories', [])]
        # Select stable anchors before excluding owned items: buying the first
        # synergy must not cause us to prescribe every other synergy in turn.
        anchors = sorted(scores, key=lambda key: (-int(key in required), -scores[key],
                                                  -int(key in route_set), key))
        anchor_count = max(len(required), 2 if 'spell_on_hit' in features else 1)
        anchors = [key for key in anchors if key in required or not known or str(key) in self.policy.owned_routes(ids)
                   or any(c['id'] == key for c in candidates)]
        # Keep a known existing spellblade; do not plan two unique spellblades.
        planned = []
        selected_spellblade = False
        for key in anchors:
            if key in planned: continue
            if self.item_features(key)['spellblade']:
                if selected_spellblade: continue
                selected_spellblade = True
            planned.append(key)
            if len(planned) >= anchor_count: break
        # Preserve boots and the original non-conflicting core behind anchors.
        for key in constrained_route:
            if key in planned: continue
            if self.item_features(key)['spellblade']:
                if selected_spellblade: continue
                selected_spellblade = True
            planned.append(key)
        active = bool(candidates) or constrained_route != route
        return {'basis': 'mechanism_rule' if active else 'aramgg', 'name': '已选海克斯联动' if active else '常规参考',
                'reason': '依据当前强化描述调整路线；排序为机制规则，未计算装备条件胜率' if active else '保留英雄来源核心路线',
                'triggers': [a['name'] for a in analyses if a['features']], 'features': sorted(features),
                'preferredItemIds': preferred, 'requiredItemIds': sorted(required),
                'diagnostics': diagnostics, 'analyses': analyses, 'candidates': candidates,
                'route': planned if active else constrained_route, 'resourceKnown': alias in self.resources,
                'profile': lane}
