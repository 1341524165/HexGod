"""Produce the full, price-checked hero opening list from the live catalog."""
from pathlib import Path

from backend import Backend


def main():
    backend = Backend(use_remote=False)
    lines = ["# 1400 金出门装逐英雄清单", "",
             "版本：16.15；共 173 位英雄。价格与嚎哭深渊商店可用性由同版本 Riot Data Dragon 校验。",
             "这些是独立的出门方案，不从第一件成装反推，也没有整套出门装的胜率或样本量。",
             "基础来源为原数据的 starterItems 字段；13 位腰带开局的前排改为号角组合，芮尔改为号角＋眼泪。",
             "无蓝条的腕豪、慎、扎克、蒙多使用号角＋红水晶；其他原有方案保留，按预算补充药水。",
             "费德提克统一为统计表中的远古恐惧；原方案表缺少 Locke，暂定法球＋增幅典籍＋药水，明确标为待实战核对。",
             "药水不足 150 金时补生命药水；足够时使用复用型药水，不混购两种药水。",
             "这些预算与可用性检查不代表每位英雄的方案都经过实战胜率验证。", "",
             "| 英雄 | 整套出门装 | 合计 | 余金 | 选择依据 |",
             "| --- | --- | ---: | ---: | --- |"]
    for hero in backend.heroes:
        plan = backend.starters.plan(hero)
        names = " ＋ ".join(item["name"] + (" ×" + str(item["quantity"]) if item["quantity"] > 1 else "")
                            for item in plan["items"])
        lines.append(f"| {hero} | {names} | {plan['totalCost']} | {plan['remainingGold']} | {plan['reason']} |")
    lines.extend(["", "## 自动显示", "",
                  "浮窗优先显示三选一；否则在游戏前 90 秒、库存已同步且尚未结束出门阶段时显示出门装。",
                  "买齐整套（含药水）、首次死亡、已有成装、剩余金币不足以补齐推荐且已经买了其他装备，或达到 90 秒，都会切回下一件装备。",
                  "章节等出门装若分小件购买，会扣除已买合成组件的价值，不会误判为买了另一套。",
                  "阶段结束后同一局不再重开；短暂断连不会清除此状态，新局自动重置。接口不可用时不猜测开局，主窗口仍可查看出门方案。",
                  "商店开关与离开泉水没有直接可靠的接口字段，90 秒是保守的阶段上限，不等同于精确的出泉水检测。", "",
                  "数据： [Riot 装备资料](https://ddragon.leagueoflegends.com/cdn/16.15.1/data/zh_CN/item.json)；",
                  "接口字段： [Riot Live Client Data API](https://developer.riotgames.com/docs/lol#game-client-api_live-client-data-api)。", ""])
    output = Path(__file__).with_name("starter_build_audit.md")
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Validated {len(backend.heroes)} starter bundles; wrote {output.name}")


if __name__ == "__main__":
    main()
