"""A bounded second visual reading of symbols that can change a quiz answer."""
import base64
import io
from itertools import groupby
import re
from collections import namedtuple


# The second pass is optional: an ordinary high-resolution text question should
# not spawn a second OCR process. Confidence values only decide what to re-read.
_MAX_TOKENS = 4
_MAX_WARNINGS = 3
_Candidate = namedtuple("_Candidate", "field text crop")
_NUMBER = re.compile(r"[+−–—-]?(?:[0-9]{1,10}(?:[.,][0-9]{1,8})?|[.,][0-9]{1,8})[%‰]?")
_OPERATOR = re.compile(r"(?:<=|>=|[<>=≤≥≠+−–—%-])")
_TERM = re.compile(r"([a-zA-ZхХ])(?:<=|>=|[<>=≤≥])([+−-]?\d{1,8}(?:[.,]\d{1,6})?)")
_JOINED = re.compile(r"[a-zA-ZхХ]\d{2,8}")
_NEGATIONS = {"не", "ни", "нет", "not", "no", "never", "без", "кроме", "except"}
_NEGATION_NEIGHBORS = {"но", "на", "пе", "he", "ne", "nor", "lot"}


def field_for_box(box, regions):
    """Return the one field containing the center of the normalized word box."""
    cx = box["x"] + box["w"] / 2
    cy = box["y"] + box["h"] / 2
    matches = [i for i, r in enumerate(regions)
               if r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"]]
    return matches[0] if len(matches) == 1 else None


def _kind(text):
    if _TERM.fullmatch(text) or _JOINED.fullmatch(text):
        return "formula"
    if _NUMBER.fullmatch(text):
        return "number"
    if _OPERATOR.fullmatch(text):
        return "operator"
    if text.casefold() in _NEGATIONS | _NEGATION_NEIGHBORS:
        return "word"
    return None


def _key(text):
    return text.casefold().replace(",", ".").translate(str.maketrans({"−": "-", "–": "-", "—": "-", "≤": "<=", "≥": ">=", "х": "x"}))


def _difference(primary, alternate):
    kind = _kind(primary)
    if not kind or _kind(alternate) != kind or _key(primary) == _key(alternate):
        return False
    if kind == "word":
        return (primary.casefold() in _NEGATIONS) != (alternate.casefold() in _NEGATIONS)
    return True


def _normalized_box(part, page):
    return {"x": part["x"] / page.width, "y": part["y"] / page.height,
            "w": part["w"] / page.width, "h": part["h"] / page.height}


def _contains(text, token):
    # Layout has already removed counters/timers/option badges. Never report a
    # glyph that only existed in that discarded UI.
    return re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text, re.I) is not None


def _crop(original, page, part, siblings):
    # Keep a little real ink to either side: a lost sign can sit just outside the
    # primary OCR box. Clip the margin before the next word on the same line.
    x, y, w, h = (part[k] for k in ("x", "y", "w", "h"))
    left, right = max(0, x - .5 * h), min(page.width, x + w + .4 * h)
    for other in siblings:
        if other is part or not (other["y"] <= y + h * .5 <= other["y"] + other["h"]):
            continue
        if other["x"] + other["w"] <= x:
            left = max(left, other["x"] + other["w"])
        elif other["x"] >= x + w:
            right = min(right, other["x"])
    sx, sy = original.width / page.width, original.height / page.height
    padding = min(8, .45 * w) if part["text"].strip() == "=" else .12 * h
    top, bottom = max(0, y - padding), min(page.height, y + h + padding)
    xmin, ymin = int(left * sx), int(top * sy)
    xmax = min(original.width, max(xmin + 1, round(right * sx)))
    ymax = min(original.height, max(ymin + 1, round(bottom * sy)))
    return original.crop((xmin, ymin, xmax, ymax))


def _stroke_runs(mask):
    runs = []
    for active, positions in groupby(enumerate(mask), key=lambda pair: bool(pair[1])):
        if active:
            indices = [coordinate for coordinate, _ in positions]
            runs.append((indices[0], indices[-1] + 1))
    return runs


def _visible_equals(crop):
    """Confirm exactly two aligned horizontal strokes at two contrasts on the original pixels."""
    w, h = crop.size
    if not 5 <= w <= 192 or not 3 <= h <= 96:
        return False
    grey = crop.convert("L")
    low, high = grey.getextrema()
    if high - low < 48:
        return False
    # The crop can be tight above and below the sign. Its side margins
    # normally expose the background even in dark mode.
    border = sorted(grey.getpixel((x, y)) for y in range(h) for x in (0, w-1))
    light_background = border[len(border)//2] >= (low+high)/2
    for fraction in (.40, .66):
        threshold = (high - fraction * (high-low) if light_background else low + fraction * (high-low))
        ink = grey.point(lambda level: 255 if (level < threshold if light_background else level > threshold) else 0)
        bands = _stroke_runs(ink.getprojection()[1])
        if len(bands) != 2:
            return False
        left, _, right, _ = ink.getbbox()
        width = right - left
        thick = [end-start for start, end in bands]
        if (width < 5 or max(thick) > .38 * width or min(thick) * 2 < max(thick)
                or bands[1][0] - bands[0][1] > .8 * width):
            return False
        # Disjoint fragments (a colon pair, text, a diagonal slash) cannot
        # qualify by their outer bounds alone.
        for start, end in bands:
            for y in range(start, end):
                segment = _stroke_runs([ink.getpixel((x, y)) for x in range(left, right)])
                if len(segment) != 1 or width - (segment[0][1] - segment[0][0]) > max(2, .18 * width):
                    return False
    return True


def _comparison_glyph(crop):
    """Read a separated mathematical sign by its strokes, independent of OCR language."""
    from PIL import ImageOps
    grey = ImageOps.autocontrast(crop.convert("L"))
    if sum(grey.histogram()[:128]) > grey.width * grey.height / 2:
        grey = ImageOps.invert(grey)
    ink = grey.point(lambda level: 255 if level < 125 else 0)
    columns = _stroke_runs(ink.getprojection()[0])
    if len(columns) < 3:
        return None
    # This path accepts only expressions with a one-letter variable, a
    # visibly separated sign, and an operand (checked against the other read).
    left, right = columns[1]
    if min(left - columns[0][1], columns[2][0] - right) < 2:
        return None
    sign = ink.crop((left, 0, right, ink.height))
    sign = sign.crop(sign.getbbox())
    width, height = sign.size
    if width < 6 or not .65 <= width / height <= 4:
        return None
    scan = [[x for x in range(width) if sign.getpixel((x, y))] for y in range(height)]
    bands = _stroke_runs([bool(xs) for xs in scan])
    if len(bands) not in (1, 2):
        return None
    a, b = bands[0]
    underlined = len(bands) == 2
    if underlined:
        c, d = bands[1]
        if d - c > .35 * height or any(len(xs) < .76 * width for xs in scan[c:d]):
            return None
        if b - a <= .35 * height and all(len(xs) >= .76 * width for xs in scan[a:b]):
            return "="
    arc = scan[a:b]
    if len(arc) < 5 or any(len(xs) > .8 * width or xs[-1] - xs[0] + 1 > len(xs) + 1 for xs in arc):
        return None
    centers = [sum(xs) / len(xs) for xs in arc]
    middle = len(centers) // 2
    ends = (centers[0] + centers[-1]) / 2
    diff = centers[middle] - ends
    if abs(centers[0] - centers[-1]) > .23 * width or abs(diff) < .46 * width:
        return None
    direction = 1 if diff > 0 else -1
    # A '2', arrowhead or other shape must not pass as a comparison sign.
    steps = [(later - earlier) * direction for earlier, later in zip(centers, centers[1:])]
    if min(steps[:middle]) < -.12 * width or max(steps[middle:]) > .12 * width:
        return None
    if diff > 0:
        return "≥" if underlined else ">"
    return "≤" if underlined else "<"


def _views(crop):
    from PIL import Image, ImageOps
    grey = crop.convert("L")
    if sum(grey.histogram()[:128]) > grey.width * grey.height / 2:
        grey = ImageOps.invert(grey)
    grey = ImageOps.autocontrast(grey)
    height = 54
    width = max(1, min(540, round(grey.width * height / grey.height)))
    resampling = getattr(Image, "Resampling", Image)
    smooth = grey.resize((width, height), resampling.LANCZOS)
    sharp = grey.resize((width, height), resampling.NEAREST)
    return [ImageOps.expand(smooth, 12, fill=255), ImageOps.expand(sharp, 12, fill=255)]


def _preview(crop):
    # Shown only for the active question and never written to disk by QuizLens.
    crop = crop.copy()
    crop.thumbnail((280, 80))
    output = io.BytesIO()
    crop.save(output, "PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def field_warnings(pages, originals, groups, texts, regions, read):
    """Compare two visual preparations; disagreeing supported readings abstain."""
    if not texts:
        return []
    candidates = []
    for page_index, (page, original, parts) in enumerate(zip(pages, originals, groups)):
        for part in parts:
            text = part["text"].strip()
            kind = _kind(text)
            if not kind or not 3 <= part["h"] <= 100 or not 2 <= part["w"] <= 480:
                continue
            confidence = part.get("confidence", 100)
            # Small decimals, percentages and operators can score highly even
            # when a dot or extra stroke was lost. Avoid re-reading plain prose.
            glyph_height = part["h"] * original.height / page.height
            delicate = kind == "operator" or any(ch in text for ch in "%‰,.-−–")
            if confidence >= 82 and not (delicate and glyph_height <= 17):
                continue
            field = field_for_box(_normalized_box(part, page), regions) if regions is not None else page_index
            if field is None or field >= len(texts) or not _contains(texts[field], text):
                continue
            candidates.append((confidence, 0 if delicate else 1, field, text, page_index, part))
    # Limit the amount of enlargement and keep a single OCR subprocess per frame.
    candidates.sort(key=lambda c: c[:4])
    reads = [_Candidate(field, text, _crop(originals[i], pages[i], part, groups[i]))
             for _, _, field, text, i, part in candidates[:_MAX_TOKENS]]
    # Isolated word OCR often calls an equals sign a dash even in two
    # enlarged views. Its two visible strokes corroborate the primary read.
    reads = [item for item in reads if item.text != "=" or not _visible_equals(item.crop)]
    if not reads:
        return []
    try:
        reread = read([view for item in reads for view in _views(item.crop)])
    except (RuntimeError, OSError, ValueError):
        return []
    warnings = []
    for i, item in enumerate(reads):
        readings = []
        sign = _comparison_glyph(item.crop) if _kind(item.text) == "formula" else None
        for words in reread[i*2:i*2+2]:
            if not words or min(w.get("confidence", 0) for w in words) < 50:
                continue
            alternative = "".join(w["text"] for w in words)
            relation = _TERM.fullmatch(alternative) if sign else None
            if relation:
                alternative = relation[1] + sign + relation[2]
            if not _difference(item.text, alternative):
                continue
            # Do not flag a detached '%' that already belongs to this value.
            if alternative.endswith(("%", "‰")) and _key(alternative[:-1]) == _key(item.text) and re.search(
                    re.escape(item.text) + r"\s*" + re.escape(alternative[-1]), texts[item.field]):
                continue
            readings.append(alternative)
        if len(readings) == 2 and _key(readings[0]) == _key(readings[1]):
            warnings.append({"field": item.field, "readings": [item.text, readings[0]], "image": _preview(item.crop)})
            if len(warnings) >= _MAX_WARNINGS:
                break
    return warnings
