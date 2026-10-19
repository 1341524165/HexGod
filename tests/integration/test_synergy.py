from pathlib import Path
import json
import sys
import unittest

from hexgod.paths import PROJECT_ROOT as ROOT
from hexgod.api.backend import Backend
from hexgod.recommendation.synergy import JointStats, observed_rate, trio_clue


class SynergyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)

    def test_local_pack_has_no_exact_joint_win_rate(self):
        result = self.backend.rank_choices("赏金猎人", ["老练狙神", "暴击飞弹", "双生火焰"],
                                           owned=["炼狱导管"])
        self.assertEqual(result["conditionalStatus"], "unavailable")
        self.assertTrue(all(c["conditional"] is None for c in result["choices"]))
        self.assertFalse(any(c["recommended"] for c in result["choices"]))
        self.assertGreater(result["choices"][0]["trioClue"]["publishedTrioRows"], 0)

    def test_name_variant_and_mechanism_profile(self):
        self.assertEqual(self.backend.canonical_augment("灼热利息"), "炽燃利息")
        result = self.backend.rank_choices("赏金猎人", ["灼热利息"], owned=["炼狱导管"])
        self.assertEqual(result["choices"][0]["name"], "炽燃利息")
        detail = self.backend.hero_detail("赏金猎人", ["灼热利息"])
        self.assertIn("持续法伤", detail["buildProfile"]["name"])
        self.assertIn("持续伤害经济", detail["buildProfile"]["name"])
        self.assertEqual(detail["buildProfile"]["basis"], "mechanism_rule")
        self.assertEqual(detail["items"][0]["name"],
                         self.backend.hero_detail("赏金猎人")["items"][0]["name"])

    def test_virtual_weapon_changes_mage_item_candidates(self):
        detail = self.backend.hero_detail("复仇焰魂", ["虚幻武器"])
        self.assertEqual(detail["buildProfile"]["name"], "攻击特效联动")
        self.assertEqual(detail["mechanismItems"][:2], ["纳什之牙", "巫妖之祸"])
        self.assertEqual([x["id"] for x in detail["items"][:2]],
                         [x["id"] for x in self.backend.hero_detail("复仇焰魂")["items"][:2]])
        self.assertTrue(all(x["joint"] is None for x in detail["items"]))

    def test_multiple_augment_rules_merge_without_losing_one(self):
        detail = self.backend.hero_detail("赏金猎人", ["炼狱导管", "虚幻武器"])
        self.assertIn("持续法伤", detail["buildProfile"]["name"])
        self.assertIn("攻击特效联动", detail["buildProfile"]["name"])
        self.assertIn("黯炎火炬", detail["mechanismItems"])

    def test_every_local_augment_is_explicitly_audited(self):
        audit = json.loads((ROOT / "resources/legacy/augment_item_audit.json").read_text(encoding="utf-8"))
        local = json.loads((ROOT / "resources/legacy/data" / "augments.json").read_text(encoding="utf-8"))
        self.assertEqual([row["name"] for row in audit["augments"]],
                         [row["augment_name"] for row in local])
        counts = self.backend.catalog()["augmentItemAudit"]
        self.assertEqual(counts, {"total": 206, "mechanism": 84,
                                  "heroConditional": 91, "noDirectItemShift": 31})
        self.assertTrue(all(row["effect"] and row["classification"] for row in audit["augments"]))
        for row in audit["augments"]:
            matching = [rule for rule in self.backend.rules if row["name"] in rule["whenAny"]]
            expected = list(dict.fromkeys(str(item_id) for rule in matching
                                          for key in ("preferredItemIds", "apPreferredItemIds",
                                                      "otherPreferredItemIds")
                                          for item_id in rule.get(key, [])))
            self.assertEqual(row["candidateItemIds"], expected, row["name"])

    def test_direct_quest_and_distinct_item_mechanisms(self):
        quest = self.backend.hero_detail("赏金猎人", ["艾卡西亚的陷落"])
        self.assertEqual(quest["requiredItems"], ["日炎圣盾", "璀璨回响"])
        on_hit = self.backend.hero_detail("复仇焰魂", ["虚幻武器"])
        self.assertIn("纳什之牙", on_hit["mechanismItems"])
        mana = self.backend.hero_detail("符文法师", ["由心及物"])
        self.assertIn("最大法力值收益", mana["buildProfile"]["name"])
        conditional = self.backend.hero_detail("复仇焰魂", ["回归基本功"])
        self.assertIn("英雄技能条件", conditional["buildProfile"]["name"])
        self.assertEqual(conditional["mechanismItems"], [])
        neutral = self.backend.hero_detail("复仇焰魂", ["家园卫士"])
        self.assertEqual(neutral["buildProfile"]["basis"], "existing_formula")

    def test_joint_rate_uses_owned_baseline_not_hero_baseline(self):
        data = {"schemaVersion": 1, "patch": "16.15", "reportDate": "2026-08-02", "contexts": [{
            "hero": "赏金猎人", "owned": ["炼狱导管"], "games": 1000, "wins": 510,
            "candidates": [
                {"name": "老练狙神", "games": 400, "wins": 220},
                {"name": "暴击飞弹", "games": 30, "wins": 25},
                {"name": "双生火焰", "games": 1100, "wins": 600},
            ],
            "items": [{"itemId": "2503", "games": 600, "wins": 318}],
        }]}
        joint = JointStats(data, "16.15", "2026-08-02")
        self.assertEqual(joint.status, "ready")
        value = joint.candidate("赏金猎人", ["炼狱导管"], "老练狙神")
        self.assertAlmostEqual(value["winRate"], 0.55)
        self.assertAlmostEqual(value["baselineWinRate"], 0.51)
        self.assertAlmostEqual(value["delta"], 0.04)
        self.assertTrue(value["reliable"])
        self.assertFalse(joint.candidate("赏金猎人", ["炼狱导管"], "暴击飞弹")["reliable"])
        self.assertIsNone(joint.candidate("赏金猎人", ["炼狱导管"], "双生火焰"))
        self.assertAlmostEqual(joint.item("赏金猎人", ["炼狱导管"], "2503")["winRate"], 0.53)
        self.assertEqual(JointStats(data, "16.18").status, "patch_mismatch")
        self.assertIsNone(JointStats(data, "16.18").candidate("赏金猎人", ["炼狱导管"], "老练狙神"))
        self.assertEqual(JointStats(data, "16.15", "2026-08-03").status, "patch_mismatch")

    def test_joint_evidence_changes_choice_ranking_only_when_reliable(self):
        data = {"schemaVersion": 1, "patch": "16.15", "reportDate": "2026-08-02", "contexts": [{
            "hero": "赏金猎人", "owned": ["炼狱导管"], "games": 1000, "wins": 510,
            "candidates": [{"name": "老练狙神", "games": 400, "wins": 220},
                           {"name": "暴击飞弹", "games": 30, "wins": 25}],
            "items": [],
        }]}
        backend = Backend(use_remote=False)
        backend.joint = JointStats(data, "16.15", "2026-08-02")
        result = backend.rank_choices("赏金猎人", ["老练狙神", "暴击飞弹"], owned=["炼狱导管"])
        self.assertEqual(result["conditionalStatus"], "available")
        self.assertTrue(result["choices"][0]["recommended"])
        self.assertFalse(result["choices"][1]["recommended"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
