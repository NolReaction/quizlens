"""Exact, source-grounded checks for percentages in single-choice questions."""
import re
from fractions import Fraction
from typing import NamedTuple

from ocr_terms import latin_visual_key


class Ratio(NamedTuple):
    metric: str
    numerator: int
    denominator: int
    negated: bool = False

    @property
    def percent(self):
        return Fraction(100 * self.numerator, self.denominator)

    def accepts(self, percentage):
        return (percentage == self.percent) != self.negated


_PERCENT = re.compile(r"(\d{1,12}(?:[.,]\d{1,12})?)\s*%")
_METRICS = {"accuracy": "accuracy", "precision": "precision", "recall": "recall",
            "полнота": "recall", "конверсия": "conversion", "conversion": "conversion"}
_REQUEST = re.compile(
    r"\b(?:чему (?:не )?рав(?:ен|на)|каков[а]?|рассчитайте|вычислите|what (?:is|was)(?: its| the)?|calculate(?: the)?)\s+"
    r"([^\W\d_]+)(?: rate| посетителей в покупателей)?\s*[?.!]*$")
_N = r"(\d{1,12})"
# Match the entire scenario. Extra conditions, class weights or comparisons must
# never be discarded just because the same two numbers appear in a worked example.
_MAJORITY = re.compile(
    r"из " + _N + r" клиент(?:ов|а)? (ушли|ушло|остались|осталось) " + _N
    + r" (?:модель|классификатор) (?:предсказал[аи]?|предсказывает) всем(?: клиентам)? (останется|уйдет|уйдут|останутся)")
_PREDICTIONS = re.compile(
    r"из " + _N + r" прогнозов " + _N + r" (?:оказались|были) (ошибочными|верными|правильными)")
_VISITORS = re.compile(
    r"(?:магазин|сайт) посетили " + _N + r" (?:человек|посетителей) покупку (?:сделали|совершили) "
    + _N + r"(?: человек| посетителей)?")
_ALERTS = re.compile(
    r"модель (?:выдала|показала) " + _N + r" предупреждений (?:из них " + _N
    + r" верны|верными оказались " + _N + r")")
_COUNTS = re.compile(r"\b(tp|tn|fp|fn)\s*=\s*(\d{1,12})\b")

# A known general definition can support a calculation with new input values.
# Only attach the actual definition to the result, not figures from a different example.
_DEFINITIONS = {
    "accuracy": (r"(?:Accuracy\s+(?:показывает|измеряет) долю (?:всех объектов, на которых прогноз совпал с фактом|"
                 r"(?:правильных|верных) (?:ответов|прогнозов))|accuracy\s*=\s*correct predictions\s*/\s*(?:all|total) predictions)\b[.]?"),
    "precision": r"Precision\s*=\s*TP\s*/\s*\(\s*TP\s*\+\s*FP\s*\)",
    "recall": r"Recall\s*=\s*TP\s*/\s*\(\s*TP\s*\+\s*FN\s*\)",
    "conversion": r"число покупателей делят на число посетителей и умножают на 100\s*%",
}
# Match a labelled, explicitly evaluated ratio. Allow a decimal fraction before
# the author's percentage (e.g. '30 / 40 = 0,75, или 75 %').
_WORKED = re.compile(
    r"\b(accuracy|precision|recall|полнота|конверсия|conversion)(?: rate)?\s*"
    r"(?:равна|равен|составляет|is|equals|=|:)\s*" + _N + r"\s*/\s*" + _N
    + r"(?:\s*[×*·]\s*100\s*%?)?\s*=\s*(?:0[.,]\d+\s*,?\s*(?:или|or)\s*)?"
    + _PERCENT.pattern, re.I)


# Some flat OCR results keep the quiz title, progress counter or language menu.
# Only remove a title corroborated by a selected document heading. Plain prose
# or numbers between the header and scenario remain part of the question.
_QUIZ_HEADER = re.compile(
    r"^(?P<title>(?:[^\W\d_]+\s+){0,7}?)(?:"
    r"(?:вопрос|question)\s+\d{1,3}\s*(?:из|of|/)\s*(?:\d{1,3}|[бзoо])"
    r"|(?:один ответ|single answer)\s+(?:[уv~]\s+)?(?:русский|english)\s*(?:[~v]\s*)?"
    r"\d{1,3}\s*(?:секунд[ауы]?|сен|сек|seconds?|[сcs]))\s+", re.I)


def without_quiz_header(question, rows):
    header = _QUIZ_HEADER.match(question)
    if not header:
        return question
    title = header["title"].strip().casefold()
    if title:
        headings = (row["text"].split("\n", 1)[0].lstrip("# ").casefold() for row in rows if "question" not in row)
        if not any(heading == title or heading.startswith(title + " ") for heading in headings):
            return question
    return question[header.end():]


def unreadable_metric(question):
    request = _REQUEST.search(question.casefold())
    if not request or request[1] in _METRICS:
        return False
    # A visual look-alike alone cannot confirm the requested metric without pixels.
    word = request[1]
    key = latin_visual_key(word)
    return any(len(metric) == len(key) and key[:2] == metric[:2] and
               sum(a != b for a, b in zip(key, metric)) <= 1
               for metric in ("accuracy", "precision", "recall", "conversion"))


def question_ratio(question):
    text = " ".join(question.casefold().replace("ё", "е").split())
    request = _REQUEST.search(text)
    if not request or request[1] not in _METRICS:
        return None
    metric = _METRICS[request[1]]
    negated = request[0].startswith("чему не рав")
    body = text[:request.start()].strip()
    counts = _COUNTS.findall(body)
    if counts and not _COUNTS.sub("", body).strip(" .,:;?!\t"):
        labels = {key: int(amount) for key, amount in counts}
        if len(labels) != len(counts):
            return None
        required = {"precision": {"tp", "fp"}, "recall": {"tp", "fn"},
                    "accuracy": {"tp", "tn", "fp", "fn"}}.get(metric)
        if not required or not required <= labels.keys():
            return None
        numerator = labels["tp"] + (labels["tn"] if metric == "accuracy" else 0)
        denominator = sum(labels[key] for key in required)
    else:
        # Punctuation may separate clauses, but decimal or signed counts are never integers.
        if re.search(r"\d[.,]\d", body):
            return None
        body = " ".join(re.sub(r'[.,:;!?«»“”"]', ' ', body).split())
        if metric == "accuracy" and (match := _MAJORITY.fullmatch(body)):
            total, observed, count, predicted = match.groups()
            denominator, count = int(total), int(count)
            same_class = observed.startswith("уш") == predicted.startswith("уй")
            numerator = count if same_class else denominator - count
        elif metric == "accuracy" and (match := _PREDICTIONS.fullmatch(body)):
            total, count, label = match.groups()
            denominator = int(total)
            numerator = denominator - int(count) if label == "ошибочными" else int(count)
        elif metric == "precision" and (match := _ALERTS.fullmatch(body)):
            denominator = int(match[1])
            numerator = int(match[2] or match[3])
        elif metric == "conversion" and (match := _VISITORS.fullmatch(body)):
            denominator, numerator = map(int, match.groups())
        else:
            return None
    return Ratio(metric, numerator, denominator, negated) if denominator and 0 <= numerator <= denominator else None


def percentage(option):
    match = _PERCENT.fullmatch(option.strip())
    return Fraction(match[1].replace(",", ".")) if match else None


def check_evidence(ratio, passages):
    """Return (row, excerpt, conflict); an inconsistent worked example beats a definition."""
    support = None
    for row, passage in passages:
        if "question" in row:
            continue
        text = passage["text"]
        for match in _WORKED.finditer(text):
            metric, numerator, denominator, stated = match.groups()
            if (_METRICS[metric.casefold()], int(numerator), int(denominator)) != (ratio.metric, ratio.numerator, ratio.denominator):
                continue
            if Fraction(stated.replace(",", ".")) != ratio.percent:
                return row, text, True
            support = (row, text, False)
        if support is None:
            definition = re.search(_DEFINITIONS[ratio.metric], text, re.I)
            if definition:
                support = (row, definition.group(), False)
    return support


def contradicts_worked_ratio(question, selected, excerpt):
    """Retain a veto for less structured positive questions with the same direct operands."""
    chosen = percentage(selected)
    if chosen is None:
        return False
    metrics = {_METRICS[m.casefold()] for m in re.findall(
        r"\b(?:accuracy|precision|recall|полнота|конверсия|conversion)\b", question, re.I)}
    if not metrics and re.search(r"\bконверси\w*\b", question, re.I):
        metrics = {"conversion"}
    if len(metrics) != 1:
        return False
    # Exclude derived quantities here; the input parser handles complements only with known class roles.
    numbers = re.findall(r"[-+−]?\d+(?:[.,]\d+)?(?:\s*%)?", question)
    if len(numbers) != 2 or not all(n.isdigit() for n in numbers):
        return False
    matching = [m for m in _WORKED.finditer(excerpt) if _METRICS[m[1].casefold()] in metrics
                and sorted((m[2], m[3])) == sorted(numbers)]
    if len(matching) != 1 or int(matching[0][3]) == 0:
        return False
    match = matching[0]
    calculated = Fraction(100 * int(match[2]), int(match[3]))
    return calculated != chosen or calculated != Fraction(match[4].replace(",", "."))
