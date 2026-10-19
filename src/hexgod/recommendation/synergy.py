"""Version-bound observed joint counts and separately labelled mechanism rules."""
from __future__ import annotations

import math


def observed_rate(wins, games):
    return wins / games if games else None


def wilson_lower_bound(wins, games, z=1.96):
    if games <= 0:
        return 0.0
    p = wins / games
    denominator = 1 + z * z / games
    center = p + z * z / (2 * games)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * games)) / games)
    return (center - margin) / denominator


def checked_counts(row, parent_games=None, parent_wins=None):
    """Reject invalid or fractional counts before showing a precise percentage."""
    games, wins = row.get("games"), row.get("wins")
    if type(games) is not int or type(wins) is not int or games <= 0 or wins < 0 or wins > games:
        return None
    if parent_games is not None and games > parent_games:
        return None
    if parent_wins is not None and wins > parent_wins:
        return None
    return wins, games


class JointStats:
    """Exact observed rates for the current patch, never inferred from trio tiers."""

    def __init__(self, payload, patch, report_date=None):
        self.status = "unavailable"
        self.contexts = {}
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.status = "invalid"
            return
        if (payload.get("schemaVersion") != 1 or payload.get("patch") != patch or
                (report_date is not None and payload.get("reportDate") != report_date)):
            self.status = "patch_mismatch"
            return
        if not isinstance(payload.get("contexts"), list):
            self.status = "invalid"
            return
        for row in payload["contexts"]:
            if not isinstance(row, dict) or checked_counts(row) is None:
                continue
            hero = row.get("hero")
            owned = row.get("owned")
            if not isinstance(hero, str) or not isinstance(owned, list) or not owned:
                continue
            if not all(isinstance(name, str) and name for name in owned) or len(owned) != len(set(owned)):
                continue
            key = (hero, tuple(sorted(owned)))
            if key in self.contexts:
                continue
            self.contexts[key] = row
        self.status = "ready" if self.contexts else "empty"

    def context(self, hero, owned):
        return self.contexts.get((hero, tuple(sorted(set(owned)))))

    def candidate(self, hero, owned, name, minimum_games=250):
        context = self.context(hero, owned)
        if context is None:
            return None
        baseline = checked_counts(context)
        if baseline is None:
            return None
        base_wins, base_games = baseline
        for row in context.get("candidates", []) if isinstance(context.get("candidates"), list) else []:
            if not isinstance(row, dict) or row.get("name") != name:
                continue
            counts = checked_counts(row, base_games, base_wins)
            if counts is None:
                continue
            wins, games = counts
            rate = observed_rate(wins, games)
            base_rate = observed_rate(base_wins, base_games)
            return {"games": games, "wins": wins, "winRate": rate,
                    "baselineGames": base_games, "baselineWinRate": base_rate,
                    "delta": rate - base_rate,
                    "wilsonLowerBound": wilson_lower_bound(wins, games),
                    "reliable": games >= minimum_games,
                    "label": "观察到的条件胜率"}
        return None

    def item(self, hero, owned, item_id, minimum_games=250):
        context = self.context(hero, owned)
        if context is None:
            return None
        baseline = checked_counts(context)
        if baseline is None:
            return None
        for row in context.get("items", []) if isinstance(context.get("items"), list) else []:
            if not isinstance(row, dict) or str(row.get("itemId")) != str(item_id):
                continue
            counts = checked_counts(row, baseline[1], baseline[0])
            if counts is None:
                continue
            wins, games = counts
            return {"games": games, "wins": wins,
                    "winRate": observed_rate(wins, games),
                    "wilsonLowerBound": wilson_lower_bound(wins, games),
                    "reliable": games >= minimum_games,
                    "label": "观察到的海克斯＋装备胜率"}
        return None


def build_profile(hero, owned, rules, formula, conditional_names=()):
    matched = []
    for rule in rules:
        if rule.get("hero") not in (hero, "*"):
            continue
        triggers = [x for x in owned if x in rule.get("whenAny", [])]
        if triggers:
            matched.append((rule, triggers))
    conditional = [name for name in owned if name in conditional_names]
    if conditional:
        matched.append(({"name": "英雄技能条件", "reason":
                         "该强化的出装方向取决于具体英雄技能或对局条件；当前没有逐英雄机制验证，不强行指定装备。",
                         "preferredItemIds": []}, conditional))
    if matched:
        core_ids = {str(item.get("id")) for item in formula.get("coreItems", [])}
        def item_ids(rule):
            if "apCoreAny" in rule:
                key = "apPreferredItemIds" if core_ids.intersection(rule["apCoreAny"]) else "otherPreferredItemIds"
                return rule.get(key, [])
            return rule.get("preferredItemIds", [])
        preferred = list(dict.fromkeys(str(item_id) for rule, _ in matched
                                       for item_id in item_ids(rule)))
        hero_preferred = list(dict.fromkeys(str(item_id) for rule, _ in matched
                                            if rule.get("hero") == hero
                                            for item_id in item_ids(rule)))
        required = list(dict.fromkeys(str(item_id) for rule, _ in matched
                                      for item_id in rule.get("requiredItemIds", [])))
        return {"name": "＋".join(rule["name"] for rule, _ in matched),
                "basis": "mechanism_rule",
                "triggers": list(dict.fromkeys(name for _, triggers in matched for name in triggers)),
                "reason": " ".join(rule["reason"] for rule, _ in matched),
                "preferredItemIds": preferred, "heroSpecificItemIds": hero_preferred,
                "requiredItemIds": required}
    return {"name": "常规参考", "basis": "existing_formula", "triggers": [],
            "reason": "按已有英雄精选出装和通用装备统计展示。",
            "preferredItemIds": [str(item["id"]) for item in formula.get("coreItems", [])],
            "heroSpecificItemIds": [],
            "requiredItemIds": []}


def trio_clue(trios, owned, candidate):
    """Only a count of published three-augment rows, not a pair sample size."""
    if not owned or not candidate:
        return None
    required = set(owned) | {candidate}
    rows = [row for row in trios
            if required.issubset(set(str(row.get("combo", "")).split(" + ")))]
    if not rows:
        return None
    return {"publishedTrioRows": len(rows),
            "maxTrioGames": max(row.get("games", 0) for row in rows)}
