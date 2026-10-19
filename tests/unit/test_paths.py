import subprocess
import sys
import tempfile
import unittest

from hexgod.paths import PROJECT_ROOT, RESOURCE_DIR, MODEL_DIR


class ProjectPathTests(unittest.TestCase):
    def test_backend_module_runs_from_an_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            request = '{"id":1,"action":"build_path","hero":"德邦总管","owned":["虚幻武器"]}\n'
            result = subprocess.run([sys.executable,'-X','utf8','-B','-m','hexgod'],cwd=directory,
                input=request,capture_output=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        import json
        response = json.loads(result.stdout)
        self.assertTrue(response['ok'])
        self.assertTrue(response['result']['synergyItems'])

    def test_project_assets_are_explicitly_separated_from_python_sources(self):
        self.assertTrue((PROJECT_ROOT/'pyproject.toml').is_file())
        self.assertTrue((RESOURCE_DIR/'rules/item_order.json').is_file())
        self.assertTrue((MODEL_DIR/'paddleocr/rec/inference.onnx').is_file())
        self.assertFalse(any(PROJECT_ROOT.glob('*.py')))

    def test_production_backend_does_not_load_the_legacy_data_provider(self):
        result = subprocess.run([sys.executable,'-B','-c',
            "from hexgod.api.backend import Backend; import sys; Backend(); assert 'hexgod.recommendation.legacy' not in sys.modules"],
            capture_output=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
