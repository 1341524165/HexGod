"""Conservative full-title correction. Never search a description for substrings."""
from hexgod.vision.pipeline import normalize_text


def corrected_title(raw, names):
    text=normalize_text(raw)
    # Repeated glyph confusion in the title crop; keep this a full-title
    # alias rather than raising fuzzy tolerance for every augment.
    aliases = {'喂鸣喂鸣': '喂呜喂呜', '喂嗚喂嗚': '喂呜喂呜'}
    if text in aliases and aliases[text] in names:
        return aliases[text]
    if not 4 <= len(text) <= 12:
        return None
    def distance(a,b):
        row=list(range(len(b)+1))
        for i,x in enumerate(a,1):
            following=[i]
            for j,y in enumerate(b,1):
                following.append(min(following[-1]+1,row[j]+1,row[j-1]+(x!=y)))
            row=following
        return row[-1]
    matches=[name for name in names if abs(len(normalize_text(name))-len(text)) <= 1
             and distance(text,normalize_text(name)) <= 1]
    return matches[0] if len(matches)==1 else None
