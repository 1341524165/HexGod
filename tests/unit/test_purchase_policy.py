import sys
from pathlib import Path
import unittest

from hexgod.api.backend import Backend


class PurchasePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend(use_remote=False)
        cls.policy = cls.backend.purchase_policy

    def path(self, hero, ids):
        return self.backend.build_path(hero, [], ids, True)

    def test_rabadon_does_not_jump_ahead_of_veigar_core(self):
        path = self.path('邪恶小法师', [])
        self.assertEqual(path['nextItems'][0]['id'], 6655)
        self.assertNotIn(3089, [row['id'] for row in path['nextItems']])
        self.assertEqual(path['purchaseOrder']['basis'], 'formula_reference')
        self.assertIsNone(path['purchaseOrder']['games'])

    def test_rell_core_is_not_removed_by_popularity_filter(self):
        self.assertEqual(self.path('镕铁少女', [])['nextItems'][0]['id'], 3050)

    def test_full_inventory_does_not_suggest_a_seventh_item(self):
        path = self.path('邪恶小法师', [6655,4645,3089,3135,3157,3020])
        self.assertEqual(path['nextItems'], [])

    def test_inventory_failure_ignores_old_ids_and_does_not_claim_next_step(self):
        path = self.backend.build_path('邪恶小法师', [], [6655,3089], False)
        self.assertEqual(path['currentItems'], [])
        self.assertEqual(path['purchaseOrder']['basis'], 'inventory_unknown')
        self.assertIsNone(path['purchaseOrder']['nextId'])

    def test_component_credit_counts_duplicate_recipe_parts_once_each(self):
        self.assertEqual(self.policy.credit('3089', [1058]), 1200)
        self.assertEqual(self.policy.credit('3089', [1058,1058]), 2400)
        path = self.path('邪恶小法师', [1058,1058])
        self.assertEqual(path['nextItems'][0]['id'], 3089)
        self.assertEqual(path['nextItems'][0]['remainingCost'], 1100)
        self.assertEqual(path['purchaseOrder']['basis'], 'components')

    def test_boot_upgrades_and_mutually_alternative_plans(self):
        self.assertTrue(self.policy.alternative_owned('3111', {'3020'}))
        self.assertFalse(self.policy.alternative_owned('3111', {'1001'}))
        self.assertIn('3111', self.policy.owned_routes([3173]))
        for target, owned in [('3137',{'3135'}), ('3036',{'3033'}), ('3156',{'3053'})]:
            self.assertTrue(self.policy.alternative_owned(target, owned))

    def test_warmog_does_not_count_components_consumed_by_its_recipe(self):
        # Owned HP components disappear when the completed Warmog is bought.
        remaining = self.policy.credit('3083', [1011,1011,3211], remaining=True)
        self.assertLess(len(remaining), 3)
        path = self.path('熔岩巨兽', [3050,1011,3211])
        stats = self.backend.item_catalog
        hp = sum(stats[i].get('stats',{}).get('FlatHPPoolMod',0)
                 for i in self.policy.credit('3083',[3050,1011,3211],remaining=True))
        expected = (hp + stats['3083']['stats']['FlatHPPoolMod']) * 1.12 >= 2000
        self.assertEqual(path['warmogPassiveReady'], expected)

    def test_every_hero_progresses_without_rebuy_or_conflicting_alternatives(self):
        for hero in self.backend.heroes:
            inventory=[]
            for step in range(6):
                path=self.path(hero, inventory)
                for item in path['nextItems'] + path['synergyItems']:
                    self.assertNotIn(str(item['id']), self.policy.owned_routes(inventory), hero)
                    self.assertFalse(self.policy.alternative_owned(str(item['id']), set(map(str,inventory))), hero)
                    self.assertIn(str(item['id']), self.backend.direct_buyable_item_ids, hero)
                    self.assertGreaterEqual(item['remainingCost'], 0)
                if not path['nextItems']:
                    break
                inventory.append(path['nextItems'][0]['id'])
            if len(inventory)==6:
                self.assertFalse(self.path(hero,inventory)['nextItems'], hero)
