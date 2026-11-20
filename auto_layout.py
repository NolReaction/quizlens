"""Find a question and two to eight choices in full-window OCR, in screen coordinates."""
from dataclasses import dataclass
from itertools import groupby
import math
import re
from statistics import median

from ocr_layout import numbered_gutter_texts, percent_badge_splits
from quiz_mode import checkbox_prefix, clean_options, instruction_only, selection_hint


def colored_card_stack(image):
    """Find a single, evenly spaced column of four solid answer cards."""
    width, height = image.size
    scale = min(1, 360 / width, 280 / height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    palette = image.resize((w, h), resample=0).convert('RGB').quantize(colors=24)
    rgb = palette.getpalette()
    colors = {}
    lookup = bytearray(range(256))
    for _, i in sorted(palette.getcolors(), reverse=True):
        tone = rgb[3*i:3*i+3]
        if max(tone)-min(tone) < 55:
            continue
        representative = next((j for j, other in colors.items() if
                               max(abs(a-b) for a,b in zip(tone,other)) <= 30), None)
        if representative is None:
            colors[i] = tone
        else:
            lookup[i] = representative
    pixels = palette.tobytes().translate(bytes(lookup))
    bands = {i: [] for i in colors}
    for y in range(h):
        row = pixels[y*w:(y+1)*w]
        for i in colors:
            color = bytes((i,))
            count = row.count(color)
            if count < .14*w:
                continue
            left, right = row.find(color), row.rfind(color) + 1
            if count < .85 * (right-left):
                continue
            spans = bands[i]
            if not spans or y-spans[-1][-1][0] > max(4, .034*h) or abs(left-spans[-1][-1][1]) > .04*w:
                spans.append([])
            spans[-1].append((y, left, right))
    cards = []
    for color, bands_of_color in bands.items():
        for rows in bands_of_color:
            if len(rows) < .045*h:
                continue
            left = median(row[1] for row in rows)
            right = median(row[2] for row in rows)
            cards.append((left, rows[0][0], right, rows[-1][0]+1, color))
    columns = []
    for card in sorted(cards, key=lambda box: box[1]):
        left, _, right, _, _ = card
        peers = next((col for col in columns if abs(col[0][0]-left) <= .045*(right-left)
                      and abs(col[0][2]-right) <= .045*(right-left)), None)
        if peers is None:
            columns.append([card])
        else:
            peers.append(card)
    stacks = []
    for column in columns:
        if len(column) != 4 or len({card[4] for card in column}) < 3:
            continue
        heights = [b-t for _, t, _, b, _ in column]
        size = median(heights)
        gaps = [b[1]-a[3] for a,b in zip(column, column[1:])]
        if min(heights) < .78*size or max(heights) > 1.24*size or min(gaps) <= 0 or max(gaps) > .46*size:
            continue
        # A tile whose expected height extends past the crop may be incomplete.
        if column[-1][3] >= h-1 and heights[-1] < .93*size:
            continue
        stacks.append([tuple(round(value * (width/w if i % 2 == 0 else height/h))
                            for i,value in enumerate(card[:4])) for card in column])
    return stacks[0] if len(stacks) == 1 else []


def uniform_card_stack(image):
    """Locate four separate, identical fill rectangles, even next to a picture."""
    width, height = image.size
    scale = min(1, 620 / width, 480 / height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    palette = image.resize((w, h), resample=0).convert('RGB').quantize(colors=48)
    rgb = palette.getpalette()
    colors = {}
    lookup = bytearray(range(256))
    for count, index in sorted(palette.getcolors(), reverse=True):
        tone = rgb[3*index:3*index+3]
        if count < .006*w*h or max(tone) - min(tone) > 68:
            continue
        representative = next((i for i, other in colors.items()
                               if max(abs(a-b) for a, b in zip(tone, other)) <= 8), None)
        if representative is None:
            colors[index] = tone
        else:
            lookup[index] = representative
    pixels = palette.tobytes().translate(bytes(lookup))
    runs = re.compile(rb'(.)\1{%d,}' % max(2, round(.10*w)), re.S)
    columns = []
    for y in range(h):
        for run in runs.finditer(pixels[y*w:(y+1)*w]):
            index = run[1][0]
            left, right = run.span()
            span = right-left
            if index not in colors or span > .75*w:
                continue
            tolerance = max(3, .055*span)
            column = next((c for c in columns if c[0] == index and
                           abs(c[1]-left) < tolerance and abs(c[2]-right) < tolerance), None)
            if column is None:
                column = (index, left, right, [])
                columns.append(column)
            column[3].append((y, left, right))
    stacks = []
    for index, _, _, rows in columns:
        if len(rows) < .24*h:
            continue
        left = round(median(row[1] for row in rows))
        right = round(median(row[2] for row in rows))
        color = bytes((index,))
        start, end = rows[0][0], rows[-1][0]+1
        # Sample narrow stripes near the edges, outside the centered text.
        inset = max(1, round(.027*(right-left)))
        stripe = max(2, round(.026*(right-left)))
        filled = []
        for y in range(start, end):
            row = pixels[y*w:(y+1)*w]
            count = row[left+inset:left+inset+stripe].count(color)
            count += row[right-inset-stripe:right-inset].count(color)
            filled.append((y, count >= 1.4*stripe))
        bands = []
        for present, group in groupby(filled, key=lambda row: row[1]):
            rows_in_band = list(group)
            if present and len(rows_in_band) >= .055*h:
                bands.append((rows_in_band[0][0], rows_in_band[-1][0]+1))
        if len(bands) != 4:
            continue
        heights = [bottom-top for top, bottom in bands]
        typical = median(heights)
        gaps = [b[0]-a[1] for a, b in zip(bands, bands[1:])]
        if (min(heights) < .85*typical or max(heights) > 1.18*typical or typical > .24*h
                or max(gaps) > .24*typical or bands[0][0] <= 1 or bands[-1][1] >= h-1):
            continue
        stacks.append([(round(left*width/w), round(top*height/h),
                        round(right*width/w), round(bottom*height/h)) for top, bottom in bands])
    return stacks[0] if len(stacks) == 1 else []


@dataclass(frozen=True)
class Span:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    @property
    def cy(self):
        return self.y + self.h / 2


def _union(spans, text=None):
    x = min(s.x for s in spans)
    y = min(s.y for s in spans)
    return Span(text if text is not None else ' '.join(s.text for s in spans), x, y,
                max(s.right for s in spans) - x, max(s.bottom for s in spans) - y)


def _square_controls(spans):
    # Tesseract can read an unchecked box as two letters and omit the checked
    # boxes on other rows. Verify square geometry, the repeated left gutter and
    # alignment of actual answer starts even on rows missing the control.
    candidates = [s for s in spans if s.text in ('LC', 'LJ', 'L]') and .85 <= s.w/s.h <= 1.15]
    if not 2 <= len(candidates) <= 12:
        return set()
    verified = set()
    for box in candidates:
        peers = [s for s in candidates if abs(s.x-box.x) <= .35*box.h and .85 <= s.h/box.h <= 1.15]
        if not any(1.5*box.h < abs(s.cy-box.cy) < .5 for s in peers):
            continue
        followers = [s for s in spans if .28*box.h <= s.x-box.right <= 1.5*box.h
                     and abs(s.cy-box.cy) <= .65*box.h]
        if not followers:
            continue
        text_x = min(s.x for s in followers)
        aligned = [s for s in spans if abs(s.x-text_x) <= .3*box.h and abs(s.cy-box.cy) < .5]
        # Some neighboring options begin at text_x despite the missing box.
        if any(all(abs(s.cy-p.cy) > box.h for p in peers) for s in aligned):
            verified.add(box)
    return verified if len(verified) >= 2 else set()


def _align_letter_badges(spans):
    """Keep detached labels aligned to the same text column when OCR missed neighboring badges."""
    if len(spans) > 500:
        return spans
    pairs = []
    for mark in spans:
        glyph = re.fullmatch(r'([A-H])(?:[.)]|([a-h]))?', mark.text)
        if not glyph or (glyph[2] and glyph[1] != glyph[2].upper()):
            continue
        followers = [s for s in spans if .2*mark.h <= s.x-mark.right <= 3.5*mark.h
                     and abs(s.cy-mark.cy) < .55*mark.h and .65 < s.h/mark.h < 1.55
                     and s.text[0].isalpha()]
        if followers:
            pairs.append((mark, min(followers, key=lambda s:s.x)))
    skip = set()
    replacements = {}
    for mark, word in pairs:
        aligned = [s for s in spans if abs(s.x-word.x) < .48*word.h and
                   .74 < s.h/word.h < 1.28 and abs(s.cy-word.cy) <= .72]
        if len(aligned) < 3:
            continue
        peers = [m for m,w in pairs if abs(w.x-word.x) < .50*word.h and abs(m.x-mark.x) < .48*word.h]
        # An explicit mark is sufficient to preserve its own letter at the
        # new reading origin. Bare/doubled letters need an independent peer.
        explicit = any(m.text.endswith(('.', ')')) for m in peers)
        if not explicit or (not mark.text.endswith(('.',')')) and len(peers) < 2):
            continue
        replacements[word] = Span(mark.text[0]+'. '+word.text, word.x, word.y, word.w, word.h)
        skip.add(mark)
        if len(peers) < 2:
            continue
        # Noise from card borders or a lost badge may make its own dot or
        # vertical stroke. It is safely outside the verified answer column.
        for s in spans:
            if (s.text in ('|', '.') and mark.x-2*mark.h < s.x < word.x
                    and s.right <= word.x-.3*word.h
                    and (s.h < .45*word.h or (s.w < .35*word.h and s.h > 1.5*word.h))
                    and any(abs(s.cy-t.cy) <= .6*word.h for t in aligned)):
                skip.add(s)
    return [replacements.get(s,s) for s in spans if s not in skip]


def _lines(parts, aspect):
    # Use the same scale on both axes so distances can be measured in text heights.
    spans = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get('text'), str):
            continue
        coords = [part.get(k) for k in ('x', 'y', 'w', 'h')]
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in coords):
            continue
        x, y, w, h = coords
        text = ' '.join(part['text'].split())
        if text and w > 0 and h > 0 and 0 <= x < 1 and 0 <= y < 1:
            spans.append(Span(text, aspect*x, y, aspect*min(w, 1-x), min(h, 1-y)))
    spans = _align_letter_badges(spans)
    controls = _square_controls(spans)
    rows = []
    for span in sorted(spans, key=lambda s: s.cy):
        if span in controls:
            span = Span('☐', span.x, span.y, span.w, span.h)
        if rows and abs(rows[-1][0].cy - span.cy) <= .52 * max(rows[-1][0].h, span.h):
            rows[-1].append(span)
        else:
            rows.append([span])
    lines = []
    for row in rows:
        group = []
        for span in sorted(row, key=lambda s: s.x):
            # Separate columns, even when their text sits on the same baseline.
            # A smaller digit in its own gutter may be the card's ordinal.
            # Keeping it as a separate box retains evidence lost by concatenation.
            separate_badge = (len(group) == 1 and re.fullmatch(r'[1-8A-HАБВГДЕЖЗз]', group[0].text)
                              and group[0].h < .87 * span.h
                              and span.x - group[0].right >= .85 * group[0].w)
            if group and (separate_badge or span.x - group[-1].right > 1.6 * max(span.h, group[-1].h)):
                lines.append(_union(group))
                group = []
            group.append(span)
        lines.append(_union(group))
    return lines


_PROMPT = re.compile(r'^(?:выбер\w*|укаж\w*|отмет\w*|определ\w*|найд\w*|рассчита\w*|'
                     r'сопостав\w*|заверш\w*|како\w*|котор\w*|choose|select|identify|pick|check|mark|tick|'
                     r'calculate|complete|which|what|how|where|when|why)\b', re.I)
_UI = re.compile(r'^(?:(?:вопрос|question)\s*\d+\s*(?:из|of|/)\s*(?:\d+.*|[бзoо])|'
                 r'(?:\d+\s*(?:с|s|сек(?:унд[ыа]?)?)|\d{1,2}:\d{2})|'
                 r'(?:(?:осталось(?: времени)?|time left|таймер)\b.*)|'
                 r'(?:выберите|выбери|select|choose)\s+(?:один(?: из)?|one)(?:\s+(?:вариант(?:ов)?(?: ответа)?|answer|option))?[.!]?|'
                 r'(?:далее|дальше|назад|продолжить|начать|завершить|отправить|ответить|next|previous|back|start|submit|finish|continue)'
                 r'(?:\s+(?:вопрос|ответ|ответы|тест|квиз|question|answer|answers|test|quiz))?[.!]?)$', re.I)
_SELECTED = re.compile(r'^(?:(?:отмечено|выбрано)\s*:?\s*\d+(?:\s+из\s+\d+)?|'
                       r'\d+(?:\s+of\s+\d+)?\s+selected)[.!]?(?:\s+(?:отправить|submit))?[.!]?$', re.I)
_RESULTS = re.compile(r'^(?:результаты (?:квиза|теста)|квиз заверш[её]н|quiz complete|тест заверш[её]н|'
                      r'(?:ваш результат|your score|правильный ответ|correct answer)\s*[:：])', re.I)
_MARK = re.compile(r'^(?:(?:\(([1-8A-HАБВГДЕЖЗСН])\)|\[([1-8A-HАБВГДЕЖЗСН])\]|([1-8A-HАБВГДЕЖЗСН])[.):](?!\d)|([①②③④⑤⑥⑦⑧ⒶⒷⒸⒹⒺⒻⒼⒽ]))\s*|'
                   r'([1-8A-HАБВГДЕЖЗСН])(?:\s+|$))(.*)$', re.I)
_SERIES = ('12345678', 'ABCDEFGH', 'АБВГДЕЖЗ')
_LATIN = str.maketrans({'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'Н': 'H'})
_CIRCLED = dict(zip('①②③④⑤⑥⑦⑧ⒶⒷⒸⒹⒺⒻⒼⒽ', '12345678ABCDEFGH'))


def _mark(s, series):
    m = _MARK.match(s.text)
    if not m:
        return None
    char = next(g for g in m.groups()[:5] if g is not None).upper()
    char = _CIRCLED.get(char, char)
    if series == 'ABCDEFGH':
        char = char.translate(_LATIN)
    elif series == 'АБВГДЕЖЗ':
        char = char.translate(str.maketrans({'A': 'А', 'B': 'В', 'E': 'Е'}))
    if char not in series:
        return None
    return series.index(char), m.group(6).strip(), m.group(5) is None


def _questions(lines):
    questions = []
    for last in lines:
        if _UI.match(last.text) or not ('?' in last.text or _PROMPT.match(last.text)):
            continue
        block = [last]
        # A prompt can wrap (or start with a short statement of the problem).
        for direction in (-1, 1):
            while len(block) < 6 and (direction == -1 or '?' not in block[-1].text):
                edge = block[0] if direction == -1 else block[-1]
                neighbors = []
                for s in lines:
                    gap = edge.y - s.bottom if direction == -1 else s.y - edge.bottom
                    marker = _MARK.match(s.text)
                    if (s in block or _UI.match(s.text) or marker and (not marker.group(5) or not marker.group(6)) or gap < -.1 * edge.h or
                        gap > .95 * max(s.h, edge.h) or abs(s.x-edge.x) > 1.7 * edge.h or
                        not .74 <= s.h / edge.h <= 1.36):
                        continue
                    if direction == -1 and ('?' in s.text or _PROMPT.match(edge.text) and len(s.text.split()) < 3):
                        continue
                    neighbors.append((gap, s))
                if not neighbors:
                    break
                other = min(neighbors, key=lambda t: t[0])[1]
                if direction == -1:
                    block.insert(0, other)
                else:
                    block.append(other)
        q = _union(block)
        if len(q.text) >= 5 and q not in questions:
            questions.append(q)
    return questions


def prompt_bounds(parts, size):
    """Locate a single question among the OCR words above an answer stack."""
    width, height = size
    spans = [dict(text=part['text'], x=part['x']/width, y=part['y']/height,
                  w=part['w']/width, h=part['h']/height) for part in parts]
    questions = _questions(_lines(spans, width/height))
    if len(questions) != 1:
        return None
    question = questions[0]
    return tuple(round(value*height) for value in (question.x, question.y, question.right, question.bottom))


def assess_card_layout(parts, cards, *, size):
    """Read only the title and text belonging to a verified uniform card stack."""
    width, height = size
    aspect = width/height
    lines = _lines(parts, aspect)
    boxes = [tuple(coordinate/height for coordinate in box) for box in cards]
    left, top, right, bottom = boxes[0]
    card_width, card_height = right-left, bottom-top
    questions = [q for q in _questions(lines) if 0 <= top-q.bottom <= .8*card_height
                 and left-2.5*card_width <= q.x and left-.6*card_width <= q.right <= right+.2*card_width
                 and len(q.text.split()) >= 4]
    if len(questions) != 1:
        return None
    question = questions[0]
    options = []
    for x, y, r, b in boxes:
        margin = .18*(b-y)
        content = [s for s in lines if x <= s.x and s.right <= r
                   and y+margin <= s.cy <= b-margin and .04*(b-y) <= s.h <= .34*(b-y)]
        if not 1 <= len(content) <= 4:
            return None
        options.append(_union(_group_by_rows(content)).text)
    if len({option.casefold() for option in options}) != 4:
        return None
    qbox = (question.x, question.y, question.right, question.bottom)
    instructions = [s.text for s in lines if question.bottom <= s.y < top and question.x <= s.x < right
                    and instruction_only(s.text)]
    return {'regions': [{'x': x/aspect, 'y': y, 'w': (r-x)/aspect, 'h': b-y}
                        for x, y, r, b in (qbox, *boxes)],
            'texts': [question.text, *clean_options(options)],
            **selection_hint(question.text, options, instructions)}


def _rows_uniform(values, height):
    gaps = [b-a for a,b in zip(values, values[1:])]
    return (min(gaps) > 1.45 * height and max(gaps) <= .42 and
            max(gaps) < 2.15 * min(gaps))


def _group_by_rows(spans):
    rows = []
    for s in sorted(spans, key=lambda p: p.cy):
        if rows and abs(rows[-1][0].cy - s.cy) <= .48 * max(rows[-1][0].h, s.h):
            rows[-1].append(s)
        else:
            rows.append([s])
    return [p for row in rows for p in sorted(row, key=lambda p: p.x)]


def _extract(anchors, below, question, aspect, labeled=None):
    """Partition by option rows and columns, allowing a badge between two text lines."""
    height = median(s.h for s in anchors)
    count = len(anchors)
    if not 2 <= count <= 8:
        return None
    # Cluster starts by column first, so labels can run down columns or across
    # rows; only the last row may have fewer members.
    columns = []
    for anchor in sorted(anchors, key=lambda span: span.x):
        if not columns or anchor.x - columns[-1][0].x > max(.023 * aspect, 2.05 * height):
            columns.append([])
        columns[-1].append(anchor)
    if len(columns) > 4:
        return None
    for column in columns:
        column.sort(key=lambda span: span.cy)
    row_count = len(columns[0])
    sizes = [len(c) for c in columns]
    if sizes != sorted(sizes, reverse=True) or sizes[-1] < row_count - 1:
        return None
    rows = [[column[i] for column in columns if i < len(column)] for i in range(row_count)]
    row_y = [sum(s.cy for s in row)/len(row) for row in rows]
    if len(rows) > 1 and not _rows_uniform(row_y, height):
        return None
    pitch = median([b-a for a,b in zip(row_y, row_y[1:])]) if row_count > 1 else max(.07, 3.7 * height)
    for row in rows:
        for left, right in zip(row, row[1:]):
            if (abs(left.cy-right.cy) > min(.44 * pitch, 2.15 * height) or
                right.x-left.x < max(4.0 * height, .095 * aspect) or left.right > right.x + .5*height):
                return None
    top = max(question.bottom, row_y[0]-.52*pitch)
    bottom = min(1.0, row_y[-1]+.53*pitch)
    if row_y[0] - question.bottom > max(.22, 2.0*pitch):
        return None
    y_edges = [top] + [(a+b)/2 for a,b in zip(row_y, row_y[1:])] + [bottom]
    start = min(row[0].x for row in rows)
    if question.right < start or question.x > start + .2 * aspect:
        return None
    if len(rows[0]) == 1:
        # In a full browser window the quiz may share a row with a sidebar.
        # Estimate the text column from line starts close to the answer labels.
        aligned = [s for s in below if top <= s.cy < bottom and
                   start - height <= s.x <= start + max(6*height, .045*aspect)]
        if not aligned:
            return None
        column_width = max(.16 * aspect, max(s.right for s in aligned)-start + .9*height)
    else:
        starts = [min(s.x for s in column) for column in columns]
        column_width = median(b-a for a,b in zip(starts, starts[1:]))
    left_edge = max(0, start - max(.012*aspect, height))
    right_edge = min(aspect, (min(s.x for s in columns[-1]) if len(columns) > 1 else left_edge)
                     + column_width + height)
    col_edges = [left_edge, *(min(s.x for s in col) - height*.18 for col in columns[1:]), right_edge]
    groups = {}
    # More text in the answer column after the last row may be a fifth option.
    # Recognized navigation was already removed at the footer boundary.
    if any(s.cy >= bottom and any(abs(s.x - a.x) < max(4 * height, .045 * aspect)
                                  for a in rows[-1]) for s in below):
        return None
    for i,row in enumerate(rows):
        for j,anchor in enumerate(row):
            content = [s for s in below if y_edges[i] <= s.cy < y_edges[i+1]
                       and col_edges[j] <= s.x < col_edges[j+1] and s.right <= col_edges[j+1]+height*.4]
            if anchor not in content:
                return None
            # A separate right-hand pane may sit inside the overall width of
            # a particularly long option. Two unrelated columns on one row
            # are not the text of a single answer.
            for a in content:
                if any(b.x-a.right > max(5*height,.065*aspect) and abs(a.cy-b.cy)<.55*max(a.h,b.h)
                       for b in content if a != anchor or (not labeled or labeled[anchor][1])):
                    return None
            if labeled:
                ordinal, suffix, explicit = labeled[anchor]
                if suffix:
                    content[content.index(anchor)] = Span(suffix,anchor.x,anchor.y,anchor.w,anchor.h)
                else:
                    content.remove(anchor)
                    # Bordered numeric badges may leave a tiny extra stroke
                    # at their lower corner. Restrict this to the badge gutter.
                    content = [s for s in content if not (
                        s.text in ('|', '.', ']') and s.h < .45 * anchor.h and
                        s.w < .4 * anchor.h and anchor.x - anchor.h <= s.x <= anchor.right + 1.5*anchor.h and
                        abs(s.cy-anchor.cy) <= 1.5 * anchor.h)]
            else:
                ordinal = i*len(columns)+j
            if not content:
                return None
            option = _union(_group_by_rows(content))
            if option.text.strip() in ('%', '‰'):
                return None
            if not labeled:
                if len(content) > 4:
                    return None
            elif any(s is not anchor and s in labeled for s in content):
                return None
            # Leave room for a second line and longer text in the same card. Full
            # rediscovery will adjust to actual page reflow on a later question.
            left = max(col_edges[j], min(s.x for s in content)-.4*height)
            right = min(col_edges[j+1], max(left+column_width*.83, option.right+.55*height))
            box = (left,y_edges[i]+.015*pitch,right,y_edges[i+1]-.015*pitch)
            groups[ordinal] = (option.text, box)
    if len(groups) != count or len({s[0].casefold() for s in groups.values()}) != count:
        return None
    if right_edge-left_edge < .12*aspect:
        return None
    qleft = max(0, min(left_edge,question.x)-.2*height)
    qbox = (qleft,max(0,question.y-.35*height),min(aspect,max(right_edge,question.right+.4*height)),
            min(top,question.bottom+max(.55*height, .25*(top-question.bottom))))
    entries = [(question.text,qbox)] + [groups[i] for i in range(count)]
    if any(r <= x or b <= y for _,(x,y,r,b) in entries):
        return None
    return {'regions':[{'x':x/aspect,'y':y,'w':(r-x)/aspect,'h':b-y} for _,(x,y,r,b) in entries],
            'texts':[text for text,_ in entries]}


def _marked(lines, question, aspect):
    candidates = []
    for series in _SERIES:
        parsed = {s: value for s in lines if (value := _mark(s, series))}
        if 2 <= len(parsed):
            candidates.append((series, parsed))
    longest = max((len(group) for _, group in candidates), default=0)
    for series, marked in candidates:
        if len(marked) != longest or longest > 8:
            continue
        options = [[s for s, v in marked.items() if v[0] == i] for i in range(len(marked))]
        # Multiple groups of markers in one pane are ambiguous; never search all
        # combinations (especially in tables of small numbers).
        if not all(len(items) == 1 for items in options):
            continue
        anchors = [items[0] for items in options]
        # Bare values ('1 hour', '2 days', …) are possible answers. Full OCR
        # strings with plain numeric prefixes need independent badge geometry.
        if series =='12345678' and all(suffix and not explicit for _,suffix,explicit in marked.values()):
            continue
        result = _extract(anchors, lines, question, aspect, marked)
        if result:
            return result
    return None


def _unmarked(lines, question, aspect):
    # Attach any isolated short badge to its answer. Some engines miss
    # several badges but still recognize A; aligned cells can establish order.
    content = list(lines)
    standalone = [s for s in lines if any(m and not m[1] for m in (_mark(s,k) for k in _SERIES))]
    badge_targets = {}
    for mark in standalone:
        near = [s for s in content if s not in standalone and
                -.15 * mark.h <= s.x-mark.right <= 3.8*max(mark.h,s.h) and
                abs(s.cy-mark.cy) < .85*max(mark.h,s.h)]
        if len(near) == 1:
            content.remove(mark)
            badge_targets[near[0]] = mark

    # Wrapped option lines have a smaller vertical gap than separate buttons.
    # Build independent columns so the wrapping of one answer cannot reorder its neighbor.
    size = median([s.h for s in content]) if content else 0
    if not size:
        return None
    cols = []
    for s in sorted(content, key=lambda p: p.x):
        if not cols or abs(cols[-1][0].x-s.x) > max(.014*aspect,2.15*size):
            cols.append([])
        cols[-1].append(s)
    if len(cols) > 4:
        return None
    sorted_cols = []
    for col in cols:
        blocks = []
        for span in sorted(col, key=lambda p:p.cy):
            if blocks and span.y-blocks[-1][-1].bottom < .83*max(size,span.h):
                blocks[-1].append(span)
            else:
                blocks.append([span])
        sorted_cols.append([_union(group) for group in blocks])
    if not 2 <= sum(map(len, sorted_cols)) <= 8:
        return None
    # Establish visual order from column membership, matching _extract.
    anchors = [col[i] for i in range(max(map(len, sorted_cols))) for col in sorted_cols if i < len(col)]
    fields = [[vars(badge_targets[s]), vars(s)] if s in badge_targets else [vars(s)] for s in anchors]
    numbered = numbered_gutter_texts(fields)
    text_anchors = anchors.copy()
    for i, (value, start) in percent_badge_splits(fields).items():
        span = anchors[i]
        text_anchors[i] = Span(value, start, span.y, span.right - start, span.h)
    result = _extract(text_anchors, text_anchors, question, aspect)
    if not result:
        return None
    # Resolve a full series before dropping individual ordinals; this retains
    # corroboration for a partially garbled badge such as 'Cc.'.
    choices = clean_options(result['texts'][1:])
    if choices != result['texts'][1:]:
        result['texts'][1:] = choices
        return result
    for i,s in enumerate(anchors):
        marks = [_mark(Span(s.text, s.x, s.y, s.w, s.h),series) for series in _SERIES]
        if s in badge_targets and not numbered:
            if not any(m and m[0]==i for m in (_mark(badge_targets[s], k) for k in _SERIES)):
                return None
        # Strip an explicit punctuation-prefixed label when others were missed;
        # numeric option content such as '4 GB' is deliberately kept intact.
        explicit = [m for m in marks if m and m[2] and m[1]]
        if explicit and not any(m[0] == i for m in explicit):
            return None
        correct = [m for m in marks if m and m[0]==i and m[1] and
                   (m[2] or s.text[:1].isupper() and re.match(r'^[A-HАБВГДЕЖЗ]\s+[0-9]',s.text))]
        if correct:
            result['texts'][i+1] = correct[0][1]
    return result if len({t.casefold() for t in result['texts'][1:]})==len(anchors) else None


_TOTAL = re.compile(r'^(?:(?:всего\s+)?(?:вариантов(?: ответа)?|ответов всего|(?:total )?(?:options|choices))\s*[:=]\s*(\d{1,2})|'
                    r'всего\s+(\d{1,2})\s+вариант(?:а|ов)?(?: ответа)?|(\d{1,2})\s+(?:available (?:options|choices)|(?:options|choices) available))[.!]?$', re.I)
_BINARY = ({'да','нет'}, {'верно','неверно'}, {'истина','ложь'}, {'правда','ложь'}, {'true','false'}, {'yes','no'})
_PARTIAL = 'Видна только часть вариантов. Уменьши масштаб или прокрути окно, чтобы в кадр попали вопрос и все ответы.'


def _binary_statement(lines):
    # True/false tasks often display a statement ending in a period. Use the
    # paired option labels to locate it, then keep adjacent wrapped stem lines.
    choices = []
    for line in lines:
        marker = _MARK.match(line.text)
        label = marker.group(6) if marker and marker.group(6) else line.text
        label = label.strip(' .!').casefold()
        if any(label in pair for pair in _BINARY):
            choices.append((label, line))
    if len(choices) != 2 or {label for label, _ in choices} not in _BINARY:
        return []
    answer_top = min(line.y for _, line in choices)
    answer_left = min(line.x for _, line in choices)
    preceding = [s for s in lines if s.bottom <= answer_top and answer_top-s.bottom <= .23
                 and abs(s.x-answer_left) <= max(.08, 4*s.h)
                 and not _UI.fullmatch(s.text) and len(s.text.split()) >= 2]
    if not preceding:
        return []
    stem = [max(preceding, key=lambda s: s.bottom)]
    for span in sorted(preceding, key=lambda s: -s.bottom):
        first = stem[0]
        if (0 <= first.y - span.bottom < .85 * first.h and abs(span.x-first.x) < 1.5 * first.h
                and .78 < span.h/first.h < 1.28):
            stem.insert(0, span)
    return [_union(stem)]


def assess_layout(parts, *, aspect=1.6):
    """Single-pass capture result used by both window readers."""
    warnings = []
    result = detect_layout(parts, aspect=aspect, _issues=warnings)
    if result:
        return result
    result = {'regions': [], 'texts': []}
    if warnings:
        result.update(captureStatus='partial', hint=warnings[0])
    return result


def capture_issue(parts, *, aspect=1.6):
    """Explain incomplete capture only when option or frame geometry supplies a cue."""
    return assess_layout(parts, aspect=aspect).get('hint')


def detect_layout(parts, *, aspect=1.6, _issues=None):
    """Return screen-relative rectangles and texts, or None while the frame is incomplete."""
    if not isinstance(parts,(tuple,list)) or not 3 <= len(parts) <= 1500:
        return None
    if type(aspect) not in (int, float) or not .2 <= aspect <= 8:
        return None
    lines = _lines(parts, aspect)
    if any(_RESULTS.match(s.text) for s in lines):
        return None
    totals = {s: int(next(g for g in m.groups() if g)) for s in lines if (m := _TOTAL.fullmatch(s.text))}
    instructions = [s for s in lines if instruction_only(s.text)]
    checkboxes = []
    content = []
    for span in lines:
        if span in instructions or span in totals:
            continue
        prefix = checkbox_prefix(span.text)
        if prefix:
            checkboxes.append(span)
            text = span.text[prefix.end():]
            if not text:
                continue
            span = Span(text, span.x, span.y, span.w, span.h)
        content.append(span)
    if sum(bool('?' in s.text or _PROMPT.match(s.text)) for s in content) > 24:
        return None
    # A generic instruction can itself be the entire question. Prefer an
    # ordinary prompt, then try these headers only if no layout was found.
    for questions in (_questions(content), [s for s in instructions if _PROMPT.match(s.text)], _binary_statement(content)):
        results = []
        for question in questions:
            below = [s for s in content if s.cy > question.bottom and s.x >= question.x - max(4*question.h,.07*aspect)]
            if len(below) < 2:
                continue
            foot = [s.y for s in below if _SELECTED.fullmatch(s.text) or
                    (_UI.fullmatch(s.text) and not re.match(r'^(?:\d|вопрос|question|осталось|time left|таймер)',s.text,re.I))]
            end = min(foot, default=1)
            body = [s for s in below if s.cy < end and not _UI.fullmatch(s.text)]
            if not 2 <= len(body) <= 80:
                continue
            marked = _marked(body, question, aspect)
            result = marked or _unmarked(body, question, aspect)
            if not result:
                if _issues is not None:
                    sequence = [m[0] for s in body if (m := _mark(s, _SERIES[0])) and (m[2] or not m[1])]
                    if len(sequence) >= 2 and (set(sequence) != set(range(len(sequence))) or
                                               max(s.bottom for s in body) > .95):
                        _issues.append(_PARTIAL)
                continue
            regions = result['regions']
            left = min(r['x'] for r in regions) * aspect
            right = max(r['x'] + r['w'] for r in regions) * aspect
            bottom = max(r['y'] + r['h'] for r in regions)
            margin = max(.14, 5*question.h)
            def local(s):
                return left-2*question.h <= s.x <= right and s.right > left and question.y-margin <= s.cy <= bottom+margin
            # A short quiz can have an unusually large gap before its footer.
            # Only accept such a distant instruction if no other prompt follows.
            after_answers = [s for s in instructions if bottom-question.h <= s.y <= end + .8*s.h and
                             left-3*question.h <= s.x <= right and
                             not any(question.bottom < other.y < s.y and '?' in other.text for other in content)]
            close_instructions = [s for s in instructions if local(s) and s.y <= end + .8*s.h]
            nearby = [s.text for s in close_instructions or after_answers[:1]]
            count = len(regions)-1
            declared = {v for label,v in totals.items() if local(label)}
            options = {t.rstrip('.!').strip().casefold() for t in result['texts'][1:]}
            short_list = count < 4 and options not in _BINARY
            # Text alone cannot distinguish a three-choice list from the first
            # three options of a loading question. An explicit total, bottom
            # action or a true/false pair is supporting evidence that it's complete.
            has_footer = any(local(s) and s.y > question.bottom and s.cy >= bottom - margin/2
                             for s in below if s.y in foot)
            # A large blank band before a bare instruction can be an option
            # whose text is still loading. Use the observed button pitch.
            pitch = max(r['h'] for r in regions[1:])
            near_footer = any(s.cy - bottom <= max(1.35*pitch,2*question.h) for s in after_answers)
            if near_footer and (marked or not any(_MARK.match(s.text) for s in body)):
                has_footer = True
            visible = [s for s in body if left-question.h <= s.x <= right and s.cy >= regions[1]['y']]
            touches_edge = any(s.bottom >= .992 for s in visible)
            almost_offscreen = bottom >= .965 and not has_footer and not declared and count < 8
            if (declared and declared != {count}) or touches_edge or almost_offscreen or (short_list and not (declared or has_footer)):
                if _issues is not None:
                    msg = _PARTIAL if declared or touches_edge or almost_offscreen else 'Вижу меньше четырёх вариантов. Покажи нижнюю часть вопроса или разметь ответы вручную.'
                    _issues.append(msg)
                continue
            raw_options = list(result['texts'][1:])
            for i,r in enumerate(regions[1:]):
                # Standalone checkboxes may sit in a narrow gutter outside the
                # inferred text rectangle. Require one glyph per distinct card.
                x = r['x'] * aspect
                if any(x-3.8*max(s.h,question.h) <= s.x <= x+2*question.h
                       and r['y'] <= s.cy <= r['y']+r['h'] for s in checkboxes):
                    raw_options[i] = '☐ ' + raw_options[i]
            result.update(selection_hint(question.text, raw_options, nearby))
            if result not in results:
                results.append(result)
        if results:
            return results[0] if len(results) == 1 else None
    return None
