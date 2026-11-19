"""Spatial OCR regressions for animated, numbered quiz cards."""
import unittest

from ocr_layout import ocr_texts


def part(text, x, y=0, w=7, h=14):
    return dict(text=text, x=x, y=y, w=w, h=h)


def option(text):
    return [part(text, 20, w=150)]


def read(question, options=None):
    return ocr_texts([question, *(options or [option(f"X{i}") for i in range(4)])])


class Layout(unittest.TestCase):
    def test_mixed_badges_and_wrap_in_correct_order(self):
        question = option("Какое утверждение о Recall и Precision неверно?")
        options = [
            option("1 Recall учитывает пропуски"),
            [part("Precision учитывает ложные", 33, w=175), part("2", 8, y=7),
             part("тревоги", 33, y=18, w=51)],
            option("3 Recall всегда равен Precision"),
            option("4 Precision = TP / (TP + FP)"),
        ]
        self.assertEqual(read(question, options), [question[0]["text"], "Recall учитывает пропуски",
            "Precision учитывает ложные тревоги", "Recall всегда равен Precision", "Precision = TP / (TP + FP)"])
        # Vision may merge the badge with the lower line when reading a wrapped option.
        options[1] = [part("Precision учитывает ложные", 33, w=175), part("2 тревоги", 8, y=18, w=75)]
        self.assertEqual(read(question, options)[2], "Precision учитывает ложные тревоги")
        self.assertEqual(read(question, options)[3], "Recall всегда равен Precision")

    def test_preserves_numeric_options_and_genuine_inner_digits(self):
        for texts in [
            ["1", "2", "3", "4"],
            ["1 / 2", "2 / 3", "3 / 4", "4 / 5"],
            ["1 час", "2 часа", "3 месяца", "4 дня"],
            ["1.0", "2.1", "3.2", "4.3"],
            ["C1", "CO2", "3D", "4K"],
        ]:
            self.assertEqual(read(option("Сколько?"), list(map(option, texts)))[1:], texts)
        quantities = [[part(str(i), 0), part("кг", 30)] for i in range(1, 5)]
        self.assertEqual(read(option("Масса?"), quantities)[1:], [f"{i} кг" for i in range(1, 5)])
        for bodies in (["год", "месяца", "дня", "часа"], ["файл", "каталога", "слова", "больших тома"]):
            opts = [[part(str(i), 0), part(word, 35)] for i, word in enumerate(bodies, 1)]
            self.assertEqual(read(option("Что следует выбрать?"), opts)[1:],
                             [f"{i} {word}" for i, word in enumerate(bodies, 1)])
        formulas = [f"({i}) x + y" for i in range(1, 5)]
        self.assertEqual(read(option("Укажите выражение?"), list(map(option, formulas)))[1:], formulas)
        numeric = [[part(str(i), 0), part(s, 30)] for i, s in enumerate(["4", "1 / 2", "0,75", "F1"], 1)]
        self.assertEqual(read(option("Ответ?"), numeric)[1:], ["4", "1 / 2", "0,75", "F1"])
        mixed = [[part(str(i), 1), part(t, 28, w=200)] for i, t in enumerate(
            ["Шаг 1 процесса", "Осталось 2 секунды", "3 тысячи", "4 / 9"], 1)]
        self.assertEqual(read(option("Что значит 2?"), mixed)[1:], ["Шаг 1 процесса", "Осталось 2 секунды", "3 тысячи", "4 / 9"])

    def test_badges_must_corroborate_each_other(self):
        opts = [[part("1", 4), part("попытка", 48)], option("другое"), option("нет"), option("да")]
        self.assertEqual(read(option("Что?"), opts)[1], "1 попытка")
        opts[:3] = [[part(str(i), 4), part(word, 48)] for i, word in enumerate(["Раз", "Два", "Три"], 1)]
        opts[3] = option("Потеряно 4 раза")
        self.assertEqual(read(option("Что?"), opts)[1:], ["Раз", "Два", "Три", "Потеряно 4 раза"])

    def test_clock_and_counter_edges(self):
        question = part("Как изменится значение через 10 с?", 5, y=45, w=315, h=18)
        for clock in ("19 с", "00:18", "3 seconds"):
            with self.subTest(clock=clock):
                self.assertEqual(read([part(clock, 315, w=34, h=10), question])[0], question["text"])
                self.assertEqual(read([part("Вопрос 3 из 6", 0, w=86, h=12), part(clock, 315, w=32, h=10), question])[0], question["text"])
                self.assertEqual(read([question, part(clock, 315, y=80, w=34, h=10)])[0], question["text"])
        self.assertEqual(read([part("Осталось: 20", 0, w=90), question])[0], question["text"])
        self.assertEqual(read([part("Вопрос 3 из 6", 0, w=80)])[0], "Вопрос 3 из 6")

    def test_ambiguous_times_are_preserved(self):
        question = part("Чему равна задержка при T =", 5, y=0, w=200)
        self.assertEqual(read([question, part("10 с", 5, y=20)])[0], "Чему равна задержка при T = 10 с")
        self.assertEqual(read([part("00:19", 2), part("Во сколько начался тест?", 3, y=25, w=200)])[0],
                         "00:19 Во сколько начался тест?")
        self.assertEqual(read([part("19", 215, w=12, h=9), part("Какое число на схеме?", 0, y=24, w=120)])[0],
                         "19 Какое число на схеме?")
        opts = list(map(option, ["19 с", "00:18", "Вопрос 2 из 4", "Осталось: 20"]))
        self.assertEqual(read(option("Что?"), opts)[1:], [v[0]["text"] for v in opts])

    def test_variable_manual_fields_and_markers(self):
        for count in range(2,9):
            fields = [f'Поле {i}' for i in range(1, count+1)]
            options = [option(f'{i}) {v}') for i,v in enumerate(fields,1)]
            self.assertEqual(read(option('Что подходит?'), options),['Что подходит?',*fields])
        values = ['1 час','2 дня','3 недели','4 месяца','5 лет','6 мин']
        self.assertEqual(read(option('Сколько?'),list(map(option,values)))[1:],values)
