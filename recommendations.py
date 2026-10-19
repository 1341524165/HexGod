"""Validated local dataset and recommendation engine; no capture or network IO."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import time

APP_DIR = Path(__file__).resolve().parent
SOURCE_DIR = Path(os.environ.get("HEXGOD_SOURCE_DIR", str(APP_DIR))).resolve()
DATA_DIR = SOURCE_DIR / "data"
CACHE_DIR = APP_DIR / ".cache"
LATE_COMPLETE_ITEM_IDS = {"3083", "443083"}  # Warmog's passive needs prior bonus health.
ITEM_CATALOG_PATH = APP_DIR / "sources" / "riot_item_16.15.1_zh_CN.json"
WARMOG_REQUIRED_BONUS_HEALTH = 2000
WARMOG_EQUIPMENT_HEALTH_MULTIPLIER = 1.12
# Older formula/rule IDs and automatic upgrades must resolve to an item the
# Howling Abyss shop can actually sell in this data version.
SHOP_ROUTE_ITEM_IDS = {
    "3040": "3003", "3042": "3004", "3121": "3119",
    "3430": "123430", "4010": "8010", "4011": "124011", "6697": "126697",
}
FORMULA_HERO_NAMES = {"费德提克": "远古恐惧"}
os.environ["ARAM_ASSISTANT_DATA_DIR"] = str(DATA_DIR)
os.environ["ARAM_ASSISTANT_OCR_MODEL_DIR"] = str(SOURCE_DIR / "models")
sys.path[:0] = [str(SOURCE_DIR / "tools"), str(SOURCE_DIR / "tools" / "ocr-sidecar")]

from augment_pipeline import load_presets, load_runtime, match_ocr_texts, normalize_text
from starter_builds import OpeningTracker, StarterBuilds
from item_order import ItemOrder
from purchase_policy import PurchasePolicy
from synergy import JointStats, build_profile, trio_clue


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def reliability(games):
    return "样本充足" if games >= 1000 else "样本有限" if games >= 30 else "样本过少"


def ranking_score(row, hero_win_rate):
    """Shrink sparse statistics toward a neutral score; never treat one win as certainty."""
    games = max(0, int(number(row.get("games"))))
    hex_score = row.get("hex_score")
    if hex_score is not None and row.get("hex_tier") != "insufficient":
        raw = number(hex_score, 50)
    else:
        raw = 50 + (number(row.get("win_rate")) - hero_win_rate) * 100
    return 50 + (raw - 50) * games / (games + 200)


class RecommendationEngine:
    def __init__(self):
        self.runtime = load_runtime(DATA_DIR)
        self.item_names = json.loads((DATA_DIR / "item_names.json").read_text(encoding="utf-8"))
        self.heroes = {row["hero"]: row for row in self.runtime["champions"]}
        self.aliases = self.runtime["augment_aliases"]
        self.augments = {row["augment_name"]: row for row in self.runtime["augments"]}
        self.aliases = dict(self.aliases)
        # Common spoken variant; the official name in this data pack is 炽燃利息.
        if "炽燃利息" in self.augments:
            self.aliases["灼热利息"] = ["炽燃利息"]
        formula_path = DATA_DIR / "hero_formula_items.json"
        self.formulas = json.loads(formula_path.read_text(encoding="utf-8")) if formula_path.exists() else {}
        for source_name, canonical in FORMULA_HERO_NAMES.items():
            formulas_by_hero = self.formulas.get("byHeroName", {})
            if source_name in formulas_by_hero and canonical in self.heroes:
                formulas_by_hero[canonical] = formulas_by_hero.pop(source_name)
        self.hero_aliases = {}
        self.hero_lookup = {name.casefold(): name for name in self.heroes}
        for alias, row in self.formulas.get("byAlias", {}).items():
            source_name = row.get("heroName", "")
            canonical = FORMULA_HERO_NAMES.get(source_name, source_name)
            self.hero_aliases.setdefault(canonical, []).append(alias)
            if canonical in self.heroes:
                self.hero_lookup[alias.casefold()] = canonical
        rules_path = APP_DIR / "synergy_rules.json"
        rules_data = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.exists() else {}
        if rules_data.get("schemaVersion") != 2 or rules_data.get("patch") != self.runtime["meta"]["version"]:
            raise ValueError("海克斯出装枚举与本地数据版本不一致。")
        self.rules = rules_data.get("rules", [])
        self.completed_item_ids = {
            str(item.get("id")) for formula in self.formulas.get("byHeroName", {}).values()
            for item in formula.get("coreItems", []) if item.get("id") is not None
        } | {
            str(item_id) for rule in self.rules
            for key in ("preferredItemIds", "apPreferredItemIds", "otherPreferredItemIds", "requiredItemIds")
            for item_id in rule.get(key, [])
        }
        item_catalog = json.loads(ITEM_CATALOG_PATH.read_text(encoding="utf-8"))
        if item_catalog.get("version", "").rsplit(".", 1)[0] != self.runtime["meta"]["version"]:
            raise ValueError("装备商店资料与本地统计版本不一致。")
        self.item_catalog = item_catalog["data"]
        self.purchase_policy = PurchasePolicy(self.item_catalog, SHOP_ROUTE_ITEM_IDS)
        if self.completed_item_ids - self.item_catalog.keys():
            raise ValueError("装备商店资料缺少推荐候选。")
        self.shop_candidate_item_ids = {
            SHOP_ROUTE_ITEM_IDS.get(item_id, item_id) for item_id in self.completed_item_ids
        }
        if self.shop_candidate_item_ids - self.item_catalog.keys():
            raise ValueError("装备商店资料缺少购买路线目标。")
        for item_id, item in self.item_catalog.items():
            self.item_names.setdefault(item_id, item["name"])
        self.direct_buyable_item_ids = {
            item_id for item_id in self.shop_candidate_item_ids
            if self.shop_available(item_id)
        }
        if any(not self.shop_available(item_id) for item_id in SHOP_ROUTE_ITEM_IDS.values()):
            raise ValueError("购买路线指向了不能在嚎哭深渊直接购买的装备。")
        self.legendary_item_ids = {
            item_id for item_id, item in self.item_catalog.items()
            if self.shop_available(item_id) and number(item.get("gold", {}).get("total")) >= 1800
            and "Boots" not in item.get("tags", [])
            and "Consumable" not in item.get("tags", [])
        } | {
            item_id for item_id in self.completed_item_ids
            if not any(word in self.item_names.get(item_id, "") for word in ("靴", "鞋"))
        }
        self.starters = StarterBuilds(
            json.loads((APP_DIR / "starter_builds.json").read_text(encoding="utf-8-sig")),
            self.item_catalog, self.heroes, self.runtime["meta"]["version"])
        self.opening_tracker = OpeningTracker()
        self.item_order = ItemOrder(
            json.loads((APP_DIR / "item_order.json").read_text(encoding="utf-8")),
            self.runtime["meta"]["version"], self.heroes, self.direct_buyable_item_ids)
        self.conditional_augments = set(rules_data.get("abilityConditional", []))
        self.no_direct_augments = set(rules_data.get("noDirectItemShift", []))
        rule_names = {name for rule in self.rules for name in rule.get("whenAny", [])}
        self.rule_names = rule_names
        if (rule_names & self.conditional_augments or rule_names & self.no_direct_augments or
                self.conditional_augments & self.no_direct_augments or
                rule_names | self.conditional_augments | self.no_direct_augments != set(self.augments)):
            raise ValueError("海克斯出装枚举不完整或分类重复。")
        if any(str(item_id) not in self.item_names for rule in self.rules
               for key in ("preferredItemIds", "apPreferredItemIds", "otherPreferredItemIds", "requiredItemIds")
               for item_id in rule.get(key, [])):
            raise ValueError("海克斯出装规则引用了未知装备。")
        audit = json.loads((APP_DIR / "augment_item_audit.json").read_text(encoding="utf-8"))
        self.augment_effects = {row["name"]: row["effect"] for row in audit.get("augments", [])}
        if audit.get("patch") != rules_data["patch"] or set(self.augment_effects) != set(self.augments):
            raise ValueError("海克斯逐项审查表与规则或本地数据不一致。")
        joint_path = APP_DIR / "joint_stats.json"
        self.joint_error = None
        try:
            joint_payload = json.loads(joint_path.read_text(encoding="utf-8")) if joint_path.exists() else None
            self.joint = JointStats(joint_payload, self.runtime["meta"]["version"],
                                    self.runtime["meta"]["report_date"])
        except (OSError, ValueError, TypeError) as error:
            self.joint = JointStats(None, self.runtime["meta"]["version"],
                                    self.runtime["meta"]["report_date"])
            self.joint_error = str(error)

    def shop_available(self, item_id):
        item = self.item_catalog.get(str(item_id), {})
        return (item.get("gold", {}).get("purchasable") is True and
                item.get("inStore") is not False and
                item.get("maps", {}).get("12") is True)

    def canonical_augment(self, name):
        text = str(name or "").strip()
        if text in self.augments:
            return text
        names = self.aliases.get(text) or self.aliases.get(normalize_text(text))
        return names[0] if names and names[0] in self.augments else None

    def normalize_owned(self, owned):
        if owned is None:
            return []
        if not isinstance(owned, list) or len(owned) > 8:
            raise ValueError("已选强化列表格式有误。")
        result = []
        for raw in owned:
            name = self.canonical_augment(raw)
            if not name and isinstance(raw, str) and raw.startswith('未收录：') and 2 <= len(raw[4:]) <= 12:
                name = raw
            if not name:
                raise ValueError("无法识别已选强化：" + str(raw))
            if name not in result:
                result.append(name)
        return result

    def catalog(self):
        heroes = sorted(self.heroes.values(), key=lambda r: (
            number(r.get("games")) >= 1000, number(r.get("win_rate")), number(r.get("games"))
        ), reverse=True)
        return {
            "meta": self.runtime["meta"],
            "heroes": [{"name": h["hero"], "fullName": h.get("full_name", h["hero"]),
                        "winRate": h.get("win_rate", 0), "games": h.get("games", 0),
                        "pickRate": h.get("pick_rate", 0),
                        "availability": h.get("data_availability", "observed"),
                        "aliases": " ".join(self.hero_aliases.get(h["hero"], []))}
                       for h in heroes],
            "presets": json.loads((DATA_DIR / "augment_presets.json").read_text(encoding="utf-8")),
            "augments": sorted(self.augments),
            "jointDataStatus": self.joint.status,
            "jointDataError": self.joint_error,
            "augmentItemAudit": {"total": len(self.augments), "mechanism": len(self.rule_names),
                                 "heroConditional": len(self.conditional_augments),
                                 "noDirectItemShift": len(self.no_direct_augments)},
        }

    def validate_hero(self, hero):
        if hero not in self.heroes:
            raise ValueError("请先从左侧列表选择英雄。")
        return self.heroes[hero]

    def hero_detail(self, hero, owned=None):
        champion = self.validate_hero(hero)
        owned = self.normalize_owned(owned)
        formula = self.formulas.get("byHeroName", {}).get(hero, {})
        profile = build_profile(hero, owned, self.rules, formula, self.conditional_augments)
        preferred_ids = {SHOP_ROUTE_ITEM_IDS.get(item_id, item_id)
                         for item_id in profile["preferredItemIds"]}
        rows = self.runtime["hero_item_builds"].get(hero, [])
        ranked = sorted((row for row in rows if self.shop_available(row.get("item_id"))), key=lambda r: (
            number(r.get("games")) >= 30,
            number(r.get("recommendation_score")) * number(r.get("games")) / (number(r.get("games")) + 200),
            number(r.get("games")),
        ), reverse=True)
        items = []
        mechanism_items = []
        if profile["basis"] == "mechanism_rule":
            mechanism_items = [self.item_names.get(str(r.get("item_id")), "装备 " + str(r.get("item_id")))
                               for r in ranked
                               if str(r.get("item_id")) in preferred_ids and
                               number(r.get("games")) >= 30][:4]
        for r in ranked[:6]:
            item_id = r.get("item_id")
            items.append({"name": self.item_names.get(str(item_id), "装备 " + str(item_id)),
                          "id": item_id, "winRate": r.get("win_rate", 0),
                          "pickRate": r.get("pick_rate", 0), "games": r.get("games", 0),
                          "preferred": (str(item_id) in preferred_ids and number(r.get("games")) >= 30)
                          if profile["basis"] == "mechanism_rule" else False,
                          "reliability": reliability(number(r.get("games"))),
                          "joint": self.joint.item(hero, owned, item_id) if owned else None})
        wr = number(champion.get("win_rate"))
        augment_rows = sorted(self.runtime["hero_augment_stats"].get(hero, []), key=lambda r: (
            number(r.get("games")) >= 30, ranking_score(r, wr), number(r.get("games"))
        ), reverse=True)
        core_items = []
        ordered = self.item_order.openings.get(hero, {}).get("items", [])
        core_source = [{"id": item_id} for item_id in ordered] if ordered else formula.get("coreItems", [])
        for item in core_source:
            item_id = SHOP_ROUTE_ITEM_IDS.get(str(item.get("id")), str(item.get("id")))
            if (item_id in self.direct_buyable_item_ids and
                    all(str(existing["id"]) != item_id for existing in core_items)):
                core_items.append({"id": int(item_id), "name": self.item_names[item_id]})
        return {"hero": hero, "winRate": wr, "games": champion.get("games", 0),
                "pickRate": champion.get("pick_rate", 0), "items": items,
                "coreItems": core_items,
                "starterItems": self.starters.plan(hero)["items"],
                "starterPlan": self.starters.plan(hero),
                "buildProfile": profile,
                "mechanismItems": mechanism_items,
                "requiredItems": [self.item_names[SHOP_ROUTE_ITEM_IDS.get(item_id, item_id)]
                                  for item_id in profile["requiredItemIds"]
                                  if SHOP_ROUTE_ITEM_IDS.get(item_id, item_id) in self.direct_buyable_item_ids],
                "conditionalEffects": [{"name": name, "effect": self.augment_effects[name]}
                                       for name in owned if name in self.conditional_augments],
                "noDirectOwned": [name for name in owned if name in self.no_direct_augments],
                "owned": owned,
                "jointDataStatus": self.joint.status,
                "jointContextAvailable": bool(self.joint.context(hero, owned)) if owned else False,
                "topAugments": [{"name": r["augment_name"], "rarity": r.get("rarity", ""),
                                 "winRate": r.get("win_rate", 0), "games": r.get("games", 0)}
                                for r in augment_rows[:5]]}

    def build_path(self, hero, owned=None, item_ids=None, items_known=False, live_context=None):
        champion = self.validate_hero(hero)
        owned = self.normalize_owned(owned)
        if item_ids is None:
            item_ids = []
        if not isinstance(item_ids, list) or len(item_ids) > 20:
            raise ValueError("当前装备列表格式有误。")
        item_ids = [value for value in item_ids if type(value) is int and value > 0] if items_known else []
        purchased = {str(value) for value in item_ids}
        live = live_context if isinstance(live_context, dict) else {}
        starter_plan = self.starters.plan(hero, item_ids if items_known else [],
                                          live.get("itemCounts") if items_known else None)
        stage, opening_reason = self.opening_tracker.stage(
            hero, starter_plan, item_ids, items_known, live,
            self.item_catalog, self.legendary_item_ids)
        purchased_routes = self.purchase_policy.owned_routes(item_ids)
        formula = self.formulas.get("byHeroName", {}).get(hero, {})
        profile = build_profile(hero, owned, self.rules, formula, self.conditional_augments)
        core = {SHOP_ROUTE_ITEM_IDS.get(str(item["id"]), str(item["id"]))
                for item in formula.get("coreItems", []) if item.get("id") is not None}
        ordered_core = {str(i) for i in self.item_order.openings.get(hero, {}).get("items", [])}
        core |= ordered_core
        preferred = ({SHOP_ROUTE_ITEM_IDS.get(item_id, item_id) for item_id in profile["preferredItemIds"]}
                     if profile["basis"] == "mechanism_rule" else set())
        hero_preferred = {SHOP_ROUTE_ITEM_IDS.get(item_id, item_id)
                          for item_id in profile.get("heroSpecificItemIds", [])}
        required = {SHOP_ROUTE_ITEM_IDS.get(item_id, item_id)
                    for item_id in profile["requiredItemIds"]}
        rows = {str(row.get("item_id")): row for row in self.runtime["hero_item_builds"].get(hero, [])}
        baseline = number(champion.get("win_rate"), 0.5)
        first_complete = not bool(purchased & self.legendary_item_ids)
        completed_count = len(purchased & self.legendary_item_ids)
        after_warmog_components = self.purchase_policy.credit('3083', item_ids, remaining=True)
        owned_equipment_health = sum(number(self.item_catalog.get(item_id, {}).get("stats", {}).get("FlatHPPoolMod"))
                                     for item_id in after_warmog_components)
        warmog_health = number(self.item_catalog["3083"].get("stats", {}).get("FlatHPPoolMod"))
        warmog_passive_ready = (not first_complete and
                                (owned_equipment_health + warmog_health) *
                                WARMOG_EQUIPMENT_HEALTH_MULTIPLIER >= WARMOG_REQUIRED_BONUS_HEALTH)
        candidates, synergy = [], []
        for item_id in self.shop_candidate_item_ids | core | preferred | required:
            if (item_id in purchased_routes or item_id not in self.item_names or
                    item_id not in self.direct_buyable_item_ids):
                continue
            row = rows.get(item_id)
            games = int(number(row.get("games"))) if row else 0
            if games < 250 and item_id not in required | preferred | core:
                continue
            joint = self.joint.item(hero, owned, item_id) if owned else None
            use_joint = joint is not None and joint["reliable"]
            rate = number(joint["winRate"]) if use_joint else number(row.get("win_rate")) if row else None
            sample = int(joint["games"]) if use_joint else games
            adjusted = (sample * rate + 400 * baseline) / (sample + 400) if rate is not None and sample else baseline
            # recommendation_score already combines this hero's item evidence.
            # Shrink sparse rows; the curated core is only a modest tie-breaker.
            score = (number(row.get("recommendation_score")) * games / (games + 500)
                     if row else -12) + (6 if item_id in core else 0)
            score += (adjusted - baseline) * 50
            if 0 < games < 250:
                score -= 6 * (250 - games) / 250
            entry = {"id": int(item_id), "name": self.item_names[item_id],
                     "winRate": rate, "games": sample,
                     "basis": "augment_item" if use_joint else "single_item" if row else "formula",
                     "sampleLimited": sample < 250, "sortScore": score}
            # Upgrade-form observations stay separate from purchase-form
            # win rates; they only inform how common the route is.
            entry["popularityGames"] = max([games] + [
                int(number(rows.get(upgrade, {}).get("games")))
                for upgrade, base in SHOP_ROUTE_ITEM_IDS.items() if base == item_id])
            if item_id in preferred | required and (games >= 30 or item_id in required):
                synergy.append({**entry, "heroSpecific": item_id in hero_preferred,
                                "required": item_id in required,
                                "laterOnly": item_id in LATE_COMPLETE_ITEM_IDS and not warmog_passive_ready})
            # Use the currently visible item's flat HP as a conservative
            # estimate: augments and gained stacks cannot be read from item IDs.
            if item_id in LATE_COMPLETE_ITEM_IDS and not warmog_passive_ready:
                continue
            if (row and games >= 250) or item_id in core:
                candidates.append(entry)
        candidates.sort(key=lambda item: (item["sortScore"], item["games"]), reverse=True)
        candidates = self.purchase_policy.decorate(candidates, item_ids, items_known)
        all_candidates = list(candidates)
        eligible_item_ids = [row['id'] for row in all_candidates]
        candidates, purchase_order = self.item_order.apply(
            hero, candidates, purchased_routes, first_complete, items_known)
        # The popularity fallback must not erase a champion's core plan.
        if hero not in self.item_order.openings and formula.get('coreItems'):
            candidates = all_candidates
        candidates, purchase_order = self.purchase_policy.plan(candidates,
            [i['id'] for i in formula.get('coreItems', [])], purchased_routes,
            completed_count, items_known, purchase_order)
        purchase_order["routeNames"] = [self.item_names[str(i)] for i in purchase_order["route"]]
        synergy = self.purchase_policy.decorate(synergy, item_ids, items_known)
        synergy.sort(key=lambda item: (item["required"], item["heroSpecific"],
                                       not item["sampleLimited"], item["sortScore"], item["games"]),
                     reverse=True)
        main_items = candidates[:3]
        main_ids = {item["id"] for item in main_items}
        extra_items = [item for item in synergy if item["id"] not in main_ids][:3]
        public_fields = lambda item: {key: value for key, value in item.items()
                                      if key not in {"sortScore", "heroSpecific", "required", "popularityGames"}}
        return {"hero": hero, "owned": owned, "itemsKnown": bool(items_known),
                "recommendationStage": stage, "openingReason": opening_reason,
                "starterPlan": starter_plan,
                "currentItems": [{"id": int(item_id), "name": self.item_names.get(item_id, "装备 " + item_id)}
                                 for item_id in sorted(purchased)],
                "firstComplete": first_complete,
                "warmogPassiveReady": warmog_passive_ready,
                "purchaseOrder": purchase_order,
                "eligibleItemIds": eligible_item_ids,
                "planPreview": [{"id": i, "name": self.item_names[str(i)]} for i in purchase_order['route']
                                if str(i) not in purchased_routes and str(i) in self.direct_buyable_item_ids],
                "nextItems": [public_fields(item) for item in main_items],
                "synergyItems": [public_fields(item) for item in extra_items],
                "synergyTriggers": profile["triggers"],
                "dataBasis": "joint" if any(item["basis"] == "augment_item" for item in main_items)
                             else "single_item"}

    def rank_choices(self, hero, texts, confidences=None, source="manual", owned=None):
        champion = self.validate_hero(hero)
        owned = self.normalize_owned(owned)
        texts = [str(x or "") for x in list(texts)[:3]]
        texts += [""] * (3 - len(texts))
        matches = match_ocr_texts(texts, self.aliases)
        rows = {r["augment_name"]: r for r in self.runtime["hero_augment_stats"].get(hero, [])}
        choices = []
        for slot, (raw, match) in enumerate(zip(texts, matches), 1):
            confidence = number(confidences[slot - 1]) if confidences and len(confidences) >= slot else 1.0
            name = (raw if raw.startswith('未收录：') and 2 <= len(raw[4:]) <= 12 else
                    self.canonical_augment(raw) or match.get("matched_name"))
            row = rows.get(name)
            games = int(number(row.get("games"))) if row else 0
            choices.append({
                "slot": slot, "raw": raw, "name": name or "待识别", "matched": bool(name),
                "matchMode": match.get("mode"), "ocrConfidence": confidence,
                "rarity": self.augments.get(name, {}).get("rarity", ""),
                "hasStats": bool(row), "games": games,
                "winRate": number(row.get("win_rate")) if row else 0,
                "delta": number(row.get("win_rate")) - number(champion.get("win_rate")) if row else 0,
                "score": round(ranking_score(row, number(champion.get("win_rate"))), 2) if row else 0,
                "reliability": reliability(games) if row else "暂无英雄样本",
                "recommended": False,
                "conditional": self.joint.candidate(hero, owned, name) if owned and name and name not in owned else None,
                "trioClue": trio_clue(self.runtime["hero_augment_trios"].get(hero, []), owned, name)
                            if owned and name and name not in owned else None,
                "reason": "未识别到强化，请手动补全" if not name else
                          "这项已经记录为本局已选" if name in owned else
                          "此英雄暂无该强化统计" if not row else
                          "样本较少，仅供参考" if games < 30 else "结合本英雄胜率与样本量比较",
            })
        eligible = ([c for c in choices if c["conditional"] and c["conditional"]["reliable"]]
                    if owned else [c for c in choices if c["hasStats"] and c["games"] >= 30])
        if eligible:
            best = (max(eligible, key=lambda c: (c["conditional"]["wilsonLowerBound"], c["conditional"]["games"]))
                    if owned else max(eligible, key=lambda c: (c["score"], c["games"])))
            best["recommended"] = True
            best["reason"] = ("基于已选强化后的联合样本优先参考" if owned else
                              "当前已识别选项中优先参考" if len(eligible) > 1 else "当前唯一有足够样本的选项")
        return {"hero": hero, "choices": choices, "source": source,
                "time": time.strftime("%H:%M:%S"), "matchedCount": sum(c["matched"] for c in choices),
                "complete": all(c["matched"] for c in choices), "owned": owned,
                "conditionalStatus": ("not_needed" if not owned else "available" if self.joint.context(hero, owned)
                                      else "patch_mismatch" if self.joint.status == "patch_mismatch" else "unavailable")}

    def demo(self, hero, owned=None):
        champion = self.validate_hero(hero)
        owned = self.normalize_owned(owned)
        rows = sorted(self.runtime["hero_augment_stats"].get(hero, []),
                      key=lambda r: number(r.get("games")), reverse=True)
        names = [r["augment_name"] for r in rows if r["augment_name"] not in owned][:3]
        return self.rank_choices(hero, names, source="demo", owned=owned)

