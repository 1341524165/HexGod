import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from hexgod.paths import PROJECT_ROOT


class RecordingEvidenceTests(unittest.TestCase):
    def test_controlled_and_untagged_successes_never_count_as_verified_live(self):
        spec = importlib.util.spec_from_file_location('audit_recordings',PROJECT_ROOT/'scripts/audit_recordings.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            for session,kind in [('20260929-011443-1',None),('old-unknown',None),('replay-case','replay'),('new-live','live')]:
                folder=Path(directory)/session;folder.mkdir()
                row={'confirmed':['虚幻武器'],'clicks':1}
                if kind: row['evidenceKind']=kind
                (folder/'summary.json').write_text(json.dumps(row),'utf-8')
            result=module.audit(Path(directory))
        self.assertEqual([s['session'] for s in result['verifiedLiveConfirmed']],['new-live'])
        self.assertEqual([s['session'] for s in result['untaggedConfirmed']],['old-unknown'])
        self.assertEqual({s['session'] for s in result['controlledConfirmed']},{'20260929-011443-1','replay-case'})
