"""Audit every item ID reachable from the local build data against Riot's 16.15 shop data."""
from __future__ import annotations

import argparse
import html
from pathlib import Path
import re

from backend import Backend, ITEM_CATALOG_PATH, LATE_COMPLETE_ITEM_IDS, SHOP_ROUTE_ITEM_IDS


ROOT = Path(__file__).resolve().parent
AUDIT_PATH = ROOT / "item_purchase_audit.md"
SCALING_NOTES = {
    "3003": "可购买；女神之泪路线，叠满后转为炽天使之拥。",
    "3004": "可购买；女神之泪路线，叠满后转为魔切。",
    "3031": "自带暴击率，暴击收益首件即可生效。",
    "3084": "可购买；生命值成长需要之后与英雄交战，早做有叠层时间。",
    "3089": "自带法强，百分比法强加成首件即可生效。",
    "3119": "可购买；女神之泪路线，叠满后转为末日寒冬。",
    "3302": "攻击即可启动攻防特效，额外装备不是硬性前置。",
    "4633": "自带生命值，额外生命值转法强首件即可生效。",
    "6621": "自带法力回复，治疗护盾与法强转换首件即可生效。",
    "6657": "可购买；按时间成长，早做有叠层时间。",
    "6665": "自带双抗，进入战斗后即可触发双抗增幅。",
}


def status(item):
    if not item:
        return "官方清单缺失"
    if item.get("gold", {}).get("purchasable") is not True:
        return "不能直接购买"
    if item.get("inStore") is False:
        return "商店不显示"
    if item.get("maps", {}).get("12") is not True:
        return "嚎哭深渊不可用"
    if "Boots" in item.get("tags", []):
        return "鞋类；不计成装"
    return "可直接购买"


def describe(item):
    raw = (item or {}).get("description", "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", raw))).strip()


def audit_rows(backend):
    observed = {str(row["item_id"]) for rows in backend.runtime["hero_item_builds"].values()
                for row in rows}
    for item_id in sorted(backend.completed_item_ids | observed, key=int):
        item = backend.item_catalog.get(item_id)
        shop = status(item)
        candidate = item_id in backend.completed_item_ids
        route = SHOP_ROUTE_ITEM_IDS.get(item_id, item_id)
        if item_id in LATE_COMPLETE_ITEM_IDS:
            note = "首件成装禁推；之后也要核对现有物品生命值，确认狂徒被动可达到 2000 额外生命值。"
        elif candidate and route != item_id:
            note = f"购买路线改用 {route} {backend.item_names[route]}；原 ID 不直接推荐购买。"
        elif shop not in ("可直接购买", "鞋类；不计成装"):
            note = "不作为下一件直接购买建议。"
        elif item_id in SCALING_NOTES:
            note = SCALING_NOTES[item_id]
        elif shop == "鞋类；不计成装":
            note = "可以购买，但不能把狂徒的首件限制视为已解除。"
        elif not candidate:
            note = "仅在统计数据中；当前未列为下一件候选，不据此判断购买时机。"
        else:
            note = "无需要已有成装才能触发的统一硬性前置；仍需英雄和对局判断。"
        yield {
            "id": item_id,
            "name": backend.item_names.get(item_id) or (item or {}).get("name", "未知"),
            "candidate": candidate,
            "route": route,
            "observed": item_id in observed,
            "shop": shop,
            "note": note,
            "description": describe(item),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptions", action="store_true", help="Print source descriptions for manual review")
    args = parser.parse_args()
    backend = Backend(use_remote=False)
    rows = list(audit_rows(backend))
    if args.descriptions:
        for row in rows:
            print(f"{row['id']} {row['name']}: {row['description']}")
        return
    candidate_rows = [row for row in rows if row["candidate"]]
    direct_count = sum(row["shop"] in ("可直接购买", "鞋类；不计成装")
                       for row in candidate_rows)
    unpurchasable_count = sum(row["shop"] == "不能直接购买" for row in candidate_rows)
    hidden_count = sum(row["shop"] == "商店不显示" for row in candidate_rows)
    wrong_map_count = sum(row["shop"] == "嚎哭深渊不可用" for row in candidate_rows)
    stats_only_count = sum(not row["candidate"] for row in rows)
    route_count = sum(row["candidate"] and row["route"] != row["id"] for row in rows)
    effective_count = len(backend.direct_buyable_item_ids)
    lines = [
        "# 装备购买顺序与商店可用性核对（16.15）", "",
        f"范围：本地英雄装备统计或下一件候选池出现的全部 **{len(rows)} 个装备 ID**。",
        f"下一件候选池有 **{len(candidate_rows)} 个 ID**；其中 **{direct_count} 个可直接购买**，"
        f"另有 **{unpurchasable_count} 个不能直接购买**、**{wrong_map_count} 个嚎哭深渊不可用**"
        + (f"、**{hidden_count} 个商店不显示**" if hidden_count else "") + "。",
        f"其余 **{stats_only_count} 个**仅出现在统计数据中，不进入当前下一件候选池。", "",
        f"处理：**{route_count} 个旧 ID 改指向可买的基础装备或地图版本**；去重后剩余"
        f" **{effective_count} 个有效购买候选**。歌之权冠没有确认可买路线，已从直接购买建议中排除。", "",
        "来源：[Riot 16.15.1 中文装备资料](https://ddragon.leagueoflegends.com/cdn/16.15.1/data/zh_CN/item.json)"
        f"（本地快照：`{ITEM_CATALOG_PATH.relative_to(ROOT)}`）；"
        "[Riot 地图表](https://static.developer.riotgames.com/docs/lol/maps.json) 将 12 标为嚎哭深渊。", "",
        "狂徒铠甲是当前可买候选中确认需要已有大量额外生命值的项目。程序不把它列为首件成装；",
        "之后根据已同步物品的基础生命值和狂徒自身的 12% 装备生命值增幅保守估算，未到 2000 额外生命值时仍不列入主推荐。",
        "该估算不包含海克斯增益或心之钢等后续叠层，因此可能延后显示，不会据此声称实战属性的精确值。",
        "大天使之杖、魔宗、凛冬之临可以购买并自行叠层；它们的进化形态不能直接购买。",
        "无尽之刃、灭世者的死亡之帽、界弓、千变者贾修、裂隙制造者和黎明核心等首件时核心效果也能触发，",
        "故只考虑统计排序与英雄适配，不设一刀切首件禁令。", "",
        "| ID | 装备 | 数据位置 | 商店状态 | 首件或后续判断 |", "|---:|---|---|---|---|",
    ]
    for row in rows:
        origin = ("下一件候选" if row["candidate"] else "—") + ("、统计" if row["observed"] else "")
        lines.append(f"| {row['id']} | {row['name']} | {origin} | {row['shop']} | {row['note']} |")
    AUDIT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {AUDIT_PATH} ({len(rows)} items)")


if __name__ == "__main__":
    main()
