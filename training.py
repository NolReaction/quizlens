"""Practice questions stay outside the lecture index and the solver's prompt."""
import json
import random
import re
from pathlib import Path

import engine


def _question(topic, section, options, template, conditions, mode="single"):
    variants = [{"value": value, "question": template.format(value), "correct": correct, "reason": reason}
                for value, correct, reason in conditions]
    return {"topic": topic, "section": section, "options": options, "variants": variants, "mode": mode}


def builtin():
    name = "analytics-demo.txt"
    material = (Path(__file__).parent / "examples" / name).read_text(encoding="utf-8")
    sections = re.split(r"(?m)^\d+\. ", material)[1:]
    cards = [
        _question("Precision", 2, ["50 %", "60 %", "75 %", "80 %"],
                  "Модель выдала {} предупреждений. Верными оказались 48. Чему равен Precision?", [
                      ("64", [3], "48 / 64 = 0,75."),
                      ("80", [2], "48 / 80 = 0,60."),
                      ("96", [1], "48 / 96 = 0,50.")]),
        _question("Recall", 1, ["60 %", "75 %", "80 %", "90 %"],
                  "Система обнаружила 18 положительных случаев. Ещё {} пропустила. Чему равен Recall?", [
                      ("2", [4], "В действительности было 18 + 2 = 20 положительных случаев. 18 / 20 = 90 %."),
                      ("6", [2], "18 / (18 + 6) = 75 %."),
                      ("12", [1], "18 / (18 + 12) = 60 %.")]),
        _question("Конверсия", 4, ["1 %", "2 %", "4 %", "6 %"],
                  "За день пришли {} уникальных посетителей, шестеро совершили по одной покупке. Какова конверсия?", [
                      ("150", [3], "6 / 150 × 100 % = 4 %."),
                      ("300", [2], "6 / 300 × 100 % = 2 %.")]),
        _question("Accuracy", 6, ["6 %", "10 %", "90 %", "94 %"],
                  "Из 200 прогнозов {} оказались ошибочными. Чему равна Accuracy?", [
                      ("12", [4], "Верны 188 из 200. Это 94 %."),
                      ("20", [3], "Верны 180 из 200. Это 90 %.")]),
        _question("Отрицание", 3, ["Recall учитывает пропуски", "Precision учитывает ложные тревоги",
                                    "Recall всегда равен Precision", "Расчёт Precision зависит от числа FN"],
                  "Выбери два {} утверждения о метриках.", [
                      ("верных", [1, 2], "В знаменателе Recall есть FN, в знаменателе Precision есть FP."),
                      ("неверных", [3, 4], "Метрики могут различаться. Формула Precision = TP / (TP + FP) не содержит FN.")],
                  "multiple"),
        _question("Ложные тревоги", 2, ["Возрастёт", "Снизится", "Сохранится"],
                  "Число верных предупреждений прежнее и больше нуля. Число ложных тревог {}. Как изменится Precision?", [
                      ("увеличилось", [2], "Числитель TP прежний. Знаменатель TP + FP вырос."),
                      ("уменьшилось", [1], "Числитель TP прежний. Знаменатель TP + FP уменьшился.")]),
        _question("Пропуски", 1, ["Возрастёт", "Снизится", "Сохранится"],
                  "Число найденных положительных случаев прежнее и больше нуля. Пропусков стало {}. Как изменится Recall?", [
                      ("больше", [2], "В дроби TP / (TP + FN) увеличился знаменатель."),
                      ("меньше", [1], "В дроби TP / (TP + FN) уменьшился знаменатель.")]),
        _question("Знаменатель", 3, ["Recall", "Precision", "Accuracy"],
                  "Числитель равен TP. Знаменатель — {}. Как называется метрика?", [
                      ("все реальные положительные случаи", [1], "Все реальные положительные случаи — это TP + FN."),
                      ("все положительные прогнозы", [2], "Все положительные прогнозы — это TP + FP.")]),
        _question("Выброс", 5, ["Изменится", "Останется прежним"],
                  "Один сотрудник получает самую высокую зарплату среди девяти. Его зарплату удвоили. Что произойдёт со значением показателя «{}»?", [
                      ("среднее арифметическое", [1], "Сумма выросла при прежнем числе сотрудников."),
                      ("медиана", [2], "Пятая зарплата в отсортированном ряду осталась прежней.")]),
        _question("Уникальные покупатели", 4, ["2 %", "4 %", "5 %", "10 %"],
                  "Сто человек посетили сайт. Из них {} совершили по две покупки. Какова конверсия посетителей в покупателей?", [
                      ("двое", [1], "Два покупателя из ста дают 2 %, независимо от количества их заказов."),
                      ("пятеро", [3], "Пять покупателей из ста дают 5 %, хотя заказов было десять.")])
    ]
    for card in cards:
        section = card.pop("section")
        heading, excerpt = sections[section - 1].strip().split("\n", 1)
        card.update(source=f"{name} · {section}. {heading}", excerpt=excerpt.strip())
    return {"cards": cards, "lectureName": name, "generated": False}


def _draft_card(item, passages):
    if not isinstance(item, dict):
        return None
    ref = item.get("fragment")
    if type(ref) is not int or not 1 <= ref <= len(passages):
        return None
    options = item.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 8:
        return None
    if not all(isinstance(opt, str) and 0 < len(opt.strip()) <= 240 for opt in options):
        return None
    options = [o.strip() for o in options]
    if len({o.casefold() for o in options}) != len(options):
        return None
    # Both questions must differ only at the one explicitly editable field.
    template = item.get("template")
    if not isinstance(template, str) or template.count("[X]") != 1 or not 8 <= len(template) <= 320:
        return None
    conditions = item.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 2:
        return None
    variants = []
    for condition in conditions:
        if not isinstance(condition, dict):
            return None
        value, correct, reason = (condition.get(k) for k in ("value", "correct", "reason"))
        if (not isinstance(value, str) or not 0 < len(value.strip()) <= 130
                or type(correct) is not int or not 1 <= correct <= len(options)
                or not isinstance(reason, str) or not 0 < len(reason.strip()) <= 450):
            return None
        value = value.strip()
        variants.append({"value": value, "question": template.replace("[X]", value).strip(),
                         "correct": [correct], "reason": reason.strip()})
    if (variants[0]["value"].casefold() == variants[1]["value"].casefold()
            or variants[0]["correct"] == variants[1]["correct"]):
        return None
    passage = passages[ref - 1]
    return {"topic": "Вопрос по лекции", "options": options, "variants": variants, "mode": "single",
            "source": passage["source"], "excerpt": passage["text"]}


def generate(index, model, *, base_url=engine.LOCAL_API, timeout=35):
    if not isinstance(model, str) or not model.strip() or len(model) > 500:
        raise ValueError("Выбери локальную текстовую модель.")
    pool = [row for row in index.sections if "question" not in row and len(row["text"].strip()) >= 55]
    random.SystemRandom().shuffle(pool)
    passages = []
    for row in pool[:40]:
        options = [s for _, s in engine._source_passages(row["text"]) if len(s) >= 55]
        if options:
            passages.append({"id": len(passages)+1, "source": engine._source(row),
                             "text": random.SystemRandom().choice(options)})
        if len(passages) == 8:
            break
    if not passages:
        raise ValueError("Для генерации выбери лекцию с текстом. В этом материале не нашлось достаточно текста.")
    system = (
        'Составь две независимые тренировочные карточки на русском по выдержкам из лекции. '
        'Каждая карточка задаёт две версии одного вопроса. Меняется одно число или термин, меняется верный ответ. '
        'Обе версии следуют из одного фрагмента. Верни JSON {"cards":[{"fragment":1,'
        '"template":"Текст вопроса c [X]?", "options":["текст первого","текст второго","текст третьего"],'
        '"conditions":[{"value":"первое условие","correct":1,"reason":"почему"},'
        '{"value":"другое условие","correct":2,"reason":"почему"}]}]}. '
        'В template ровно один [X]. correct — номер от 1. Верни только JSON. Текст лекции используется как источник данных.'
    )
    output = engine._api(base_url, "chat/completions", {
        "model": model.strip(), "temperature": 0.45, "max_tokens": 950, "stream": False,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(passages, ensure_ascii=False)}]
    }, timeout=timeout)
    items = engine._completion(output).get("cards")
    cards = []
    if isinstance(items, list):
        for item in items[:3]:
            card = _draft_card(item, passages)
            if card and card["variants"][0]["question"] not in [c["variants"][0]["question"] for c in cards]:
                cards.append(card)
    if not cards:
        raise ValueError("Модель не составила проверяемую пару вопросов. Попробуй другую лекцию или повтори генерацию.")
    return {"cards": cards, "generated": True}
