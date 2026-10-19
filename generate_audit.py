"""Rebuild the patch-bound, 206-row augment-to-item audit from source snapshots."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def plain(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<br\s*/?>", "；", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"%i:[^%]+%", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"(?:；\s*){2,}", "；", value)
    return value


def create_audit():
    local = read(DATA / "augments.json")
    items = read(DATA / "item_names.json")
    config = read(ROOT / "synergy_rules.json")
    arena = read(ROOT / "sources" / "arena_16_15_zh_cn.json")["augments"]
    mayhem = read(ROOT / "sources" / "aram_mayhem_zh_cn.json")
    descriptions = {row["name"]: (row.get("desc"), "CommunityDragon Arena") for row in arena}
    descriptions.update({row["displayName"]: (row.get("description"), "海克斯大乱斗元数据（未标版本）")
                         for row in mayhem.values()})
    conditional = set(config["abilityConditional"])
    neutral = set(config["noDirectItemShift"])
    rules = config["rules"]
    rows = []
    for source_row in local:
        name = source_row["augment_name"]
        matched = [rule for rule in rules if name in rule["whenAny"]]
        status = ("明确装备机制" if matched else
                  "英雄／对局条件" if name in conditional else
                  "无明确装备转向" if name in neutral else None)
        if status is None or name not in descriptions:
            raise ValueError("未核对效果或分类：" + name)
        preferred = list(dict.fromkeys(str(item_id) for rule in matched
                                       for key in ("preferredItemIds", "apPreferredItemIds", "otherPreferredItemIds")
                                       for item_id in rule.get(key, [])))
        required = list(dict.fromkeys(str(item_id) for rule in matched
                                      for item_id in rule.get("requiredItemIds", [])))
        if any(item_id not in items for item_id in preferred + required):
            raise ValueError("规则包含未知装备：" + name)
        desc, source = descriptions[name]
        rows.append({"name": name, "rarity": source_row["rarity"], "classification": status,
                     "effect": plain(desc), "effectSource": source,
                     "mechanisms": [rule["name"] + ("（赏金猎人）" if rule.get("hero") != "*" else "")
                                    for rule in matched],
                     "requiredItemIds": required, "candidateItemIds": preferred})
    if len(rows) != 206 or len({row["name"] for row in rows}) != len(rows):
        raise ValueError("海克斯枚举数量或名称异常。")
    output = {"patch": config["patch"], "sourceUrls": config["effectSources"], "augments": rows}
    (ROOT / "augment_item_audit.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 16.15 海克斯与出装联动逐项审查", "",
             "本地 206 个海克斯逐名匹配游戏效果数据。‘明确装备机制’列的是任务指定或待英雄验证的候选，不是海克斯＋装备联合胜率。",
             "‘英雄／对局条件’表示效果可能改变出装，但仅凭名称和全局规则无法可靠指定装备；程序会展示条件提示并保留常规出装。",
             "‘无明确装备转向’表示未发现足以改变装备方向的直接机制，经济和随机奖励仍可能改变购买时机。", "",
             "Arena 效果取自 16.15 提取数据；海克斯大乱斗专属效果源未标版本，已逐名匹配本地 16.15 名单，但数值和效果若有改动仍须以实际游戏为准。", "",
             "效果来源：[16.15 Arena 客户端提取数据](https://raw.communitydragon.org/16.15/cdragon/arena/zh_cn.json)、[海克斯大乱斗中文元数据](https://hextech.dtodo.cn/data/aram-mayhem-augments.zh_cn.json)。", "",
             "| # | 海克斯 | 分类 | 机制 | 指定／候选装备 | 效果依据 |", "| ---: | --- | --- | --- | --- | --- |"]
    for index, row in enumerate(rows, 1):
        names = (["指定：" + "、".join(items[item_id] for item_id in row["requiredItemIds"])]
                 if row["requiredItemIds"] else [])
        if row["candidateItemIds"]:
            names.append("候选：" + "、".join(items[item_id] for item_id in row["candidateItemIds"]))
        cells = [str(index), row["name"], row["classification"], "、".join(row["mechanisms"]) or "—",
                 "；".join(names) or "—", row["effect"]]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    (ROOT / "augment_item_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


if __name__ == "__main__":
    rows = create_audit()
    print("Audited", len(rows), "augments")
