"""Summarize saved evidence without treating fixtures or replays as live proof."""
import argparse
from collections import Counter
import json
from pathlib import Path
from hexgod.paths import LOG_DIR, REPORT_DIR, RESOURCE_DIR


def audit(log_root):
    controlled = json.loads((RESOURCE_DIR/'diagnostics/controlled-sessions.json').read_text('utf-8'))
    sessions, errors = [], []
    for path in sorted(Path(log_root).glob('*/summary.json')):
        try:
            row = json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            errors.append({'file':str(path),'error':str(error)})
            continue
        session = path.parent.name
        kind = controlled.get(session, {}).get('kind') or row.get('evidenceKind')
        kind = kind or ('offline' if session == 'offline' else 'historical_untagged')
        sessions.append({'session':session,'kind':kind,'hero':row.get('hero'),
                         'panels':row.get('panelFrames',0),'clicks':row.get('clicks',0),
                         'confirmed':row.get('confirmed',[]),
                         'observedSpellOnHit':'虚幻武器' in row.get('observedTitles',[])})
    candidates = [s for s in sessions if s['kind'] in ('live','historical_untagged')]
    return {'summaryFiles':len(sessions),'kinds':dict(Counter(s['kind'] for s in sessions)),
            'candidateLiveSessions':len(candidates),
            'verifiedLiveConfirmed':[s for s in sessions if s['kind']=='live' and s['confirmed']],
            'untaggedConfirmed':[s for s in sessions if s['kind']=='historical_untagged' and s['confirmed']],
            'controlledConfirmed':[s for s in sessions if s['kind'] not in ('live','historical_untagged') and s['confirmed']],
            'spellOnHitZeroClicks':[s['session'] for s in candidates if s['observedSpellOnHit'] and not s['clicks']],
            'spellOnHitZeroClickCount':sum(s['observedSpellOnHit'] and not s['clicks'] for s in candidates),
            'errors':errors,'sessions':sessions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-root', type=Path, default=LOG_DIR/'matches')
    parser.add_argument('--output', type=Path, default=REPORT_DIR/'recording-audit.json')
    args = parser.parse_args()
    result = audit(args.log_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='sessions'},ensure_ascii=False))


if __name__ == '__main__':
    main()
