"""Exercise the same owned-augment and inventory inputs used by the overlay."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend


class LiveSynergyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)

    def test_virtual_weapon_is_extra_advice_without_reordering_main_items(self):
        normal = self.backend.build_path("复仇焰魂", [], [], True)
        selected = self.backend.build_path("复仇焰魂", ["虚幻武器"], [], True)
        normal_names = [item["name"] for item in normal["nextItems"]]
        selected_names = [item["name"] for item in selected["nextItems"]]
        self.assertEqual(normal_names, selected_names)
        self.assertEqual([item["name"] for item in selected["synergyItems"][:2]],
                         ["纳什之牙", "巫妖之祸"])
        self.assertEqual(selected["owned"], ["虚幻武器"])

    def test_burning_interest_alias_activates_hero_rule(self):
        normal = self.backend.build_path("赏金猎人", [], [], True)
        selected = self.backend.build_path("赏金猎人", ["灼热利息"], [], True)
        self.assertEqual(selected["owned"], ["炽燃利息"])
        self.assertEqual([item["id"] for item in normal["nextItems"]],
                         [item["id"] for item in selected["nextItems"]])
        self.assertEqual(selected["synergyTriggers"], ["炽燃利息"])
        self.assertIn(3135, [item["id"] for item in selected["synergyItems"]])
        self.assertFalse({item["id"] for item in selected["nextItems"]} &
                         {item["id"] for item in selected["synergyItems"]})

    def test_purchased_item_is_removed_after_augment_is_selected(self):
        path = self.backend.build_path("复仇焰魂", ["虚幻武器"], [3115], True)
        self.assertNotIn(3115, [item["id"] for item in path["nextItems"]])
        self.assertNotIn(3115, [item["id"] for item in path["synergyItems"]])
        self.assertIn("巫妖之祸", [item["name"] for item in path["synergyItems"]])
        self.assertTrue(path["itemsKnown"])

    def test_multiple_owned_augments_are_both_used(self):
        path = self.backend.build_path("赏金猎人", ["灼热利息", "虚幻武器"],
                                       [2503, 3020], True)
        self.assertEqual(set(path["owned"]), {"炽燃利息", "虚幻武器"})
        self.assertNotIn(2503, [item["id"] for item in path["nextItems"]])
        profile = self.backend.hero_detail("赏金猎人", path["owned"])["buildProfile"]
        self.assertEqual(set(profile["triggers"]), set(path["owned"]))

    def test_protocol_passes_owned_augments_and_items_to_recommender(self):
        requests = [
            {"id": 1, "action": "build_path", "hero": "复仇焰魂", "owned": [],
             "itemIds": [], "itemsKnown": True},
            {"id": 2, "action": "build_path", "hero": "复仇焰魂",
             "owned": ["虚幻武器"], "itemIds": [3115], "itemsKnown": True},
        ]
        run = subprocess.run([sys.executable, "-B", str(ROOT / "backend.py"), "--legacy-fixture"],
                             input="\n".join(json.dumps(row, ensure_ascii=False) for row in requests) + "\n",
                             encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        results = [json.loads(line)["result"] for line in run.stdout.splitlines()]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["nextItems"][0]["id"], results[1]["nextItems"][0]["id"])
        self.assertEqual(results[1]["synergyItems"][0]["name"], "巫妖之祸")
        self.assertEqual(results[1]["owned"], ["虚幻武器"])

    def test_warmog_cannot_be_the_first_completed_item(self):
        first = self.backend.build_path("熔岩巨兽", [], [], True)
        after_boots = self.backend.build_path("熔岩巨兽", [], [3158, 1001], True)
        after_no_health = self.backend.build_path("熔岩巨兽", [], [3089], True)
        after_insufficient_health = self.backend.build_path("熔岩巨兽", [], [3050, 3068], True)
        after_other_legendary = self.backend.build_path("熔岩巨兽", [], [3075], True)
        later = self.backend.build_path("熔岩巨兽", [], [3084], True)
        self.assertTrue(first["firstComplete"])
        self.assertNotIn(3083, [item["id"] for item in first["nextItems"]])
        self.assertTrue(after_boots["firstComplete"])
        self.assertNotIn(3083, [item["id"] for item in after_boots["nextItems"]])
        for path in (after_no_health, after_insufficient_health, after_other_legendary):
            self.assertFalse(path["firstComplete"])
            self.assertFalse(path["warmogPassiveReady"])
            self.assertNotIn(3083, [item["id"] for item in path["nextItems"]])
        self.assertFalse(later["firstComplete"])
        self.assertTrue(later["warmogPassiveReady"])
        self.assertIn(3083, later['eligibleItemIds'])
        for hero in self.backend.heroes:
            path = self.backend.build_path(hero, [], [], True)
            self.assertNotIn(3083, [item["id"] for item in path["nextItems"]], hero)

    def test_upgrade_forms_and_other_map_ids_are_not_sold_as_next_items(self):
        blocked = {2530, 3040, 3042, 3121, 3430, 4010, 4011, 6697}
        cases = [
            ("符文法师", ["由心及物"]),
            ("明烛", ["风语者的祝福"]),
            ("星界游神", ["升级：花晓之剑"]),
            ("铸星龙王", ["炽燃利息"]),
            ("暗裔剑魔", []),
        ]
        for hero, owned in cases:
            path = self.backend.build_path(hero, owned, [], True)
            shown = {item["id"] for item in path["nextItems"] + path["synergyItems"]}
            self.assertFalse(shown & blocked, (hero, shown & blocked))
        self.assertIn(8010, self.backend.build_path("铸星龙王", ["炽燃利息"], [3116], True)['eligibleItemIds'])
        self.assertIn(126697, [item["id"] for item in
                               self.backend.build_path("暗裔剑魔", [], [], True)["nextItems"]])
        self.assertIn(124011, [item["id"] for item in
                               self.backend.build_path("星界游神", ["升级：花晓之剑"], [], True)["nextItems"]])
        self.assertEqual([item["id"] for item in self.backend.hero_detail("符文法师")["coreItems"]],
                         [6657, 3003, 3089, 3119])

    def test_typhoon_on_warwick_does_not_force_on_hit_items_to_first(self):
        normal = self.backend.build_path("祖安怒兽", [], [], True)
        selected = self.backend.build_path("祖安怒兽", ["台风"], [], True)
        self.assertEqual([item["id"] for item in normal["nextItems"]],
                         [item["id"] for item in selected["nextItems"]])
        tips = [item["id"] for item in selected["synergyItems"]]
        self.assertIn(3302, tips)  # Terminus is an augment mechanism candidate.
        self.assertNotIn(3087, tips)  # Statikk Shiv is only in the base hero formula.
        self.assertTrue(all(item["games"] > 0 and isinstance(item["sampleLimited"], bool)
                            for item in selected["nextItems"] + selected["synergyItems"]))

    def test_no_joint_file_means_rates_are_not_claimed_as_conditional(self):
        self.assertFalse((ROOT / "joint_stats.json").exists())
        path = self.backend.build_path("复仇焰魂", ["虚幻武器"], [], True)
        self.assertEqual(path["dataBasis"], "single_item")
        self.assertTrue(all(item["basis"] != "augment_item" for item in path["nextItems"]))

    def test_no_direct_item_shift_augment_does_not_fake_a_build_change(self):
        name = sorted(self.backend.no_direct_augments)[0]
        normal = self.backend.build_path("复仇焰魂", [], [], True)
        selected = self.backend.build_path("复仇焰魂", [name], [], True)
        self.assertEqual([item["id"] for item in selected["nextItems"]],
                         [item["id"] for item in normal["nextItems"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
