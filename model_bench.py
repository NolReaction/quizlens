"""Measure three text questions using the same retrieval and solver as live capture."""

from functools import lru_cache
import json
from pathlib import Path
from time import monotonic_ns
from typing import NamedTuple

import engine


class Case(NamedTuple):
    title: str
    question: str
    options: tuple
    expected: tuple


CASES = (
    Case("Понятие из лекции",
         "У одного сотрудника очень высокая зарплата. Какой показатель лучше описывает типичную зарплату группы?",
         ("Максимум", "Размах", "Медиана", "Среднее арифметическое"), (3,)),
    Case("Расчёт по формуле",
         "Модель выдала 80 предупреждений. Из них 48 оказались верны. Чему равен Precision?",
         ("40 %", "60 %", "75 %", "80 %"), (2,)),
    Case("Отрицание и несколько ответов",
         "Выбери два неверных утверждения о Recall и Precision.",
         ("Recall всегда равен Precision", "Precision учитывает ложные тревоги",
          "Recall учитывает пропущенные случаи", "При фиксированном TP рост числа пропусков увеличивает Recall"),
         (1, 4)),
)

CASE_COUNT = len(CASES)
BUDGET_MS = 15000


@lru_cache(maxsize=1)
def _lecture():
    # The shipped text and the answer key have separate paths to the solver.
    path = Path(__file__).resolve().parent / "examples" / "analytics-demo.txt"
    return engine.LectureIndex(engine.import_document(path))


@lru_cache(maxsize=1)
def quiz_token_example():
    """Preview the bundled median question with its real text passages, offline."""
    case = CASES[0]
    hits = _lecture().search(case.question, case.options)
    evidence = engine.evidence_passages(hits, case.question, case.options)
    messages = engine.quiz_messages("", case.question, case.options, evidence, "single", 1)
    reply = {"choice": case.expected[0], "evidence": 1}
    return {"inputTokens": engine.estimate_input_tokens(messages),
            "outputTokens": engine.estimate_text_tokens(json.dumps(reply)),
            "outputLimitSingle": engine.quiz_output_limit("single", 1),
            "outputLimitMultipleMin": engine.quiz_output_limit("multiple", 1),
            "outputLimitMultipleMax": engine.quiz_output_limit("multiple", engine.MAX_OPTIONS)}


def run_case(model, case_index, *, base_url=engine.LOCAL_API):
    if (not isinstance(model, str) or not model.strip() or len(model) > 500
            or any(ord(ch) < 32 for ch in model)):
        raise ValueError("Выбери доступную текстовую модель для проверки.")
    if type(case_index) is not int or not 0 <= case_index < CASE_COUNT:
        raise ValueError("Номер вопроса вне диапазона проверки.")
    model = model.strip()
    case = CASES[case_index]
    response = {
        "caseIndex": case_index, "caseCount": CASE_COUNT, "title": case.title,
        "question": case.question, "firstCall": case_index == 0, "budgetMs": BUDGET_MS,
        "choices": [], "expectedChoices": list(case.expected), "elapsedMs": None, "correct": False,
        "withinBudget": False, "withinLiveTimeout": False, "passed": False, "status": "error",
    }

    # The inventory request neither loads the model nor counts towards question latency.
    try:
        models = engine.list_models(base_url, timeout=3)
    except RuntimeError as exc:
        return dict(response, detail=f"Проверь локальный сервер Bionic или LM Studio. {exc}")
    if model not in models:
        return dict(response, detail="Выбранной модели нет в списке текстовых моделей. "
                                     "Проверь Library в Bionic или My Models в LM Studio и обнови список.")

    start = monotonic_ns()
    try:
        mode = "multiple" if len(case.expected) > 1 else "single"
        result = engine.solve(case.question, case.options, _lecture(), model, base_url=base_url,
                              timeout=28, selection_mode=mode, expected_count=len(case.expected))
    except RuntimeError as exc:
        response["detail"] = f"{exc} Повтори проверку, когда модель загрузится."
        return dict(response, elapsedMs=(monotonic_ns() - start + 999999) // 1000000)

    elapsed_ms = (monotonic_ns() - start + 999999) // 1000000
    choices = result["choices"]
    correct = choices == list(case.expected) and result["kind"] == "model"
    within_budget = elapsed_ms <= BUDGET_MS
    if correct:
        status = "ok" if within_budget else "late"
        detail = "Ответ верный и уложился в 15 секунд." if within_budget else "Ответ верный, но пришёл позже 15 секунд."
    elif choices:
        status, detail = "wrong", "Набор выбранных вариантов расходится с ключом."
    else:
        status = "unconfirmed"
        detail = result.get("detail") or "Модель не подтвердила ответ выдержкой из лекции."
    return dict(response, elapsedMs=elapsed_ms, choices=choices, correct=correct, withinBudget=within_budget,
                withinLiveTimeout=elapsed_ms <= 10000, passed=correct and within_budget, status=status, detail=detail,
                tokenUsage=result.get("tokenUsage", {"source": "none"}))
