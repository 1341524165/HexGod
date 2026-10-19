"""Purchase-stage constraints, separate from marginal item win rates."""


class ItemOrder:
    def __init__(self, payload, patch, heroes, buyable):
        if payload.get("schemaVersion") != 1 or payload.get("patch") != patch:
            raise ValueError("装备顺序与本地数据版本不一致。")
        self.openings = payload.get("heroOpenings", {})
        for hero, plan in self.openings.items():
            ids = plan.get("items", [])
            if (hero not in heroes or not ids or len(ids) != len(set(ids)) or
                    any(str(i) not in buyable for i in ids + plan.get("deferUntilCore", []))):
                raise ValueError("装备顺序引用了未知英雄或不可购买装备。")

    def apply(self, hero, candidates, purchased_routes, first_complete, items_known):
        plan = self.openings.get(hero)
        route = plan.get("items", []) if plan else []
        remaining = [i for i in route if str(i) not in purchased_routes]
        result = {"basis": "single_item", "route": route, "nextId": None,
                  "reason": "只有单件统计，暂无逐件路径样本。", "games": None}
        if plan and remaining:
            # Every route step is a main candidate, but later steps cannot
            # become alternatives for buying the current one.
            wanted = remaining[0]
            blocked = set(remaining[1:] + plan.get("deferUntilCore", []))
            candidates = [row for row in candidates if row["id"] not in blocked]
            candidates.sort(key=lambda row: row["id"] == wanted, reverse=True)
            result.update(basis="curated", nextId=wanted, reason=plan["reason"])
        elif first_complete and items_known:
            # Rare final-inventory items are not good default rush evidence.
            # This is explicitly a popularity fallback, NOT slot-one stats.
            peak = max((row.get("popularityGames", row["games"]) for row in candidates), default=0)
            common = [row for row in candidates if row.get("popularityGames", row["games"]) >= max(250, peak * 0.4)]
            if common:
                candidates = common
                result.update(basis="common_first_fallback",
                              reason="首件从该英雄常用装备中比较；使用量至少为最常用候选的40%，并非首件胜率。")
        return candidates, result
