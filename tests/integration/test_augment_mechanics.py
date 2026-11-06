import json
from pathlib import Path
import sys
import unittest

from hexgod.recommendation.aramgg import AramggEngine
from hexgod.paths import CLIENT_DATA_DIR


class MechanismPlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exact counts and ordering below describe this immutable snapshot.
        cls.engine = AramggEngine(folder=CLIENT_DATA_DIR / '16.19.1')

    def plan(self, hero, owned, ids=(), known=True):
        return self.engine.build_path(hero, owned, list(ids), known)

    def test_upgrade_infinity_changes_actual_next_purchase_without_inventing_rate(self):
        result = self.plan('齐天大圣', ['升级：无尽之刃'])
        self.assertEqual(result['nextItems'][0]['id'], 3031)
        self.assertTrue(result['nextItems'][0]['required'])
        self.assertEqual([i['id'] for i in result['synergyItems']], [3031])
        self.assertEqual(result['purchaseOrder']['basis'], 'mechanism_rule')
        self.assertIsNone(result['nextItems'][0]['winRate'])
        self.assertEqual(self.engine.hero_detail('齐天大圣', ['升级：无尽之刃'])['synergyItems'][0]['id'], 3031)
        self.assertNotIn(3031, [i['id'] for i in self.plan('齐天大圣', ['升级：无尽之刃'], [3031])['nextItems']])
        self.assertEqual(self.plan('齐天大圣', ['升级：无尽之刃'], [3031])['synergyItems'], [])

    def test_all_standalone_upgrades_only_recommend_their_named_items_or_alternatives(self):
        for name in self.engine.augments:
            if not name.startswith('升级：'): continue
            analysis = self.engine.mechanics.analyze(name)
            targets = set(analysis['required'] + analysis['alternatives'])
            for hero in ('齐天大圣','复仇焰魂','放逐之刃'):
                result = self.plan(hero, [name])
                self.assertTrue({i['id'] for i in result['synergyItems']} <= targets, (hero,name))

    def test_synergy_candidate_count_adapts_to_selection_and_completed_requirements(self):
        self.assertEqual(self.plan('齐天大圣', [])['synergyItems'], [])
        self.assertEqual(self.plan('齐天大圣', ['捐赠'])['synergyItems'], [])
        pair = ['升级：无尽之刃','升级：死亡之舞']
        self.assertEqual({i['id'] for i in self.plan('齐天大圣', pair)['synergyItems']}, {3031,6333})
        self.assertEqual([i['id'] for i in self.plan('齐天大圣', pair, [3031])['synergyItems']], [6333])
        self.assertEqual(self.plan('齐天大圣', pair, [3031,6333])['synergyItems'], [])
        self.assertEqual(len(self.plan('齐天大圣', ['终极九头蛇'])['synergyItems']), 3)

    def test_missing_upgrade_tooltip_still_resolves_item_from_name(self):
        result = self.plan('齐天大圣', ['升级：死亡之舞'])
        self.assertEqual(result['nextItems'][0]['id'], 6333)

    def test_spell_on_hit_searches_entire_catalog_and_respects_hero_profile(self):
        ap = self.plan('复仇焰魂', ['虚幻武器'])
        self.assertEqual(ap['nextItems'][0]['id'], 3115)
        self.assertIn(3100, ap['purchaseOrder']['route'])
        self.assertNotIn(6672, ap['purchaseOrder']['route'])  # third ATTACK != spell hit
        self.assertNotIn(3085, ap['purchaseOrder']['route'])  # bolts need actual attacks
        self.assertEqual(self.plan('齐天大圣', ['虚幻武器'])['nextItems'][0]['id'], 3078)
        self.assertEqual(self.plan('曙光女神', ['虚幻武器'])['nextItems'][0]['id'], 3084)

    def test_all_on_hit_augment_descriptions_are_covered_without_becoming_spell_hits(self):
        names = [n for n,a in self.engine.augments.items() if '攻击特效' in a['description']]
        self.assertEqual(len(names), 9)
        for name in names:
            analysis = self.engine.mechanics.analyze(name)
            self.assertIn('on_hit', analysis['features'], name)
            self.assertEqual('spell_on_hit' in analysis['features'], name == '虚幻武器', name)

    def test_stable_synergy_anchors_stop_after_bought_and_source_route_resumes(self):
        result = self.plan('复仇焰魂', ['虚幻武器'], [3115,3100])
        self.assertEqual(result['nextItems'][0]['id'], 2503)
        self.assertEqual(result['purchaseOrder']['route'][:2], [3115,3100])
        self.assertIsNone(result['nextItems'][0]['winRate'])  # modified combo != source combo

    def test_owned_spellblade_excludes_alternatives_and_recipe_investment_is_kept(self):
        result = self.plan('齐天大圣', ['虚幻武器'], [3078])
        forbidden = {3100,6662,3508,2510}
        self.assertFalse(forbidden & {i['id'] for i in result['synergyItems']})
        invested = self.plan('复仇焰魂', ['虚幻武器'], [1026])
        self.assertGreater(invested['nextItems'][0]['componentCredit'], 0)

    def test_three_hydra_quest_can_complete_despite_regular_alternative_group(self):
        result = self.plan('齐天大圣', ['终极九头蛇'], [3074])
        self.assertIn(result['nextItems'][0]['id'], (3748,6698))
        self.assertTrue({3748,6698} <= {i['id'] for i in result['synergyItems']})

    def test_multi_item_quest_and_shared_recipe_costs(self):
        result = self.plan('复仇焰魂', ['沃格勒特的巫师帽'], [3089])
        self.assertEqual(result['nextItems'][0]['id'], 3157)
        self.assertTrue({3068,6664} <= set(self.plan('齐天大圣', ['艾卡西亚的陷落'])['purchaseOrder']['route']))

    def test_crit_on_hit_pair_and_order_independence(self):
        pair = ['双发快射','虚幻武器']
        a, b = self.plan('齐天大圣', pair), self.plan('齐天大圣', pair[::-1])
        self.assertEqual(a['purchaseOrder'], b['purchaseOrder'])
        self.assertIn('crit_repeats_on_hit', a['buildProfile']['features'])
        self.assertTrue(any('CriticalStrike' in self.engine.items[str(i['id'])]['categories'] for i in a['synergyItems']))
        caster = self.plan('复仇焰魂', ['珠光护手','升级：无尽之刃'])
        self.assertEqual(caster['synergyItems'][0]['id'], 3031)
        self.assertEqual(caster['synergyItems'][1]['id'], 123430)

    def test_attribute_conversion_is_not_damage_type_conversion(self):
        result = self.plan('齐天大圣', ['物理转魔法'])
        self.assertEqual(result['buildProfile']['profile'], 'ap')
        self.assertNotIn(6610, result['purchaseOrder']['route'])
        self.assertTrue(result['nextItems'][0]['mechanismOnly'])
        reverse = self.plan('齐天大圣', ['魔法转物理'])
        self.assertNotIn(3135, reverse['purchaseOrder']['route'])  # do not blindly add magic penetration
        both = self.plan('齐天大圣', ['魔法转物理','物理转魔法'])
        self.assertEqual(both['buildProfile']['profile'], 'ad')
        self.assertTrue(both['buildProfile']['diagnostics'])

    def test_resource_gates_and_unknown_inventory(self):
        result = self.plan('放逐之刃', ['溢流'])
        self.assertFalse(any('Mana' in self.engine.items[str(i['id'])]['categories'] for i in result['nextItems']+result['synergyItems']))
        unknown = self.plan('齐天大圣', ['升级：无尽之刃'], known=False)
        self.assertIsNone(unknown['purchaseOrder']['nextId'])
        self.assertEqual(unknown['purchaseOrder']['basis'], 'inventory_unknown')

    def test_disabled_ultimate_does_not_recommend_ultimate_only_passives(self):
        result = self.plan('唤潮鲛姬', ['回归基本功','大招工具人'])
        self.assertNotIn(3118, result['purchaseOrder']['route'])
        self.assertTrue(result['buildProfile']['diagnostics'])

    def test_full_inventory_and_conflicting_required_item_are_explained(self):
        result = self.plan('齐天大圣', ['升级：无尽之刃'], [6610,3078,3111,3071,6333,6692])
        self.assertEqual(result['nextItems'], [])
        self.assertTrue(result['buildProfile']['diagnostics'])

    def test_no_item_direction_preserves_route_and_unknown_is_explicit(self):
        self.assertEqual(self.plan('齐天大圣', ['捐赠'])['purchaseOrder']['route'],
                         self.plan('齐天大圣', [])['purchaseOrder']['route'])
        self.assertEqual(self.engine.mechanics.analyze('不存在的强化')['status'], 'unknown')
        for name in self.engine.augments:
            self.assertIn(self.engine.mechanics.analyze(name)['status'], ('mechanism','hero_conditional','no_item_direction'))

    def test_exact_trio_evidence_does_not_replace_marginal_rate_or_add_overlapping_rows(self):
        result = self.engine.rank_choices('齐天大圣', ['升级：死亡之舞'], owned=['邦！','升级：焚天'])
        combo = result['choices'][0]['combinationEvidence']
        self.assertEqual(combo['games'], 151)
        self.assertAlmostEqual(combo['winRate'], 73/151)
        self.assertEqual(result['conditionalStatus'], 'unavailable')
        self.assertFalse(combo['reliable'])
        self.assertIsNone(self.engine.combination_evidence('齐天大圣', ['邦！'], '升级：死亡之舞'))

    def test_wukong_regular_choices_balance_samples_and_keep_true_rates(self):
        base = self.plan('齐天大圣', [])
        self.assertEqual(len(base['regularItems']), 3)
        self.assertEqual(base['regularSort'], 'sample_adjusted_win_rate')
        self.assertEqual([i['id'] for i in base['regularItems']], [3047,3156,3071])
        self.assertTrue(all(i['games'] > 2000 for i in base['regularItems']))
        self.assertFalse({6631,3065} & {i['id'] for i in base['regularItems']})
        self.assertGreater(len([i for i in base['regularItems'] if i['id'] != 3111]), 1)
        for item in base['regularItems']:
            if item['id'] in {r['id'] for r in self.engine.detail('齐天大圣')['builds'][0]['situationalItems']}:
                stat = next(r for r in self.engine.detail('齐天大圣')['builds'][0]['situationalItems'] if r['id'] == item['id'])
                self.assertAlmostEqual(item['winRate'], stat['wins']/stat['games'])

    def test_sparse_perfect_rate_does_not_beat_large_samples_and_rate_still_matters(self):
        build = {'stats': {'winRate': .5}, 'situationalItems': [
            {'id':3071, 'wins':5100, 'games':10000},
            {'id':3156, 'wins':540, 'games':1000},
            {'id':3065, 'wins':3000, 'games':10000},
            {'id':6631, 'wins':1, 'games':1}]}
        rows = self.engine.regular_items(build, [], [], True)
        self.assertNotIn(6631, [i['id'] for i in rows])
        self.assertEqual({r['id'] for r in rows[:2]}, {3071,3156})
        self.assertEqual(rows[0]['id'], 3156)  # stronger rate can beat larger volume
        self.assertEqual(rows[-1]['id'], 3065)  # volume alone cannot win
        self.assertAlmostEqual(next(r for r in rows if r['id']==3156)['winRate'], .54)

    def test_missing_single_item_stats_remain_unknown_in_sparse_fallback(self):
        rows = self.engine.regular_items({'stats': {'winRate': .5}, 'situationalItems': [
            {'id':3071, 'wins':51, 'games':100}]}, [3031,3156], [], True)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['id'], 3071)
        self.assertTrue(all(r['winRate'] is None and r['games'] is None for r in rows[1:]))

    def test_regular_and_mechanism_columns_have_separate_evidence_and_inventory_filtering(self):
        result = self.plan('复仇焰魂', ['虚幻武器'], [3115,3020])
        self.assertEqual(len(result['regularItems']), 3)
        self.assertNotIn(3115, [i['id'] for i in result['synergyItems']])
        self.assertNotIn(3020, [i['id'] for i in result['regularItems']])
        self.assertTrue(all(i['winRate'] is None and i['games'] is None for i in result['synergyItems']))
        self.assertEqual(result['itemConditionalStatus'], 'unavailable')


if __name__ == '__main__': unittest.main()
