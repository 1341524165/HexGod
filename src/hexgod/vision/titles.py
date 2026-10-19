"""Locate visible augment cards before reading their title bands."""
from pathlib import Path
import itertools
import numpy as np
import cv2
from PIL import Image
from hexgod.vision.pipeline import is_title_like_text


def legacy_card_layout(pixels):
    """Strong six-edge evidence for the reference client layout.

    Used only when adaptive contour grouping cannot see all three cards.
    This preserves usable recognition for animated/card-outline variants.
    """
    h, w = pixels.shape[:2]
    card_width, gap = w*.168, w*.0175
    left = (w-3*card_width-2*gap)/2
    dark = 0
    for slot in range(3):
        x = int(left+slot*(card_width+gap)+card_width*.02)
        band = pixels[int(h*.35):int(h*.445), x:int(x+card_width*.96)]
        dark += bool(band.size and np.mean(np.max(band, axis=2) < 135) > .6)
    edges = 0
    for x in [left+slot*(card_width+gap)+edge*card_width
              for slot in range(3) for edge in (0, 1)]:
        center = int(x)
        strip = pixels[int(h*.22):int(h*.61),
                       max(0, center-int(w*.009)):min(w, center+int(w*.009)+1)]
        bright = (strip[:, :, 0] > 150) & (strip[:, :, 1] > 120) & (strip[:, :, 2] > 90)
        if bright.size and np.max(np.mean(bright, axis=0)) > .60:
            edges += 1
    if edges < 4 or dark < 2:
        return None
    return [(round(left+slot*(card_width+gap)), round(h*.17),
             round(left+slot*(card_width+gap)+card_width), round(h*.655))
            for slot in range(3)]


def detect_card_layout(image):
    pixels = np.asarray(image.convert('RGB'))
    height, width = pixels.shape[:2]
    reduction = max(1.0, width / 1920, height / 1200)
    small = (cv2.resize(pixels, (round(width/reduction), round(height/reduction)),
                        interpolation=cv2.INTER_AREA) if reduction > 1 else pixels)
    r, g, b = (small[:, :, channel].astype(np.int16) for channel in range(3))
    warm = (r > 130) & (g > 100) & (b > 65) & (r > b * 1.15)
    pale = (r > 165) & (g > 160) & (b > 165)
    cool = (b > 155) & (g > 115) & (r > 85) & (b > r * 1.15)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 19))
    base_border = cv2.morphologyEx((warm | pale).astype(np.uint8) * 255,
                                   cv2.MORPH_CLOSE, kernel)
    border = cv2.morphologyEx((warm | pale | cool).astype(np.uint8) * 255,
                              cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(border, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sh, sw = small.shape[:2]
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (.045 * sw <= w <= .32 * sw and .23 * sh <= h <= .82 * sh
                and 1.28 <= h/w <= 2.25):
            continue
        if cv2.contourArea(contour) / (w*h) < .30:
            continue
        body = small[y+round(h*.65):y+round(h*.85), x+round(w*.25):x+round(w*.75)]
        if not body.size or np.mean(np.max(body, axis=2) < 165) < .52:
            continue
        candidates.append((x, y, w, h))
    best = None
    for group in itertools.combinations(sorted(candidates), 3):
        sizes = [row[2] for row in group]
        heights = [row[3] for row in group]
        centers = [row[0] + row[2]/2 for row in group]
        steps = [centers[1]-centers[0], centers[2]-centers[1]]
        median_w, median_h = float(np.median(sizes)), float(np.median(heights))
        if (max(sizes)/min(sizes) > 1.25 or max(heights)/min(heights) > 1.25
                or max(steps)/min(steps) > 1.23
                or not all(1.04*median_w < step < 1.65*median_w for step in steps)
                or max(row[1] for row in group)-min(row[1] for row in group) > .11*median_h):
            continue
        score = (sum(abs(s-median_w) for s in sizes)/median_w
                 + sum(abs(s-median_h) for s in heights)/median_h
                 + abs(steps[0]-steps[1])/median_w
                 + (max(row[1] for row in group)-min(row[1] for row in group))/median_h)
        if best is None or score < best[0]:
            best = (score, group)
    if best is None:
        legacy = legacy_card_layout(pixels)
        if legacy:
            return legacy
        # Tooltips may hide two cards and merge their outlines into one
        # contour. Preserve an isolated, visible first card without claiming
        # the obscured titles were read.
        fragments = []
        partial_contours, _ = cv2.findContours(base_border, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in partial_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (.17*sw <= x <= .30*sw and .10*sh <= y <= .25*sh
                    and .11*sw <= w <= .20*sw and .24*sh <= h <= .58*sh
                    and .85 <= h/w <= 1.30):
                fragments.append((x, y, w, h))
        if not fragments:
            return None
        x, y, w, h = max(fragments, key=lambda row: row[2]*row[3])
        inferred_h = round(w * 1.64)
        return [(round(x*reduction), round(y*reduction),
                 round((x+w)*reduction), round((y+inferred_h)*reduction)), None, None]
    group = best[1]
    # Glow and animated highlights can extend far above an otherwise intact
    # card. The lower border and card width are much more stable in real games.
    card_width = float(np.median([row[2] for row in group]))
    card_bottom = float(np.median([row[1]+row[3] for row in group]))
    observed_ratio = float(np.median([row[3]/row[2] for row in group]))
    card_ratio = 1.64 if observed_ratio > 1.85 else max(1.5, observed_ratio)
    card_top = card_bottom - card_width * card_ratio
    return [(round((x+w/2-card_width/2)*reduction), round(card_top*reduction),
             round((x+w/2+card_width/2)*reduction), round(card_bottom*reduction))
            for x, y, w, h in group]


def title_regions(image, include_layout=False):
    cards = detect_card_layout(image)
    rects, fingerprints = [], []
    if cards:
        for card in cards:
            if card is None:
                rects.append(None); fingerprints.append(None)
                continue
            x1, y1, x2, y2 = card
            w, h = x2-x1, y2-y1
            rect = (round(x1+w*.055), round(y1+h*.39),
                    round(x2-w*.055), round(y1+h*.56))
            rects.append(rect)
            tiny = image.crop(rect).convert('L').resize((32, 12))
            values = np.asarray(tiny)
            fingerprints.append(np.packbits(values >= values.mean()).tobytes().hex())
    else:
        rects, fingerprints = [None]*3, [None]*3
    result = (rects, fingerprints, bool(cards))
    return (*result, cards or []) if include_layout else result


def read_titles(source, directory, runner):
    with Image.open(source) as image:
        image = image.convert('RGB')
        rects, hashes, cards, card_rects = title_regions(image, include_layout=True)
        if not cards:
            return {'slot_candidates': [[], [], []], 'titleFingerprints': hashes,
                    'titleRegionCards': False, 'titleRegionRects': rects,
                    'titleCardRects': [], 'imageSize': list(image.size)}
        paths, slots = [], []
        for slot, rect in enumerate(rects):
            if rect is None:
                continue
            target = Path(directory) / ('title-' + str(slot) + '.png')
            crop = image.crop(rect)
            factor = max(1, min(3, 900 / max(1, crop.width)))
            crop.resize((int(crop.width*factor), int(crop.height*factor)), Image.Resampling.LANCZOS).save(target)
            paths.append(str(target)); slots.append(slot)
        size = list(image.size)
    candidates = [[] for _ in range(3)]
    for slot, payload in zip(slots, runner(paths) if paths else []):
        for line in payload.get('lines') or []:
            text, score = line.get('text', ''), line.get('score', 0)
            if is_title_like_text(text) and text.strip() not in {'伤害', '防御', '通用', '攻击', '技能', '治疗', '辅助'}:
                candidates[slot].append({'text': text, 'confidence': score * 100,
                    'title_like': True, 'image_quality': 100, 'variant_index': 0,
                    'title_region': True})
    if not any(candidates):
        cards = False
    return {'slot_candidates': candidates, 'titleFingerprints': hashes,
            'titleRegionCards': cards, 'titleRegionRects': rects,
            'titleCardRects': card_rects, 'imageSize': size}
