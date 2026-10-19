"""Pure shared catalog validation, text normalization and numeric helpers."""
import math
import re

def normalize_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    replacements = {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "！": "!",
        "？": "?",
        "“": "\"",
        "”": "\"",
        "‘": "'",
        "’": "'",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "＋": "+",
        "—": "-",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"\s+", "", normalized)
    for token in ["·", "-", "!", "'", "\"", "+"]:
        normalized = normalized.replace(token, "")
    return normalized

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


class CatalogValidation:
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

    def validate_hero(self, hero):
        if hero not in self.heroes:
            raise ValueError("请先从左侧列表选择英雄。")
        return self.heroes[hero]
