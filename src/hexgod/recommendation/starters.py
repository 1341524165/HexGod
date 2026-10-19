"""Independent, budgeted opening bundles and their live purchase progress."""
from collections import Counter
from copy import deepcopy
import math


OPENING_SECONDS = 90


def nonnegative(value):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0)


class StarterBuilds:
    def __init__(self, payload, catalog, heroes, patch):
        if payload.get("patch") != patch or payload.get("schemaVersion") != 1:
            raise ValueError("出门装方案与本地数据版本不一致。")
        self.budget = payload.get("budget")
        if self.budget != 1400:
            raise ValueError("出门装预算必须为 1400 金。")
        self.catalog = catalog
        self.plans = {}
        for row in payload.get("plans", []):
            items = []
            for entry in row["items"]:
                item_id, quantity = str(entry["id"]), entry.get("quantity", 1)
                item = catalog.get(item_id, {})
                cost = item.get("gold", {}).get("total")
                if (type(quantity) is not int or not 1 <= quantity <= 5 or
                        not item.get("gold", {}).get("purchasable") or
                        item.get("inStore") is False or not item.get("maps", {}).get("12") or
                        not nonnegative(cost) or cost == 0):
                    raise ValueError("出门装引用了无效或不可购买的装备：" + item_id)
                items.append({"id": int(item_id), "name": item["name"], "quantity": quantity,
                              "unitCost": int(cost), "cost": int(cost) * quantity,
                              "consumable": "Consumable" in item.get("tags", [])})
            ids = {item["id"] for item in items}
            total = sum(item["cost"] for item in items)
            if (not items or len(ids) != len(items) or len(items) > 6 or total > self.budget or
                    (2003 in ids and 2031 in ids)):
                raise ValueError("出门装超预算、重复或药水冲突。")
            for hero in row["heroes"]:
                if hero in self.plans:
                    raise ValueError("英雄出门装方案重复：" + hero)
                self.plans[hero] = {"items": items, "budget": self.budget,
                                    "totalCost": total, "remainingGold": self.budget - total,
                                    "reason": row["reason"], "basis": row.get("basis", "curated"), "games": None}
        if set(self.plans) != set(heroes):
            raise ValueError("出门装方案没有完整覆盖本地英雄。")

    def plan(self, hero, item_ids=(), item_counts=None):
        plan = deepcopy(self.plans[hero])
        inventory = Counter(str(value) for value in item_ids if type(value) is int and value > 0)
        # Counts come from the same Live Client snapshot as the IDs. Never let
        # a stale count invent an item absent from the actual inventory.
        if isinstance(item_counts, dict):
            for item_id in inventory:
                count = item_counts.get(item_id)
                if type(count) is int and 1 <= count <= 10:
                    inventory[item_id] = count
        available = inventory.copy()

        def component_credit(item_id, ancestry=()):
            if item_id in ancestry:
                return 0
            item = self.catalog[item_id]
            if available[item_id] > 0:
                available[item_id] -= 1
                return int(item["gold"]["total"])
            return sum(component_credit(child, ancestry + (item_id,))
                       for child in item.get("from", []) if child in self.catalog)

        for item in plan["items"]:
            item_id = str(item["id"])
            item["ownedQuantity"] = min(inventory[item_id], item["quantity"])
            credit = sum(component_credit(item_id) for _ in range(item["quantity"]))
            item["remainingCost"] = max(0, item["cost"] - credit)
        plan["remainingCost"] = sum(item["remainingCost"] for item in plan["items"])
        plan["complete"] = plan["remainingCost"] == 0
        return plan


class OpeningTracker:
    """Latch the end of the opening phase until a different match is observed.

    The API does not report whether the shop is open. A bounded 90-second
    opening window, purchase progress and deaths are conservative signals.
    Missing API data suppresses the opening UI without resetting this latch.
    """
    def __init__(self):
        self.start = None
        self.hero = None
        self.last_time = 0
        self.finished = False
        self.reason = "unavailable"
        self.session_id = None

    def stage(self, hero, plan, item_ids, items_known, live, catalog, legendary_ids):
        if not isinstance(live, dict) or not live.get("available") or live.get("hero") != hero:
            return "items", "unavailable"
        game_time, start = live.get("gameTime"), live.get("matchStartMs")
        if not nonnegative(game_time) or not nonnegative(start) or start == 0:
            return "items", "unavailable"
        if (self.start is None or live.get('sessionId') != self.session_id or abs(start - self.start) > 60000 or
                game_time < self.last_time - 30):
            self.session_id = live.get('sessionId')
            self.start, self.hero = start, hero
            self.finished, self.reason = False, "opening"
        elif self.hero != hero:
            return "items", "hero_mismatch"
        self.last_time = game_time
        if self.finished:
            return "items", self.reason
        if game_time >= OPENING_SECONDS:
            self.finished, self.reason = True, "opening_elapsed"
        elif nonnegative(live.get("deaths")) and live["deaths"] > 0:
            self.finished, self.reason = True, "respawn"
        elif not items_known:
            return "items", "inventory_unavailable"
        elif any(str(item_id) in legendary_ids for item_id in item_ids):
            self.finished, self.reason = True, "completed_item"
        elif plan["complete"]:
            self.finished, self.reason = True, "purchased"
        else:
            gold = live.get("currentGold")
            # Do not prompt buying a whole second opening set after the player
            # has chosen a different set. Recipe components receive credit.
            if nonnegative(gold) and plan["remainingCost"] > gold + 1:
                if any(catalog.get(str(item_id), {}).get("gold", {}).get("total", 0) > 0
                       for item_id in item_ids):
                    self.finished, self.reason = True, "different_purchase"
                else:
                    return "items", "waiting_for_starting_gold"
            elif any(str(item_id) not in catalog for item_id in item_ids):
                return "items", "unknown_inventory"
        return ("items", self.reason) if self.finished else ("starter", "opening")
