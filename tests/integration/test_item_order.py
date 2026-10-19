import json
import sys
from pathlib import Path
import unittest

from hexgod.paths import PROJECT_ROOT as ROOT
from hexgod.api.backend import Backend
from hexgod.recommendation.item_order import ItemOrder


class ItemOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)

    def path(self, items=(), owned=()):
        return self.backend.build_path("战争之影", list(owned), list(items), True)

    def test_hecarim_order_with_boots_tear_and_upgraded_manamune(self):
        for items in ([], [3070, 3158], [3177, 1036]):
            path = self.path(items)
            self.assertEqual(path["nextItems"][0]["id"], 3161)
            self.assertNotIn(6696, [i["id"] for i in path["nextItems"]])
            self.assertNotIn(3004, [i["id"] for i in path["nextItems"]])
        after_spear = self.path([3161])
        self.assertEqual(after_spear["nextItems"][0]["id"], 3004)
        self.assertNotIn(6696, [i["id"] for i in after_spear["nextItems"]])
        for items in ([3161, 3004], [3161, 3042]):
            path = self.path(items)
            self.assertIsNone(path["purchaseOrder"]["nextId"])
            self.assertIn(6696, [i["id"] for i in path["nextItems"]])
            self.assertNotIn(3004, [i["id"] for i in path["nextItems"]])

    def test_route_is_not_claimed_as_a_joint_or_ordered_win_rate(self):
        path = self.path([3161])
        self.assertEqual(path["purchaseOrder"]["basis"], "curated")
        self.assertIsNone(path["purchaseOrder"]["games"])
        item = path["nextItems"][0]
        # Never relabel evolved Muramana observations as Manamune's rate.
        row = next(r for r in self.backend.runtime["hero_item_builds"]["战争之影"] if r["item_id"] == 3004)
        self.assertEqual(item["winRate"], row["win_rate"])
        self.assertEqual(item["games"], row["games"])
        self.assertEqual(item["basis"], "single_item")
        self.assertEqual([i["id"] for i in self.backend.hero_detail("战争之影")["coreItems"]], [3161, 3004])

    def test_synergy_stays_extra_and_does_not_replace_route(self):
        self.assertEqual([i["id"] for i in self.path()["nextItems"]],
                         [i["id"] for i in self.path(owned=["台风"])["nextItems"]])

    def test_rare_high_rate_does_not_define_first_item(self):
        order = self.backend.item_order
        rows = [{"id": 6696, "games": 500, "popularityGames": 500},
                {"id": 3161, "games": 20000, "popularityGames": 20000}]
        first, basis = order.apply("其他英雄", rows, set(), True, True)
        self.assertEqual([i["id"] for i in first], [3161])
        self.assertEqual(basis["basis"], "common_first_fallback")
        for first_complete, known in ((False, True), (True, False)):
            later, _ = order.apply("其他英雄", rows, set(), first_complete, known)
            self.assertEqual([i["id"] for i in later], [6696, 3161])

    def test_all_heroes_have_usable_first_candidates_and_valid_sample_labels(self):
        for hero in self.backend.heroes:
            path = self.backend.build_path(hero, [], [], True)
            self.assertTrue(path["nextItems"], hero)
            self.assertIsNone(path["purchaseOrder"]["games"])
            for item in path["nextItems"]:
                self.assertIn(str(item["id"]), self.backend.direct_buyable_item_ids)
                self.assertIsInstance(item["games"], int)
                self.assertNotIn(item["id"], (3083, 3042, 3040))

    def test_invalid_route_fails_validation(self):
        data = json.loads((ROOT / "resources/rules/item_order.json").read_text(encoding="utf-8"))
        data["heroOpenings"]["战争之影"]["items"] = [3042]
        with self.assertRaises(ValueError):
            ItemOrder(data, "16.15", self.backend.heroes, self.backend.direct_buyable_item_ids)
