"""Measure OCR on generated quiz frames with independently specified text and geometry.

Run with Pillow, DejaVu Sans and Russian/English Tesseract (Apple Vision on Mac):
    python tests/vision_benchmark.py --json vision-report.json
"""
import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.request import ProxyHandler, Request, build_opener

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    options: tuple
    columns: int = 1
    dark: bool = False
    scale: float = 1.0
    colored_cards: bool = False
    uniform_cards: bool = False


# Ground truth is known before drawing. Never send this text to the runtime.
CASES = (
    Case("decimal", "Какой процент равен пяти тысячным?", ("0,05 %", "5 %", "0,5 %", "50 %")),
    Case("negation", "Что НЕ относится к расходам?", ("Зарплата", "Выручка", "Аренда", "Доставка"), dark=True),
    Case("signed", "Как изменился баланс за день?", ("−12,5 %", "+12,5 %", "−1,25 %", "+1,25 %"), scale=.85),
    Case("operators", "Как записать значение ниже порога?", ("x > 12", "x = 12", "x < 12", "x ≥ 12"), dark=True),
    Case("grid", "Сколько минут длится один час?", ("45 минут", "90 минут", "60 минут", "30 минут"), columns=2),
    Case("reordered-grid", "Сколько минут длится один час?", ("60 минут", "30 минут", "45 минут", "90 минут"), columns=2),
    Case("units", "Какое измерение совпадает с одним метром?", ("10 см", "100 мм", "1,0 м", "0,1 м"), scale=.8),
    Case("colored-cards", "Один из этих приборов измеряет\nтемпературу воздуха на улице\nв тени.",
         ("Барометр", "Термометр", "Вольтметр", "Анемометр"), colored_cards=True),
    Case("uniform-cards", "Как называется прибор для измерения силы тока?",
         ("Термометр", "Амперметр", "Барометр", "Динамометр"), uniform_cards=True),
)


def png_url(image):
    png = io.BytesIO()
    image.save(png, "PNG")
    return "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")


def render_colored_cards(case, phase, font):
    """Render a dark tab with widely separated answers of mixed text polarity."""
    width, height = 1920, 1000
    image = Image.new("RGB", (width, height), "#202a42")
    draw = ImageDraw.Draw(image)
    body = ImageFont.truetype(font, 19)
    small = ImageFont.truetype(font, 16)
    foreground = "#fcfafa"
    left, top = 646 + 2 * phase, 244 + phase
    center = left + 312
    draw.text((left, 28), "Игрок", font=small, fill=foreground)
    draw.text((left + 624, 28), "120 баллов", anchor="ra", font=small, fill="#a5b6d5")
    draw.text((center, 91), "Вопрос 3 из 8", anchor="mm", font=small, fill="#a5b6d5")
    draw.text((center, 127), "24 сек" if phase == 0 else "9 сек", anchor="mm", font=body, fill="#f1ad43")
    draw.multiline_text((center, 158), case.question, anchor="ma", align="center", spacing=4,
                        font=body, fill=foreground)
    boxes = [(left, 151, 624, 79)]
    colors = ("#cf2158", "#2755ba", "#f3a717", "#15745f")
    for i, (option, color) in enumerate(zip(case.options, colors)):
        y = top + i * 168
        draw.rounded_rectangle((left, y, left + 624, y + 156), radius=14, fill=color)
        x, mid = left + 27, y + 78
        ink = "#131e31" if i == 2 else foreground
        if i == 0:
            draw.polygon([(x, mid - 8), (x - 8, mid + 7), (x + 8, mid + 7)], fill=ink)
        elif i == 1:
            draw.polygon([(x, mid - 8), (x - 8, mid), (x, mid + 8), (x + 8, mid)], fill=ink)
        elif i == 2:
            draw.ellipse((x - 6, mid - 6, x + 6, mid + 6), fill=ink)
        else:
            draw.rectangle((x - 7, mid - 7, x + 7, mid + 7), fill=ink)
        draw.text((left + 47, mid), option, anchor="lm", font=body, fill=ink)
        boxes.append((left + 43, mid - 18, 528, 36))
    regions = [dict(x=x/width, y=y/height, w=w/width, h=h/height) for x, y, w, h in boxes]
    return png_url(image), regions


def render_uniform_cards(case, phase, font):
    width, height = 1920, 960
    image = Image.new("RGB", (width, height), "#3a454b")
    draw = ImageDraw.Draw(image)
    body = ImageFont.truetype(font, 21)
    small = ImageFont.truetype(font, 16)
    left, top = 455 + 2*phase, 252 + phase
    draw.text((22, 18), f"Вопрос {3+phase} из 8", font=small, fill="white")
    draw.text((left, 157), case.question, font=body, fill="white")
    draw.text((left, 208), "Задание по физике", font=small, fill="white")
    draw.rounded_rectangle((left, top, left+595, top+598), 15, fill="#80704e")
    for j in range(6):
        y = top+55+90*j
        draw.ellipse((left+42, y-12, left+545, y+34), fill="#b69756")
        draw.text((left+120, y), "Учебная шкала", font=small, fill="#322534")
    boxes = [(left-2, 150, 620, 38)]
    for i, label in enumerate(case.options):
        x, y = left+607, top+151*i
        draw.rounded_rectangle((x, y, x+324, y+146), 15, fill="#263238")
        draw.text((x+162, y+73), label, anchor="mm", font=body, fill="white")
        boxes.append((x+4, y+42, 316, 60))
    regions = [dict(x=x/width, y=y/height, w=w/width, h=h/height) for x, y, w, h in boxes]
    return png_url(image), regions


def render(case, phase, font):
    """Jitter the card by two pixels and move the timer without changing the answer text."""
    if case.colored_cards:
        return render_colored_cards(case, phase, font)
    if case.uniform_cards:
        return render_uniform_cards(case, phase, font)
    image = Image.new("RGB", (1040, 570), "#18222b" if case.dark else "#fbfcfd")
    draw = ImageDraw.Draw(image)
    fg, muted = ("#f4f6fa", "#d1d9e0") if case.dark else ("#111820", "#40505c")
    bg, border = ("#27343e", "#8c9ba7") if case.dark else ("#f1f5f8", "#acbbc4")
    body = ImageFont.truetype(font, 26)
    small = ImageFont.truetype(font, 18)
    x, y = (46, 68) if phase == 0 else (48, 69)
    draw.text((x, 18), "Вопрос 3 из 8", font=small, fill=muted)
    draw.text((922, 18), "16 с" if phase == 0 else "9 с", font=small, fill=muted)
    draw.rectangle((x, 51, x + (810 if phase == 0 else 430), 54), fill="#758995")
    draw.text((x, y + 16), case.question, font=body, fill=fg)
    width = 944 if case.columns == 1 else 455
    boxes = [(x - 3, y, 954, 66)]
    for i, option in enumerate(case.options):
        row, col = divmod(i, case.columns)
        left, top = x + col * (width + 26), y + 100 + 72 * row
        draw.rounded_rectangle((left, top, left + width, top + 53), 7, fill=bg, outline=border)
        draw.text((left + 12, top + 15), str(i + 1), font=small, fill=muted)
        draw.text((left + 57, top + 10), option, font=body, fill=fg)
        boxes.append((left + 3, top + 3, width - 6, 47))
    draw.text((x, 509), "Выберите один вариант.", font=small, fill=muted)
    regions = [dict(x=x/1040, y=y/570, w=w/1040, h=h/570) for x, y, w, h in boxes]
    if case.scale != 1:
        image = image.resize((round(image.width * case.scale), round(image.height * case.scale)), Image.Resampling.LANCZOS)
    return png_url(image), regions


def plain_text(text):
    text = unicodedata.normalize("NFKC", text).casefold().strip().replace("−", "-")
    return re.sub(r"(?<!\w)[—–](?=\s*\d)", "-", text)


def normalize(text):
    text = plain_text(text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return re.sub(r"\s+", "", text)


def critical(text):
    text = plain_text(text)
    # Spaces around operators are typographic. A missing decimal point, sign,
    # comparison operator, percent or negation changes the meaning.
    return tuple(t.replace(",", ".") for t in re.findall(
        r"\d+(?:[.,]\d+)?|[%+<>=≤≥-]|\b(?:не|нет|без|not)\b", text))


def score(case, response):
    expected = (case.question, *case.options)
    read = response.get("texts", [])
    complete = len(read) == len(expected) and all(read)
    warnings = {w.get("field") for w in response.get("readingWarnings", [])}
    exact = [normalize(a) == normalize(b) and critical(a) == critical(b) for a, b in zip(expected, read)]
    mismatches = [i for i, (a, b) in enumerate(zip(expected, read)) if critical(a) != critical(b)]
    return {"complete": bool(complete), "questionExact": bool(complete and exact[0]),
            "optionsInOrder": bool(complete and all(exact[1:])),
            "criticalExact": bool(complete and not mismatches), "warnedFields": sorted(warnings),
            "unflaggedCriticalErrors": [i for i in mismatches if i not in warnings] if complete else [],
            "texts": read}


@contextmanager
def server(root):
    with tempfile.TemporaryDirectory(prefix="quizlens-vision-benchmark-") as tmp:
        env = {**os.environ, "QUIZ_LENS_LM_URL": "http://127.0.0.1:1/v1", "PYTHONUTF8": "1"}
        process = subprocess.Popen([sys.executable, "-u", "app.py", "--no-browser", "--port", "0", "--data-dir", tmp],
                                   cwd=root, env=env, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            match = re.search(r"http://127\.0\.0\.1:\d+", process.stdout.readline())
            if not match:
                raise RuntimeError("Could not start the local QuizLens service")
            yield match.group()
        finally:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def measure(root, font):
    rows = []
    client = build_opener(ProxyHandler({}))
    with server(root) as url:
        for case in CASES:
            for phase in (0, 1):
                image, regions = render(case, phase, font)
                for mode in ("auto", "regions"):
                    payload = {"image": image}
                    if mode == "regions":
                        payload["regions"] = regions
                    path = "/api/ocr" if mode == "regions" else "/api/detect-layout"
                    request = Request(url + path, data=json.dumps(payload).encode(),
                                      headers={"Content-Type": "application/json"})
                    start = time.monotonic()
                    with client.open(request, timeout=40) as response:
                        result = json.load(response)
                    rows.append({"case": case.name, "phase": phase, "mode": mode,
                                 "ms": round(1000 * (time.monotonic() - start)), **score(case, result)})
    return rows


def summarize(rows):
    times = sorted(row["ms"] for row in rows)
    return {"frames": len(rows),
            "layoutComplete": sum(row["complete"] for row in rows),
            "optionsInOrder": sum(row["optionsInOrder"] for row in rows),
            "questionExact": sum(row["questionExact"] for row in rows),
            "criticalExact": sum(row["criticalExact"] for row in rows),
            "framesWithWarnings": sum(bool(row["warnedFields"]) for row in rows),
            "framesWithUnflaggedCriticalErrors": sum(bool(row["unflaggedCriticalErrors"]) for row in rows),
            "sameReadingAfterTimerChange": sum(bool(a["complete"] and b["complete"] and
                [normalize(s) for s in a["texts"]] == [normalize(s) for s in b["texts"]])
                for a, b in zip(rows[::2], rows[1::2])),
            "medianMs": statistics.median(times), "p95Ms": times[math.ceil(.95 * len(times)) - 1]}


def check_regressions(rows):
    for mode in ("auto", "regions"):
        by_mode = [r for r in rows if r["mode"] == mode]
        for case in ("decimal", "negation", "grid", "reordered-grid"):
            success = [r for r in by_mode if r["case"] == case and r["questionExact"] and r["optionsInOrder"]]
            if not success:
                raise ValueError(f"{mode}/{case}: failed to read an intact question and its options in either frame")
        for item in by_mode:
            if item["case"] in ("decimal", "signed", "operators") and item["unflaggedCriticalErrors"]:
                raise ValueError(f"{mode}/{item['case']}: unchecked reading in fields {item['unflaggedCriticalErrors']}: {item['texts']}")
    for case in ("colored-cards", "uniform-cards"):
        cards = [r for r in rows if r["mode"] == "auto" and r["case"] == case]
        if len(cards) != 2 or not all(r["questionExact"] and r["optionsInOrder"] for r in cards):
            raise ValueError(f"auto/{case}: question and all four choices must survive the layout change: {cards}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="QuizLens checkout to run for comparison")
    parser.add_argument("--check", action="store_true", help="Fail on loss of critical benchmark coverage")
    parser.add_argument("--font", default="DejaVuSans.ttf")
    parser.add_argument("--json", type=Path, help="Write full readings and warnings for individual frames")
    args = parser.parse_args()
    rows = measure(args.root, args.font)
    summary = {mode: summarize([row for row in rows if row["mode"] == mode]) for mode in ("auto", "regions")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "frames": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.check:
        check_regressions(rows)


if __name__ == "__main__":
    main()
