"""Enumerate the active release; validate display and inventory invariants."""
from collections import Counter
import json
from pathlib import Path
import time
from hexgod.recommendation.aramgg import AramggEngine


def audit():
    engine = AramggEngine()
    analyses = [engine.mechanics.analyze(n) for n in sorted(engine.augments)]
    rows = {a['name']: {**a, 'heroesWithMechanismPlan': 0, 'example': None} for a in analyses}
    failures, checks = [], 0
    started = time.perf_counter()
    for hero in sorted(engine.heroes):
        baseline = engine.build_path(hero, [], [], True)
        first = baseline['purchaseOrder']['originalRoute'][:1]
        for name in rows:
            for inventory in ([], first):
                result = engine.build_path(hero, [name], inventory, True)
                checks += 1
                displayed = result['regularItems'] + result['synergyItems'][:3]
                if len(result['regularItems']) > 3: failures.append([hero,name,'regular_column_overflow'])
                for item in displayed:
                    if not engine.usable(item['id']): failures.append([hero,name,'unavailable_item',item['id']])
                    if str(item['id']) in engine.policy.owned_routes(inventory): failures.append([hero,name,'owned_item',item['id']])
                    if item.get('mechanismOnly') and (item.get('winRate') is not None or item.get('games') is not None):
                        failures.append([hero,name,'invented_joint_rate',item['id']])
                if not inventory and result['buildProfile']['basis'] == 'mechanism_rule':
                    rows[name]['heroesWithMechanismPlan'] += 1
                    if rows[name]['example'] is None:
                        rows[name]['example'] = {'hero':hero,'regular':[i['name'] for i in result['regularItems']],
                                                 'synergy':[i['name'] for i in result['synergyItems'][:3]]}
        if checks % 8440 == 0: print(f'{checks} hero/augment/inventory cases checked', flush=True)
    # Pair order must not change visible choices. Include contradictions,
    # overlapping quests, AP crit, self-provided procs and resource conversion.
    pairs = [('虚幻武器','双发快射'),('虚幻武器','火上浇油'),('虚幻武器','注魔'),
             ('虚幻武器','纯粹主义者 - 术师'),('魔法转物理','物理转魔法'),
             ('回归基本功','大招工具人'),('珠光护手','升级：无尽之刃'),
             ('钢化你心','升级：献祭'),('艾卡西亚的陷落','终极九头蛇')]
    for hero in engine.heroes:
        for pair in pairs:
            a = engine.build_path(hero, list(pair), [], True)
            b = engine.build_path(hero, list(reversed(pair)), [], True)
            checks += 1
            if a['regularItems'] != b['regularItems'] or a['synergyItems'] != b['synergyItems']:
                failures.append([hero,list(pair),'selection_order_changed_result'])
    report = {'dataVersion':engine.meta['dataVersion'], 'patch':engine.meta['version'],
              'heroes':len(engine.heroes), 'augments':len(rows), 'cases':checks,
              'elapsedSeconds':round(time.perf_counter()-started,2),
              'classification':dict(Counter(r['status'] for r in rows.values())),
              'onHitAugments':[n for n,r in rows.items() if 'on_hit' in r['features']],
              'failures':failures,'rows':list(rows.values())}
    from hexgod.paths import REPORT_DIR
    root = REPORT_DIR
    root.mkdir(parents=True, exist_ok=True)
    (root/'mechanics-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines = ['# 当前数据的海克斯装备联动审计', '',
             f"数据 {report['dataVersion']}；{report['heroes']} 位英雄 × {report['augments']} 个海克斯；共 {checks} 个库存/组合场景；失败 {len(failures)}。", '',
             '分类只说明是否能从当前描述得到装备方向，不表示每个英雄技能与触发行为都经过实战验证。机制分数不是胜率。', '',
             '| 海克斯 | 分类 | 机制 | 产生机制路线的英雄数 |', '|---|---|---|---|']
    labels={'mechanism':'有装备机制','hero_conditional':'英雄/数据条件','no_item_direction':'无明确装备转向'}
    for name,row in rows.items():
        lines.append(f"| {name} | {labels[row['status']]} | {', '.join(row['features']) or row['reason']} | {row['heroesWithMechanismPlan']} |")
    (root/'mechanics-audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False),flush=True)
    if failures: raise AssertionError(f'{len(failures)} audit failures')


if __name__ == '__main__': audit()
