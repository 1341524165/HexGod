import unittest

from hexgod.paths import CLIENT_DATA_DIR
from hexgod.recommendation.aramgg import AramggEngine


class SynergySourceTests(unittest.TestCase):
    def test_items_are_attributed_to_the_rules_that_nominate_them(self):
        engine = AramggEngine(folder=CLIENT_DATA_DIR / '16.21.5')
        result = engine.build_path('德邦总管', ['虚幻武器', '升级：无尽之刃'], [], True)
        by_name = {item['name']: item for item in result['synergyItems']}
        self.assertEqual(by_name['无尽之刃']['triggerAugments'], ['升级：无尽之刃'])
        self.assertIn('虚幻武器', by_name['三相之力']['triggerAugments'])
        self.assertNotIn('升级：无尽之刃', by_name['三相之力']['triggerAugments'])


if __name__ == '__main__':
    unittest.main()
