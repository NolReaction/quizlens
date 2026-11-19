"""Interpret visible quiz instructions. Return an exact count only when the UI supplies one."""
import re
import unicodedata


def _plain(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold().replace('ё', 'е')
    return ' '.join(text.split()).rstrip('.!:; ')


_NUMBERS = {
    **dict.fromkeys('один одно одну one single'.split(), 1),
    **dict.fromkeys('два две two both оба обе'.split(), 2),
    **dict.fromkeys('три three'.split(), 3),
    **dict.fromkeys('четыре four'.split(), 4),
    **dict.fromkeys('пять five'.split(), 5),
    **dict.fromkeys('шесть six'.split(), 6),
    **dict.fromkeys('семь seven'.split(), 7),
    **dict.fromkeys('восемь eight'.split(), 8),
}
_NUMBER = r'(?:\d{1,2}|' + '|'.join(_NUMBERS) + r')'
_VERB = r'(?:выбери(?:те)?|выбрать|выбирать|укажи(?:те)?|указать|отметь(?:те)?|отметить|выдели(?:те)?|select|choose|pick|mark|tick|check)'
# Restrict verb matches to the start of a directive, including appended directives
# ("Что верно? Выберите два."). A phrase quoted as the topic is not a directive.
_START = r'(?:^|[?!:.;(]\s*|[—–]\s*)(?:(?:пожалуйста|please)[, ]+)?'
_SKIP = r'(?:(?:из списка|из предложенных|ровно|именно|только|любые|exactly|only|the|any)\s+)*'
_COUNT = re.compile(_START + _VERB + r'\s+' + _SKIP + r'(' + _NUMBER + r')\b(?!\s*[+*/=]|[.,]\d)')
_QUESTION_COUNT = re.compile(r'^(?:какие|каких|какими|which|what)\s+(' + _NUMBER + r')\b')
_INFLECTED = r'(?:одного|двух|трех|четырех|пяти|шести|семи|восьми|' + _NUMBER + ')'
_LIMIT = r'(?:(?:до|как минимум|минимум|не более|не менее|at (?:least|most)|up to|no more than)\s+' + _INFLECTED + ')'
_RANGE = (r'(?:' + _NUMBER + r'\s*(?:или|or|[-–—])\s*(?:несколько|более|more|' + _NUMBER + r')|'
          r'(?:от|from)\s+' + _INFLECTED + r'\s+(?:до|to)\s+' + _INFLECTED + ')')
_FLEX = re.compile(_START + _VERB + r'\s+' + _SKIP + r'(' + _LIMIT + '|' + _RANGE + r')\b')
_ONE_LIMIT = re.compile(r'(?:до|не более|at most|up to|no more than) (?:одного|один|1|one)$')
_ONE = re.compile(_START + _VERB + r'\s+(?:единственн(?:ый|ое|ую)\b|'
                  r'(?:a single|a|an) (?:correct |best )?(?:answer|option|statement)\b)')
_ALL = re.compile(_START + _VERB + r'\s+(?:все|несколько|all|multiple|several|any that apply|'
                  r'(?:(?:верные|неверные|правильные|подходящие)\s+)(?:ответы|варианты|утверждения)|'
                  r'(?:the )?(?:correct|incorrect|applicable|true|false) (?:answers|options|statements))\b')
_RU_OPTIONS = r'(?:(?:верных|правильных) )?(?:ответов|вариантов(?: ответа)?)'
_LABEL_MULTI = re.compile(r'(?:множественный выбор|(?:возмож(?:но|ны)|может быть|здесь может быть)?\s*несколько ' +
                          _RU_OPTIONS + r'|(?:можно|(?:вы )?можете) (?:выбрать|отметить) (?:(?:один или )?несколько|более одного)(?: ' +
                          _RU_OPTIONS + r')?|возможен выбор нескольких (?:вариантов|ответов)(?: ответа)?|'
                          r'(?:вариантов(?: ответа)?|ответов) может быть несколько|'
                          r'multiple[- ](?:select|response)|multi[- ]select|checkboxes|'
                          r'(?:one or more|multiple) (?:answers|options)(?: (?:are|may be|can be) (?:correct|possible|allowed))?|'
                          r'there (?:may|can) be (?:multiple correct answers|more than one correct answer))')
_LABEL_SINGLE = re.compile(r'(?:одиночный выбор|единственный (?:верный |правильный )?(?:ответ|вариант)|'
                           r'(?:только )?один (?:верный |правильный )?(?:вариант(?: ответа)?|ответ)|'
                           r'single[- ](?:select|choice|answer)|(?:only )?one (?:correct )?(?:answer|option))')
_LABEL_EXACT = re.compile(r'^(?:(?:правильных ответов|верных ответов|количество правильных ответов|'
                          r'correct answers?|number of correct answers)\s*[:=—-]\s*(' + _NUMBER + r')\b|'
                          r'(' + _NUMBER + r')\s+(?:(?:верны[хй]|правильны[хй]) (?:ответа|ответов|ответ)|'
                          r'correct (?:answers?|options?))\s*$)')
_BOX = re.compile(r'^\s*(?:[☐☑☒□]|\[\s*[xх✓✔]?\s*\])(?=\s|$)\s*', re.I)
# UI-only instructions can sit between the prompt and answer rows, or below
# them. Preserve content-bearing instructions ("Выберите два свойства JSON").
_CONTROL = re.compile(r'(?:вариант\w*|ответ\w*|утверждени\w*|пункт\w*|option[s]?|answer[s]?|statement[s]?)$')
_FILLERS = set(('все всех варианты вариант ответа ответ ответы варианта вариантов ответов верно верные верных верный верное '
                'правильный правильное правильные правильных неверные неверных подходящие подходящих подходящий '
                'что верно подходят несколько или из предложенных перечисленных подходящее соответствующие '
                'единственный единственное единственную a an можете выбрать выбора выборе количество от одного двух трех четырех минимум списка любые пожалуйста только ровно именно не более менее до как минимум '
                'best may can be correct choose from no than more all that apply applies applicable the any of following correct incorrect true false statements '
                'options option answers answer items are one or more suitable responses response '
                'multiple several up to at least most exactly only').split())


def checkbox_prefix(text):
    """Return the matched checkbox prefix or None; an answer can be a number."""
    return _BOX.match(str(text))


def clean_options(options):
    """Strip corroborated control glyphs or a coherent series of labeled badges."""
    raw = list(options)
    boxes = [checkbox_prefix(s) for s in raw]
    if not all(isinstance(s, str) for s in raw):
        return raw
    if sum(bool(m) for m in boxes) >= 2:
        stripped = [s[m.end():] if m else s for s, m in zip(raw, boxes)]
        if all(stripped) and len(set(stripped)) == len(stripped) and not any(checkbox_prefix(s) for s in stripped):
            raw = stripped
    # OCR can attach a checkbox to the badge ("OA.", "mB.", "oC.", "wD.")
    # or double-print a glyph ("Cc."). Accept these only in a complete ordered
    # series of punctuated markers with a nonempty answer after each.
    if not 2 <= len(raw) <= 8:
        return raw
    for series in ('ABCDEFGH', 'АБВГДЕЖЗ', '12345678'):
        body = []
        for expected, option in zip(series, raw):
            prefix = '' if expected.isdigit() else rf'(?:[oоmw]|{expected})?'
            marker = rf'(?:{prefix}{expected}[.)]|\({expected}\))'
            match = re.match(rf'^{marker}\s+(\S.*)$', option, re.I)
            if not match:
                break
            body.append(match.group(1))
        if len(body) == len(raw) and len(set(s.casefold() for s in body)) == len(raw):
            # Stacked markers can be part of the answers. Do not peel off an
            # additional layer every time a cached frame visits this helper.
            if any(checkbox_prefix(s) or re.match(r'^(?:[A-HАБВГДЕЖЗ1-8][.)]|\([A-HАБВГДЕЖЗ1-8]\))\s', s, re.I)
                   for s in body):
                return raw
            return body
    return raw


def _count(value):
    return int(value) if value.isdigit() else _NUMBERS[value]


def _hints(text):
    hints = []
    flexible_starts = set()
    for match in _FLEX.finditer(text):
        flexible_starts.add(match.start())
        hints.append(('single', 1) if _ONE_LIMIT.fullmatch(match.group(1)) else ('multiple', None))
    count_matches = [m for m in _COUNT.finditer(text) if m.start() not in flexible_starts]
    question_count = _QUESTION_COUNT.match(text)
    if question_count:
        count_matches.append(question_count)
    for match in count_matches:
        n = _count(match.group(1))
        hints.append(('single' if n == 1 else 'multiple', n))
    if _ONE.search(text):
        hints.append(('single', 1))
    if _ALL.search(text):
        hints.append(('multiple', None))
    # A standalone label may also be appended after the question.
    for segment in re.split(r'[?!.]', text):
        label_text = segment.strip(' ()')
        if _LABEL_MULTI.fullmatch(label_text):
            hints.append(('multiple', None))
        if _LABEL_SINGLE.fullmatch(label_text):
            hints.append(('single', 1))
        exact = _LABEL_EXACT.fullmatch(label_text)
        if exact:
            n = _count(exact.group(1) or exact.group(2))
            hints.append(('single' if n == 1 else 'multiple', n))
    return hints


def instruction_only(text):
    """Identify short mode labels or directives that can be excluded from OCR rows."""
    text = _plain(text)
    if not text or '?' in text or not _hints(text):
        return False
    if _LABEL_EXACT.fullmatch(text) or _LABEL_MULTI.fullmatch(text) or _LABEL_SINGLE.fullmatch(text):
        return True
    tokens = re.findall(r'[\w]+', text)
    if tokens and re.fullmatch(_VERB, tokens[0]):
        tokens = tokens[1:]
    return all(t in _FILLERS or t in _NUMBERS or t.isdigit() or _CONTROL.fullmatch(t) for t in tokens)


def selection_hint(question, options=(), instructions=()):
    """Resolve visible cues. A quiz without cues defaults to single."""
    hints = _hints(_plain(question))
    if isinstance(instructions, str):
        instructions = [instructions]
    for text in instructions:
        hints.extend(_hints(_plain(text)))
    if sum(bool(checkbox_prefix(t)) for t in options) >= 2:
        hints.append(('multiple', None))
    modes = {mode for mode, _ in hints}
    counts = {n for _, n in hints if n is not None}
    if len(modes) > 1 or len(counts) > 1 or any(n < 1 or n > (len(options) or 8) for n in counts):
        return {'selectionMode': 'unknown', 'expectedCount': None}
    mode = next(iter(modes), 'single')
    return {'selectionMode': mode, 'expectedCount': next(iter(counts), 1 if mode == 'single' else None)}


def effective_hint(hint, override='auto'):
    """Resolve the explicit user control before requesting answers."""
    if override == 'single':
        return {'selectionMode': 'single', 'expectedCount': 1}
    if override == 'multiple':
        count = hint.get('expectedCount') if hint.get('selectionMode') == 'multiple' else None
        return {'selectionMode': 'multiple', 'expectedCount': count}
    if override != 'auto':
        raise ValueError('Unknown selection override')
    return {key: hint[key] for key in ('selectionMode', 'expectedCount')}
