import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Backend
from live_game import parse_live_game_data
from starter_builds import OpeningTracker, StarterBuilds


class StarterBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)

    def setUp(self):
        self.backend.opening_tracker = OpeningTracker()

    def opening(self, hero="镕铁少女", items=None, game_time=15, gold=1400,
                start=100000, deaths=0, counts=None, known=True):
        items = items or []
        live = {"available": True, "hero": hero, "matchStartMs": start,
                "gameTime": game_time, "currentGold": gold, "deaths": deaths,
                "itemCounts": counts or {str(item): items.count(item) for item in items}}
        return self.backend.build_path(hero, [], items, known, live)

    def test_every_hero_has_a_buyable_budgeted_bundle_and_no_invented_statistics(self):
        self.assertEqual(len(self.backend.starters.plans), 173)
        for hero in self.backend.heroes:
            with self.subTest(hero=hero):
                plan = self.backend.starters.plan(hero)
                self.assertLessEqual(plan["totalCost"], 1400)
                self.assertEqual(plan["totalCost"] + plan["remainingGold"], 1400)
                self.assertTrue(plan["reason"])
                self.assertEqual(plan["basis"], "provisional" if hero == "Locke" else "curated")
                self.assertIsNone(plan["games"])
                self.assertNotIn("winRate", plan)
                self.assertTrue(all(self.backend.shop_available(row["id"]) for row in plan["items"]))
                self.assertFalse({2003, 2031} <= {row["id"] for row in plan["items"]})

    def test_rell_and_vayne_exact_sets_and_costs(self):
        for hero, expected in [("镕铁少女", [(2051, 1), (3070, 1), (2003, 1)]),
                               ("暗夜猎手", [(3177, 1), (1036, 1), (2003, 2)])]:
            plan = self.backend.hero_detail(hero)["starterPlan"]
            self.assertEqual([(i["id"], i["quantity"]) for i in plan["items"]], expected)
            self.assertEqual(plan["totalCost"], 1400)

    def test_frontline_starters_do_not_rush_heartsteel_components(self):
        for hero in ("腕豪", "暮光之眼", "生化魔人", "祖安狂人"):
            ids = [i["id"] for i in self.backend.starters.plan(hero)["items"]]
            self.assertEqual(ids[:2], [2051, 1028])
        for hero in ("河流之王", "北地之怒", "弗雷尔卓德之心", "山隐之焰", "深海泰坦",
                     "圣锤之毅", "曙光女神", "虚空恐惧", "亡灵战神"):
            ids = [i["id"] for i in self.backend.starters.plan(hero)["items"]]
            self.assertEqual(ids[:2], [2051, 3070])

    def test_starter_is_independent_of_first_legendary_and_augment_ranking(self):
        hero = "暗夜猎手"
        original = self.backend.formulas["byHeroName"][hero]["coreItems"]
        expected = self.backend.hero_detail(hero)["starterPlan"]
        try:
            self.backend.formulas["byHeroName"][hero]["coreItems"] = [{"id": "3083", "name": "狂徒铠甲"}]
            self.assertEqual(self.backend.hero_detail(hero, ["虚幻武器"])["starterPlan"], expected)
        finally:
            self.backend.formulas["byHeroName"][hero]["coreItems"] = original

    def test_partial_purchases_and_buying_tear_first(self):
        result = self.opening(items=[3070], gold=1000)
        self.assertEqual(result["recommendationStage"], "starter")
        plan = result["starterPlan"]
        self.assertEqual(plan["remainingCost"], 1000)
        self.assertEqual(plan["items"][1]["ownedQuantity"], 1)
        self.assertEqual(plan["items"][1]["remainingCost"], 0)

    def test_starter_recipe_parts_get_credit_without_double_counting(self):
        result = self.opening(hero="复仇焰魂", items=[1052, 1027], gold=700)
        self.assertEqual(result["recommendationStage"], "starter")
        self.assertEqual(result["starterPlan"]["remainingCost"], 650)
        self.assertEqual(result["starterPlan"]["items"][0]["remainingCost"], 500)

    def test_stacked_potion_count_updates_and_consuming_does_not_reopen(self):
        partial = self.opening(hero="暗夜猎手", items=[3177, 1036, 2003], gold=50,
                               counts={"3177": 1, "1036": 1, "2003": 1})
        self.assertEqual(partial["recommendationStage"], "starter")
        self.assertEqual(partial["starterPlan"]["remainingCost"], 50)
        complete = self.opening(hero="暗夜猎手", items=[3177, 1036, 2003], gold=0,
                                counts={"3177": 1, "1036": 1, "2003": 2})
        self.assertEqual(complete["openingReason"], "purchased")
        consumed = self.opening(hero="暗夜猎手", items=[3177, 1036], gold=100, game_time=50)
        self.assertEqual(consumed["recommendationStage"], "items")

    def test_different_opening_does_not_recommend_a_second_full_set(self):
        result = self.opening(items=[1011, 1028], gold=100)
        self.assertEqual(result["openingReason"], "different_purchase")
        self.assertEqual(result["recommendationStage"], "items")
        self.assertTrue(result["nextItems"])

    def test_loading_without_starting_gold_does_not_close_opening(self):
        self.assertEqual(self.opening(gold=0)["recommendationStage"], "items")
        self.assertEqual(self.opening(gold=1400)["recommendationStage"], "starter")

    def test_clock_and_death_exit_without_inventory_change(self):
        self.assertEqual(self.opening()["recommendationStage"], "starter")
        self.assertEqual(self.opening(game_time=90)["openingReason"], "opening_elapsed")
        self.backend.opening_tracker = OpeningTracker()
        self.assertEqual(self.opening(deaths=1)["openingReason"], "respawn")

    def test_api_gap_and_new_match_same_hero(self):
        self.assertEqual(self.opening(game_time=500)["recommendationStage"], "items")
        gap = self.backend.build_path("镕铁少女", [], [], False)
        self.assertEqual(gap["recommendationStage"], "items")
        self.assertEqual(self.opening(game_time=510)["recommendationStage"], "items")
        self.assertEqual(self.opening(start=900000)["recommendationStage"], "starter")

    def test_unknown_inventory_does_not_activate_opening_or_close_latch(self):
        self.assertEqual(self.opening(known=False)["recommendationStage"], "items")
        self.assertEqual(self.opening(items=[999999])["recommendationStage"], "items")
        self.assertEqual(self.opening()["recommendationStage"], "starter")

    def test_invalid_catalog_is_rejected_instead_of_silent_fallback(self):
        payload = json.loads((ROOT / "starter_builds.json").read_text(encoding="utf-8-sig"))
        for mutation in ("over_budget", "missing_hero", "duplicate_hero", "unbuyable", "patch"):
            bad = copy.deepcopy(payload)
            if mutation == "over_budget":
                bad["plans"][0]["items"][0]["quantity"] = 5
            elif mutation == "missing_hero":
                bad["plans"][0]["heroes"].pop()
            elif mutation == "duplicate_hero":
                bad["plans"][0]["heroes"].append(bad["plans"][0]["heroes"][0])
            elif mutation == "unbuyable":
                bad["plans"][0]["items"][0]["id"] = 3040
            else:
                bad["patch"] = "0.0"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                StarterBuilds(bad, self.backend.item_catalog, self.backend.heroes, "16.15")

    def test_live_api_to_protocol_includes_gold_deaths_and_stack_counts(self):
        data = {"gameData": {"gameTime": 12}, "activePlayer": {
            "riotId": "Player#CN1", "currentGold": 50}, "allPlayers": [{
                "riotId": "Player#CN1", "championName": "Vayne", "scores": {"deaths": 0},
                "items": [{"itemID": 3177, "count": 1, "slot": 0},
                          {"itemID": 1036, "count": 1, "slot": 1},
                          {"itemID": 2003, "count": 1, "slot": 2}]}]}
        requests = []
        for index in range(2):
            live = parse_live_game_data(data, self.backend.hero_lookup, now=1000)
            requests.append({"id": index + 1, "action": "build_path", "hero": live["hero"],
                             "itemIds": live["itemIds"], "itemsKnown": live["itemsKnown"],
                             "liveContext": live})
            data["activePlayer"]["currentGold"] = 0
            data["allPlayers"][0]["items"][2]["count"] = 2
        run = subprocess.run([sys.executable, "-B", str(ROOT / "backend.py"), "--legacy-fixture"],
                             input="\n".join(json.dumps(row) for row in requests) + "\n",
                             encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        results = [json.loads(line)["result"] for line in run.stdout.splitlines()]
        self.assertEqual(results[0]["recommendationStage"], "starter")
        self.assertEqual(results[0]["starterPlan"]["remainingCost"], 50)
        self.assertEqual(results[1]["recommendationStage"], "items")
        self.assertEqual(results[1]["openingReason"], "purchased")


if __name__ == "__main__":
    unittest.main(verbosity=2)
