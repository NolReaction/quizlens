"""Reconstruct quiz text from ordered OCR fragments with top-left bounding boxes."""
import re
from statistics import median

# A bare time can be part of the question. It needs a counter on the same
# line or a distinct right-hand clock above/below the actual question.
_TIME = r"(?:[0-9]{1,2}:[0-5][0-9]|[0-9]{1,3}(?:[.,][0-9])?\s*(?:с|s|c|сек(?:унд[ауы]?)?\.?|seconds?|secs?))"
_COUNTER = r"(?:вопрос|question)\s+[0-9]+\s*(?:из|of|/)\s*(?:[0-9]+|[бзoо])"
_LABELED_TIME = rf"(?:осталось(?:\s+времени)?|таймер|time\s+(?:left|remaining)|timer|remaining)\s*[:–-]?\s*(?:{_TIME}|[0-9]{{1,3}})"
_CHROME = re.compile(rf"(?:{_COUNTER}(?:\s+(?:{_LABELED_TIME}|{_TIME}))?|{_LABELED_TIME})", re.I)
_CLOCK = re.compile(_TIME, re.I)
_CIRCLED = "①②③④⑤⑥⑦⑧"
_BADGE = re.compile(r"^(\([1-8]\)|\[[1-8]\]|[1-8][.)]|[①②③④⑤⑥⑦⑧]|[1-8])(?:\s+(.+))?$", re.S)
_INFIX = re.compile(r"^[+×÷*/=<>%,.^:−–-]")
_UNIT = re.compile(
    r"(?:год(?:а|ы|ов)?|лет|месяц(?:а|ев|ы)?|недел(?:я|и|ь)|день|дн[яи]|дней|сут(?:ки|ок)|"
    r"час(?:а|ов|ы)?|минут[аыу]?|секунд[аыу]?|мин|сек|мс|мкс|[сч]|"
    r"(?:[кмсд]?м|к?г|м?л|к?м|гц|[кмгт]?б(?:айт(?:а|ов)?)?|бит(?:а|ов)?|процент(?:а|ов)?)|"
    r"years?|months?|weeks?|days?|hours?|minutes?|seconds?|secs?|mins?|ms|[sh]|"
    r"(?:[kmc]?m|[km]?g|m?l|hz|[kmgt]?b|bytes?|bits?|percent))\.?", re.I)
_FORMULA_TAIL = re.compile(r"(?:\b(?:и|или|либо|and|or)\b|[+*/=<>^×÷−]|[A-Za-z]\s*\()", re.I)


def _join(parts):
    return " ".join(" ".join(p["text"] for p in parts).split())


def _without_timer(parts):
    if len(parts) < 2:
        return parts
    # Vision supplies line or word fragments; Tesseract supplies words.
    rows = []
    for part in parts:
        if rows:
            first = rows[-1][0]
            centers = abs((part["y"] + part["h"] / 2) - (first["y"] + first["h"] / 2))
            if centers <= .48 * min(first["h"], part["h"]):
                rows[-1].append(part)
                continue
        rows.append([part])
    if len(rows) < 2:
        return parts
    first, last = 0, len(rows) - 1
    while first <= last and _CHROME.fullmatch(_join(rows[first])):
        first += 1
    while first <= last and _CHROME.fullmatch(_join(rows[last])):
        last -= 1
    if first > last:
        return parts

    for edge in (first, last):
        if first >= last or not _CLOCK.fullmatch(_join(rows[edge])):
            continue
        body = [p for row in rows[first:last + 1] if row is not rows[edge] for p in row]
        if len(_join(body).split()) < 3 or "?" not in _join(body):
            continue
        left = min(p["x"] for p in body)
        right = max(p["x"] + p["w"] for p in body)
        clock = rows[edge]
        clock_left = min(p["x"] for p in clock)
        clock_width = max(p["x"] + p["w"] for p in clock) - clock_left
        body_height = max(p["h"] for p in body)
        if (clock_left >= left + .68 * (right - left)
                and clock_width <= .30 * (right - left)
                and max(p["h"] for p in clock) <= .9 * body_height):
            if edge == first:
                first += 1
            else:
                last -= 1
    return [part for row in rows[first:last + 1] for part in row]


def _badge_candidate(parts, number):
    if not parts:
        return None
    # On a wrapping answer the badge can fall below line 1 in reading order.
    leftmost = min(range(len(parts)), key=lambda index: parts[index]["x"])
    part = parts[leftmost]
    match = _BADGE.fullmatch(part["text"].strip())
    if match is None:
        return None
    marker, following = match.groups()
    if marker not in (str(number), f"{number}.", f"{number})", f"({number})", f"[{number}]", _CIRCLED[number-1]):
        return None
    remaining = list(parts)
    if following:
        remaining[leftmost] = dict(part, text=following)
    else:
        remaining.pop(leftmost)
    if not remaining:
        return None
    body = _join(remaining)
    if not body or _INFIX.match(body):
        return None
    # Explicit punctuation/circled digits are a strong signal; a plain
    # number needs a visible column gap. A merged "2 Answer" supplies no
    # such proof on its own and is only eligible with corroborating fields.
    explicit = marker != str(number)
    if not explicit and _UNIT.fullmatch(body):
        return None
    if marker.startswith("(") and _FORMULA_TAIL.search(body):
        return None
    if not following:
        gap = min(p["x"] for p in remaining) - (part["x"] + part["w"])
        separated = gap >= .9 * part["w"]
    else:
        # Vision sometimes attaches the left badge to the LOWER line of
        # a wrapped answer. The earlier line then starts in the text column.
        preceding = parts[:leftmost]
        glyph_width = part["w"] / len(part["text"])
        separated = bool(preceding and following[0].isalpha()
                         and min(p["x"] for p in preceding) - part["x"] >= 2.3 * glyph_width
                         and all(p["y"] + p["h"] / 2 < part["y"] for p in preceding))
    # With short lowercase noun phrases even a wide gap could separate
    # values from the objects being counted ("1 файл", "2 больших каталога").
    could_be_count = not explicit and body[:1].islower() and len(body.split()) <= 3
    return body, explicit or separated, could_be_count


_PERCENT = re.compile(r"[0-9]+(?:[.,][0-9]+)?\s*[%‰]")


def percent_badge_splits(options):
    """Locate a digit joined to its percentage only with measured badge and answer columns."""
    peers = []
    for number, parts in enumerate(options, 1):
        if len(parts) < 2:
            continue
        mark, *body = sorted(parts, key=lambda p: p["x"])
        value = _join(body)
        if mark["text"] != str(number) or not _PERCENT.fullmatch(value):
            continue
        start = body[0]["x"]
        gutter = start - mark["x"]
        if gutter <= 0 or not 0 < mark["w"] <= .52 * gutter:
            continue
        end = max(p["x"] + p["w"] for p in body)
        peers.append((mark["x"], start, end - start, max(p["h"] for p in body), value))

    splits = {}
    for number, parts in enumerate(options, 1):
        value = _join(parts)
        if not _PERCENT.fullmatch(value) or not value.startswith(str(number)):
            continue
        tail = value[1:]
        if not _PERCENT.fullmatch(tail):
            continue
        x = min(p["x"] for p in parts)
        right = max(p["x"] + p["w"] for p in parts)
        height = max(p["h"] for p in parts)
        aligned = [(start, width, text) for mark, start, width, body_height, text in peers
                   if abs(mark - x) <= .18 * (start - mark) and .75 * height <= body_height <= 1.3 * height]
        if len(aligned) < 2:
            continue
        start = median(a for a, _, _ in aligned)
        gutter = start - x
        if any(abs(a - start) > .18 * gutter for a, _, _ in aligned):
            continue
        # A real 24% at either margin must survive. The box should reach as far
        # to the right of the verified gutter as a separately read 4% does.
        widths = [width for _, width, text in aligned
                  if len(re.sub(r"\s", "", text)) == len(re.sub(r"\s", "", tail))
                  and text[-1] == tail[-1]]
        if widths and abs(right - start - median(widths)) <= .34 * gutter:
            splits[number - 1] = (tail, start)
    return splits


def numbered_gutter_texts(fields):
    """Remove detached, smaller ordinals only when all choices share the same gutter."""
    if not 4 <= len(fields) <= 8:
        return None
    values, gutters = [], []
    for number, parts in enumerate(fields, 1):
        if len(parts) < 2:
            return None
        mark = min(parts, key=lambda p: p["x"])
        glyph = mark["text"]
        # A lower-case Cyrillic з resembles a 3; a tiny 3 can also be read as Е.
        # The other ordinals, font scale and spacing must all corroborate it.
        if glyph != str(number) and not (number == 3 and glyph in ("з", "З", "Е", "E")):
            return None
        body = [part for part in parts if part is not mark]
        text = _join(body)
        height = max(p["h"] for p in body)
        gap = min(p["x"] for p in body) - mark["x"] - mark["w"]
        if (not text or _UNIT.fullmatch(text) or not .35 * height <= mark["h"] <= .84 * height
                or gap < .82 * height or mark["w"] > 1.1 * mark["h"]):
            return None
        if _INFIX.match(text) and not re.fullmatch(r"[−—–+-]\s*\d+(?:[.,]\d+)?\s*[%‰]?", text):
            return None
        gutters.append(gap / height)
        values.append(text)
    if max(gutters) > 1.8 * min(gutters) or len({t.casefold() for t in values}) != len(values):
        return None
    return values


def _overlapping_percent_artifact(parts):
    # Tesseract can report the lower loop of '%' a second time as a tiny digit, letter or degree sign.
    # Only remove it when it overlaps the already recognized percent glyph.
    if len(parts) != 2:
        return False
    amount, digit = parts
    if not _PERCENT.fullmatch(amount["text"]) or not re.fullmatch(r"[0-9°oO]|fo", digit["text"]):
        return False
    right = amount["x"] + amount["w"]
    return (0 < digit["w"] <= .28 * amount["w"] and 0 < digit["h"] <= .6 * amount["h"]
            and right - digit["w"] <= digit["x"] <= right - .5 * digit["w"]
            and amount["y"] + .3 * amount["h"] <= digit["y"]
            and digit["y"] + digit["h"] <= amount["y"] + 1.04 * amount["h"])


def ocr_texts(groups):
    """Question followed by two to eight choices; preserve ambiguous content and option order."""
    texts = [_join(parts) for parts in groups]
    if not 3 <= len(texts) <= 9:
        return texts
    # Older native workers may report text only. They retain their raw output.
    if not all(all(key in p for key in ("x", "y", "w", "h")) for group in groups for p in group):
        return texts
    texts[0] = _join(_without_timer(groups[0]))
    options = groups[1:]
    if sum(bool(_PERCENT.fullmatch(value)) for value in texts[1:]) >= 2:
        options = [parts[:1] if _overlapping_percent_artifact(parts) else parts for parts in options]
        texts[1:] = [_join(parts) for parts in options]
    numbered = numbered_gutter_texts(options)
    if numbered:
        return [texts[0], *numbered]
    badges = [_badge_candidate(parts, i) for i, parts in enumerate(options, 1)]
    for index, (value, _) in percent_badge_splits(options).items():
        badges[index] = (value, True, False)
    strong = sum(1 for candidate in badges if candidate and candidate[1])
    if strong and (all(badges) or strong >= 3) and not all(b[2] for b in badges if b):
        cleaned = [candidate[0] if candidate and (candidate[1] or all(badges)) else raw
                   for raw, candidate in zip(texts[1:], badges)]
        # Label cleanup may never merge two distinct answer choices.
        if len({text.casefold() for text in cleaned}) == len(badges):
            texts[1:] = cleaned
    return texts
