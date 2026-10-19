import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

from hexgod.paths import PROJECT_ROOT as ROOT
from hexgod.api.backend import Backend, ranking_score


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)
        cls.hero = next(iter(cls.backend.heroes))

    def test_catalog_and_named_items(self):
        catalog = self.backend.catalog()
        self.assertEqual(len(catalog["heroes"]), len(self.backend.heroes))
        detail = self.backend.hero_detail(self.hero)
        self.assertEqual(len(detail["items"]), 6)
        self.assertTrue(all(i["name"] and not i["name"].startswith("装备 ") for i in detail["items"]))
        self.assertTrue(detail["coreItems"])
        self.assertTrue(any(h["aliases"] for h in catalog["heroes"]))

    def test_demo_preserves_screen_slot_order(self):
        expected = sorted(self.backend.runtime["hero_augment_stats"][self.hero],
                          key=lambda r: r["games"], reverse=True)[:3]
        result = self.backend.demo(self.hero)
        self.assertEqual([c["name"] for c in result["choices"]], [r["augment_name"] for r in expected])
        self.assertEqual(result["source"], "demo")
        self.assertTrue(result["complete"])
        self.assertEqual(sum(c["recommended"] for c in result["choices"]), 1)

    def test_empty_and_unknown_text_never_invent_choices(self):
        result = self.backend.rank_choices(self.hero, ["", "unknown nonsense", "x"])
        self.assertEqual(result["matchedCount"], 0)
        self.assertFalse(any(c["recommended"] for c in result["choices"]))

    def test_known_name_keeps_stats_even_when_ocr_confidence_is_low(self):
        name = self.backend.demo(self.hero)["choices"][0]["name"]
        result = self.backend.rank_choices(self.hero, [name], [0.2])
        self.assertTrue(result["choices"][0]["matched"])
        self.assertTrue(result["choices"][0]["hasStats"])

    def test_one_game_cannot_override_reliable_score(self):
        self.assertLess(ranking_score({"games": 1, "win_rate": 1, "hex_tier": "insufficient"}, 0.5),
                        ranking_score({"games": 10000, "win_rate": 0.55, "hex_score": 80}, 0.5))
        original = self.backend.runtime["hero_augment_stats"].get(self.hero)
        name = next(iter(self.backend.augments))
        try:
            self.backend.runtime["hero_augment_stats"][self.hero] = [
                {"augment_name": name, "games": 1, "win_rate": 1, "hex_score": 100}]
            result = self.backend.rank_choices(self.hero, [name])
            self.assertFalse(result["choices"][0]["recommended"])
        finally:
            self.backend.runtime["hero_augment_stats"][self.hero] = original

    def test_build_path_excludes_purchased_and_uses_hero_augment_rules(self):
        path = self.backend.build_path("赏金猎人", ["炼狱导管"], [2503, 3020], True)
        ids = [item["id"] for item in path["nextItems"]]
        self.assertTrue(path["itemsKnown"])
        self.assertNotIn(2503, ids)
        self.assertNotIn(3020, ids)
        self.assertEqual(ids[0], 6653)
        self.assertNotIn(3089, ids)  # A later core step is not an alternative now.
        self.assertIn(3089, [item['id'] for item in path['planPreview']])
        self.assertTrue(all(item["games"] >= 250 for item in path["nextItems"]))

    def test_unknown_hero_rejected(self):
        with self.assertRaises(ValueError):
            self.backend.hero_detail("does not exist")

    def test_protocol_recovers_after_invalid_request(self):
        requests = "bad json\n" + json.dumps({"id": 2, "action": "hero", "hero": self.hero}) + "\n"
        run = subprocess.run([sys.executable, "-B", "-m", "hexgod"], input=requests,
                             encoding="utf-8", capture_output=True, timeout=20)
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(run.returncode, 0)
        self.assertFalse(responses[0]["ok"])
        self.assertTrue(responses[1]["ok"])
        self.assertEqual(responses[1]["id"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
