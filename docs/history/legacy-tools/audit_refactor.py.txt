"""Reproduce all-hero progression and optional before/after screenshot checks."""
import json
from pathlib import Path
import sys
import time

from backend import Backend

ROOT=Path(__file__).resolve().parent


def run():
    backend=Backend(use_remote=False)
    baseline=None
    old=ROOT/'.cache/pre-refactor-0.9/backend.py'
    if old.exists():
        scope={'__file__':str(ROOT/'backend.py'),'__name__':'baseline_09'}
        exec(compile(old.read_text(encoding='utf-8'),str(old),'exec'),scope)
        baseline=scope['Backend']()
    report={'patch':backend.runtime['meta']['version'],'reportDate':backend.runtime['meta']['report_date'],
            'referenceCommit':'e07cef86e7f88a16aa6046eabfc4cc7c4ab8da5a','heroes':[],'screenshots':[]}
    for hero in sorted(backend.heroes):
        inventory=[]; steps=[]
        first=backend.build_path(hero,[],[],True)
        before=baseline.build_path(hero,[],[],True) if baseline else None
        for _ in range(6):
            result=backend.build_path(hero,[],inventory,True)
            if not result['nextItems']: break
            chosen=result['nextItems'][0]
            assert str(chosen['id']) not in backend.purchase_policy.owned_routes(inventory)
            assert str(chosen['id']) in backend.direct_buyable_item_ids
            steps.append({'item':chosen['name'],'id':chosen['id'],'basis':result['purchaseOrder']['basis']})
            inventory.append(chosen['id'])
        report['heroes'].append({'hero':hero,
            'beforeFirst':before['nextItems'][0]['name'] if before and before['nextItems'] else None,
            'afterFirst':first['nextItems'][0]['name'] if first['nextItems'] else None,
            'basis':first['purchaseOrder']['basis'],'steps':steps})
    fixtures=ROOT/'tests/fixtures/augment-ocr'
    if fixtures.exists():
        for entry in json.loads((fixtures/'manifest.json').read_text(encoding='utf-8')):
            row={'file':entry['file'],'expected':entry['expectedNames']}
            for name,engine in [('before',baseline),('after',backend)]:
                if engine is None: continue
                started=time.perf_counter()
                result=engine.recognize({'hero':'赏金猎人','image':str(fixtures/entry['file'])})
                row[name]={'slots':[c['name'] for c in result['choices']],
                           'seconds':round(time.perf_counter()-started,3),'matched':result['matchedCount']}
            report['screenshots'].append(row)
    output=ROOT/'refactor-audit.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 0.10 全英雄购买步骤与 OCR 对照审计','',
           f"本地统计：{report['patch']} / {report['reportDate']}。共 {len(report['heroes'])} 位英雄。",'',
           '下表是算法逐步选择的回放，不是实战胜率验证；核心表顺序是启发式方案，补充候选仍受单件样本偏差影响。没有购买位置、对手阵容、海克斯组合的全量路径数据。', '',
           '假设：库存已同步、未选海克斯，每次买入本步第一项；不模拟出售与换装。升级形态、同类替代、配方、地图资格另有回归测试。', '',
           '| 英雄 | 0.9 首项 | 0.10 首项 | 首项依据 | 六步回放 |','|---|---|---|---|---|']
    for row in report['heroes']:
        lines.append(f"| {row['hero']} | {row['beforeFirst'] or '—'} | {row['afterFirst'] or '—'} | {row['basis']} | {' → '.join(s['item'] for s in row['steps'])} |")
    lines+=['','## 参考截图','',
            '截图位于只读参考仓库，不打包进应用；耗时为本机单次离线管道计时，包含冷启动差异，不代表实战帧率。', '',
            '| 文件 | 0.9 左 / 中 / 右 | 0.10 左 / 中 / 右 |','|---|---|---|']
    for row in report['screenshots']:
        lines.append('| '+row['file']+' | '+' / '.join(row.get('before',{}).get('slots',[]))+' | '+' / '.join(row['after']['slots'])+' |')
    (ROOT/'refactor-audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    changed=sum(row['beforeFirst']!=row['afterFirst'] for row in report['heroes'])
    print(json.dumps({'heroes':len(report['heroes']),'firstChoiceChanged':changed,'screenshots':report['screenshots']},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    run()
