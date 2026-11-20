"""Read a full quiz window or selected text regions on this device."""

import atexit
import base64
import binascii
import functools
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from statistics import median

from auto_layout import assess_layout, colored_card_stack
from ocr_layout import ocr_texts
from ocr_review import field_warnings
from quiz_mode import selection_hint

_ROOT = Path(__file__).resolve().parent
_MAX_BYTES = 8 * 1024 * 1024
_worker = None
_worker_warm = False
_COLD_TIMEOUT = 60
_WARM_TIMEOUT = 12
_STAGES = {
    "startup": "запуск модуля OCR и чтение заголовка",
    "receive": "передача изображения модулю OCR",
    "configure": "инициализация языков Apple Vision",
    "vision": "обработка изображения Apple Vision",
}
_lock = threading.Lock()  # One request at a time on the binary pipe.
_lifecycle = threading.Lock()  # Synchronizes creating a child with server shutdown.
_closing = False
_warmup_thread = None
_warmup_error = ""


def _decode_image(data_url):
    if not isinstance(data_url, str):
        raise ValueError("Кадр должен быть изображением PNG или JPEG.")
    header, separator, content = data_url.partition(",")
    if header not in ("data:image/jpeg;base64", "data:image/png;base64") or not separator:
        raise ValueError("Неизвестный формат кадра. Нужен PNG или JPEG.")
    if not content or len(content) > 4 * ((_MAX_BYTES + 2) // 3):
        raise ValueError("Кадр пуст или превышает 8 МиБ.")
    try:
        image = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Повреждённые данные изображения.") from exc
    signature = b"\xff\xd8\xff" if "jpeg" in header else b"\x89PNG\r\n\x1a\n"
    if not image.startswith(signature) or len(image) > _MAX_BYTES:
        raise ValueError("Повреждённый кадр или неверный формат изображения.")
    return image


def _validate_regions(regions):
    if not isinstance(regions, (list, tuple)) or not 3 <= len(regions) <= 9:
        raise ValueError("Выдели вопрос и от двух до восьми вариантов.")
    cleaned = []
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("Область текста задана неверно.")
        values = [region.get(key) for key in ("x", "y", "w", "h")]
        if not all(type(n) in (int, float) and 0 <= n <= 1 and math.isfinite(n) for n in values):
            raise ValueError("Координаты области текста заданы неверно.")
        x, y, w, h = values
        if w <= 0 or h <= 0 or x + w > 1.00001 or y + h > 1.00001:
            raise ValueError("Область текста выходит за границы кадра.")
        cleaned.append(dict(x=x, y=y, w=min(w, 1-x), h=min(h, 1-y)))
        if cleaned[-1]["w"] == 0 or cleaned[-1]["h"] == 0:
            raise ValueError("Пустая область текста.")
    return cleaned


@functools.lru_cache(maxsize=1)
def _native_binary():
    supplied = Path(os.environ.get("QUIZ_LENS_REGION_OCR", _ROOT / "native" / "RegionOCR"))
    if supplied.is_file() and os.access(supplied, os.X_OK):
        return supplied
    source = _ROOT / "native" / "RegionOCR.swift"
    cache = Path.home() / "Library" / "Caches" / "QuizLens" / "native"
    cache.mkdir(parents=True, exist_ok=True)
    layout_source = _ROOT / "native" / "LayoutFragments.swift"
    digest = hashlib.sha256(source.read_bytes() + b"\0" + layout_source.read_bytes()).hexdigest()[:16]
    binary = cache / ("RegionOCR-" + platform.machine() + "-" + digest)
    if binary.is_file() and os.access(binary, os.X_OK):
        return binary
    if not shutil.which("xcrun"):
        raise RuntimeError("Для сборки OCR установите Command Line Tools командой xcode-select --install.")
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        compiled = Path(directory) / "RegionOCR"
        try:
            target = platform.machine() + "-apple-macos14.0"
            result = subprocess.run(["xcrun", "swiftc", "-O", "-parse-as-library", "-target", target,
                                     str(source), str(layout_source), "-o", str(compiled)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Сборка OCR не завершилась за две минуты. Проверьте установку Xcode.") from exc
        if result.returncode:
            raise RuntimeError("Не удалось собрать OCR. Проверьте Xcode Command Line Tools. "
                               + (result.stdout or "").strip()[-700:])
        compiled.replace(binary)
    return binary


def _stop_worker():
    global _worker, _worker_warm
    process, _worker = _worker, None
    _worker_warm = False
    if process is None:
        return
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    finally:
        process.stdin.close()
        process.stdout.close()


def shutdown():
    global _closing
    with _lifecycle:
        _closing = True
        process = _worker
        if process is not None and process.poll() is None:
            process.kill()  # Interrupt a prewarm or foreground exchange; never wait its cold budget.
    if _lock.acquire(timeout=2):
        try:
            _stop_worker()
        finally:
            _lock.release()


atexit.register(shutdown)


def _exchange_frame(process, image, regions, timeout):
    # Send a short length-prefixed header followed by the exact number of raw image
    # bytes it declares. A persistent child must never wait for EOF after a frame.
    metadata = {"size": len(image), "regions": [] if regions is None else regions}
    if regions is None:
        metadata["layout"] = True
    header = json.dumps(metadata, separators=(",", ":")).encode()
    blocks = iter((memoryview(struct.pack(">I", len(header)) + header), memoryview(image)))
    outgoing = next(blocks)
    reader, writer = process.stdout.fileno(), process.stdin.fileno()
    # Darwin only guarantees an atomic nonblocking pipe write up to PIPE_BUF.
    chunk_size = max(1, os.fpathconf(writer, "PC_PIPE_BUF"))
    deadline = time.monotonic() + timeout
    incoming = bytearray()
    stage = "startup"
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError(f"Истекло время ожидания кадра ({timeout:g} с). Последний этап: {_STAGES[stage]}. "
                               "Повторите проверку OCR. Если ошибка повторится, сохраните название этапа для диагностики.")
        readable, writable, _ = select.select([reader], [writer] if outgoing else [], [], left)
        if writable:
            try:
                count = os.write(writer, outgoing[:chunk_size])
            except BlockingIOError:
                count = 0
            outgoing = outgoing[count:]
            if not outgoing:
                outgoing = next(blocks, None)
        if readable:
            data = os.read(reader, 65_536)
            if not data:
                raise RuntimeError(f"Служба OCR завершилась на этапе «{_STAGES[stage]}». Повторите проверку OCR.")
            incoming.extend(data)
            if len(incoming) > 1024 * 1024:
                raise RuntimeError("Служба OCR вернула слишком большой ответ.")
            while b"\n" in incoming:
                line, _, incoming = incoming.partition(b"\n")
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise ValueError("Служба OCR вернула некорректный ответ.")
                if "stage" not in reply:
                    return reply
                if reply["stage"] in _STAGES:
                    stage = reply["stage"]


def _vision_locked(image, regions, *, reviewed=False):
    global _worker, _worker_warm, _warmup_error
    if _closing:
        raise RuntimeError("QuizLens завершает работу.")
    if _worker is None or _worker.poll() is not None:
        _stop_worker()
        binary = _native_binary()
        with _lifecycle:
            if _closing:
                raise RuntimeError("QuizLens завершает работу.")
            _worker = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(_worker.stdin.fileno(), False)
    try:
        reply = _exchange_frame(_worker, image, regions, _WARM_TIMEOUT if _worker_warm else _COLD_TIMEOUT)
        if reply.get("error"):
            raise RuntimeError(str(reply["error"]))
        if regions is None:
            parts, aspect = reply.get("observations"), reply.get("aspect")
            if (not isinstance(parts, list) or not isinstance(aspect, (float, int))
                    or not math.isfinite(aspect) or aspect <= 0):
                raise RuntimeError("Служба OCR вернула некорректную разметку кадра.")
            if reviewed:
                from auto_layout import assess_layout
                result = assess_layout(parts, aspect=aspect)
                warnings = vision_reading_warnings(parts, result["texts"], result["regions"])
                if warnings:
                    result["readingWarnings"] = warnings
            else:
                result = parts, aspect
        else:
            texts = reply.get("texts")
            if not isinstance(texts, list) or len(texts) != len(regions) or not all(isinstance(s, str) for s in texts):
                raise RuntimeError("Служба OCR вернула некорректный результат.")
            result = ocr_texts(reply["parts"]) if "parts" in reply else texts
            if reviewed:
                warnings = vision_reading_warnings(
                    (dict(part, region=i) for i, group in enumerate(reply.get("parts", [])) for part in group), result)
                result = {"texts": result}
                if warnings:
                    result["readingWarnings"] = warnings
        _worker_warm = True
        _warmup_error = ""
        return result
    except (OSError, ValueError) as exc:
        _stop_worker()
        raise RuntimeError("Сбой связи с локальным OCR. Повторите проверку OCR. " + str(exc)) from exc
    except RuntimeError:
        _stop_worker()
        raise


def vision_reading_warnings(parts, texts, regions=None):
    """Keep only competing readings that still belong to a visible quiz field."""
    from ocr_review import field_for_box
    warnings = []
    if not texts:
        return warnings
    for part in parts:
        notice = part.get("readingWarning")
        if not isinstance(notice, dict):
            continue
        field = field_for_box(part, regions) if regions is not None else part.get("region")
        readings = notice.get("readings")
        if (type(field) is not int or not 0 <= field < len(texts) or not isinstance(readings, list)
                or len(readings) != 2 or not all(isinstance(r, str) and 0 < len(r) <= 140 for r in readings)
                or readings[0] == readings[1]):
            continue
        # A timer and numbering badges are intentionally removed from the text.
        # Numeric boundaries avoid attaching a stray "1" to a retained "10".
        pattern = r"(?<!\w)" + re.escape(readings[0]).replace(r"\ ", r"\s*") + r"(?!\w)"
        if not re.search(pattern, texts[field], re.IGNORECASE):
            continue
        warning = {"field": field, "readings": readings}
        image = notice.get("image")
        if isinstance(image, str) and len(image) < 45000 and image.startswith("data:image/png;base64,"):
            warning["image"] = image
        warnings.append(warning)
        if len(warnings) == 3:
            break
    return warnings


def reviewed_vision(image, regions):
    return _vision(image, regions, reviewed=True)


def _vision(image, regions, *, reviewed=False):
    if not _lock.acquire(timeout=0.5):
        thread = _warmup_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_COLD_TIMEOUT + 2)
        if not _lock.acquire(timeout=0.1):
            raise RuntimeError("Сейчас OCR читает другой кадр. Дождитесь результата и повторите проверку.")
    try:
        return _vision_locked(image, regions, reviewed=reviewed)
    finally:
        _lock.release()


def _warmup():
    global _warmup_error
    # A live quiz frame already in progress also warms this worker.
    if not _lock.acquire(blocking=False):
        return
    try:
        if _closing or _worker_warm:
            return
        bands = [dict(x=0, y=i / 5, w=1, h=0.2) for i in range(5)]
        _vision_locked((_ROOT / "examples" / "ocr-warmup.png").read_bytes(), bands)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        _warmup_error = str(exc)
    finally:
        _lock.release()


def start_prewarm():
    global _warmup_thread
    if sys.platform != "darwin":
        return
    with _lifecycle:
        if _closing or _warmup_thread is not None:
            return
        _warmup_thread = threading.Thread(target=_warmup, name="quizlens-ocr-warmup", daemon=True)
        _warmup_thread.start()


def warmup_status():
    if sys.platform == "win32":
        return windows_ocr_status()
    with _lifecycle:
        process = _worker
        if _closing or sys.platform != "darwin":
            return {"state": "idle", "detail": ""}
        if _worker_warm and process is not None and process.poll() is None:
            return {"state": "ready", "detail": "Apple Vision готов к распознаванию."}
        if _warmup_thread is not None and _warmup_thread.is_alive():
            return {"state": "preparing", "detail": "Apple Vision читает пробный кадр."}
        if _warmup_error:
            return {"state": "failed", "detail": _warmup_error}
        return {"state": "idle", "detail": ""}


def _tesseract_binary():
    if sys.platform == "win32":
        custom = os.environ.get("QUIZ_LENS_TESSERACT")
        if custom:
            if Path(custom).is_file():
                return custom
            raise RuntimeError("Проверь путь к tesseract.exe в переменной QUIZ_LENS_TESSERACT.")
        for var, subdir in (("ProgramFiles", "Tesseract-OCR"), ("ProgramW6432", "Tesseract-OCR"),
                            ("LOCALAPPDATA", "Programs/Tesseract-OCR"), ("LOCALAPPDATA", "Tesseract-OCR"),
                            ("ProgramFiles(x86)", "Tesseract-OCR")):
            folder = os.environ.get(var)
            if folder:
                candidate = Path(folder) / subdir / "tesseract.exe"
                if candidate.is_file():
                    return str(candidate)
    binary = shutil.which("tesseract")
    if binary:
        return binary
    if sys.platform == "win32":
        raise RuntimeError("Установи Tesseract 5 для Windows с русским и английским языками. Шаги в файле START-HERE.txt.")
    raise RuntimeError("Не найден Tesseract. Установите tesseract-ocr и tesseract-ocr-rus.")


@functools.lru_cache(maxsize=1)
def _tesseract_config():
    binary = _tesseract_binary()
    try:
        result = subprocess.run([binary, "--list-langs"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", timeout=6)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Не удалось запустить Tesseract. Проверь установку и повтори проверку.") from exc
    if result.returncode:
        raise RuntimeError("Tesseract не смог открыть языковые пакеты. Проверь папку tessdata и повтори проверку.")
    available = [line.strip() for line in result.stdout.splitlines()]
    languages = [lang for lang in ("rus", "eng") if lang in available]
    if not languages:
        raise RuntimeError("Для Tesseract нужны языковые пакеты rus и eng.")
    return binary, "+".join(languages)


def windows_ocr_status():
    try:
        from PIL import Image
    except ImportError:
        return {"state": "failed", "displayDetail": True,
                "detail": "На главной странице QuizLens установи компоненты OCR. Для ручной установки есть Install-OCR.cmd."}
    try:
        _, languages = _tesseract_config()
    except RuntimeError as exc:
        return {"state": "failed", "displayDetail": True, "detail": str(exc)}
    if languages != "rus+eng":
        available, missing = ("английский", "русский (rus)") if languages == "eng" else ("русский", "английский (eng)")
        return {"state": "partial", "displayDetail": True,
                "detail": f"OCR готов: {available} язык. Для двуязычных вопросов добавь {missing} через раздел компонентов на главной странице QuizLens."}
    return {"state": "ready", "displayDetail": True, "detail": "Tesseract готов. Установлены русский и английский языки."}


def _read_tsv(pages, binary, languages, mode, timeout):
    # Batch pages so that each reading pass starts Tesseract just once.
    buffer = io.BytesIO()
    pages[0].save(buffer, format="TIFF", save_all=True, append_images=pages[1:], compression="tiff_lzw")
    try:
        # Installed language packs can live in a directory without the configs/tsv preset.
        result = subprocess.run([binary, "stdin", "stdout", "--psm", mode, "-l", languages, "-c", "tessedit_create_tsv=1"],
                                input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, OMP_THREAD_LIMIT="1"), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Распознавание Tesseract заняло слишком много времени.") from exc
    if result.returncode:
        raise RuntimeError("Tesseract не смог распознать этот кадр.")
    groups = [[] for _ in pages]
    seen = set()
    for row in result.stdout.decode("utf-8").splitlines()[1:]:
        cells = row.split("\t", 11)
        if len(cells) != 12:
            continue
        level, page = int(cells[0]), int(cells[1]) - 1
        if not 0 <= page < len(groups):
            raise RuntimeError("Tesseract вернул неизвестный номер страницы.")
        if level == 1:
            seen.add(page)
        if level == 5 and cells[11].strip():
            x, y, w, h = map(int, cells[6:10])
            groups[page].append(dict(text=cells[11], x=x, y=y, w=w, h=h, confidence=float(cells[10])))
    if len(seen) != len(pages):
        raise RuntimeError("Tesseract вернул неполный результат распознавания.")
    return groups


def _word_image(page, part):
    x, y = part["x"], part["y"]
    return page.crop((x, y, x + part["w"], y + part["h"]))


def _separate_badge(page, part):
    # The number printed in a small circle may join the percentage into a single
    # OCR word. Split boxes only if the pixels contain a smaller glyph separated
    # by a large blank gutter; layout still checks the option numbering.
    if (not re.fullmatch(r"[1-8][0-9]+(?:[.,][0-9]+)?[%‰]", part["text"])
            or not 5 <= part["h"] <= 128 or not 8 <= part["w"] <= 512):
        return [part]
    image = _word_image(page, part)
    histogram = image.histogram()
    background = max(range(256), key=histogram.__getitem__)
    low, high = image.getextrema()
    if high - low < 35:
        return [part]
    foreground = low if background - low >= high - background else high
    threshold = abs(foreground - background) * .5
    ink = image.point(lambda level: 255 if abs(level - background) > threshold else 0)
    xmask = ink.getprojection()[0]
    try:
        left = xmask.index(1)
        end = xmask.index(0, left)
        body_start = xmask.index(1, end)
    except ValueError:
        return [part]
    small = ink.crop((left, 0, end, image.height)).getbbox()
    body = ink.crop((body_start, 0, image.width, image.height)).getbbox()
    if not small or not body:
        return [part]
    body_height = body[3] - body[1]
    small_height = small[3] - small[1]
    if (not .35 * body_height <= small_height <= .8 * body_height
            or end - left > .68 * body_height or body_start - end < .8 * body_height):
        return [part]
    return [
        dict(text=part["text"][0], x=part["x"] + left, y=part["y"] + small[1],
             w=end - left, h=small_height),
        dict(text=part["text"][1:], x=part["x"] + body_start, y=part["y"] + body[1],
             w=body[2] - body[0], h=body_height),
    ]


def _verify_lecture_words(pages, groups, binary, vocabulary):
    # The default Russian+English pass sometimes picks a Russian lookalike
    # even with very high confidence. Confirm documented terms on their pixels.
    from PIL import Image, ImageOps
    candidates = []
    for page, parts in zip(pages, groups):
        for part in parts:
            match = re.fullmatch(r"([^\w]*)([^\W\d_]{4,40})([^\w]*)", part["text"])
            if not match or not (4 <= part["h"] <= 160 and 8 <= part["w"] <= 800):
                continue
            candidate = vocabulary.candidate(match[2])
            check_ending = candidate is None and match[3] == "!"
            if check_ending:
                candidate = vocabulary.ending_candidate(match[2])
            if candidate:
                candidates.append((page, part, match, candidate, check_ending))
                if len(candidates) >= 8:
                    break
        if len(candidates) >= 8:
            break
    if not candidates:
        return
    crops = []
    for page, part, _, _, _ in candidates:
        image = _word_image(page, part)
        histogram = image.histogram()
        if sum(histogram[:128]) > sum(histogram[128:]):
            image = ImageOps.invert(image)
        height = 48
        width = max(1, round(image.width * height / image.height))
        resized = image.resize((width, height), getattr(Image, "Resampling", Image).LANCZOS)
        crops.append(ImageOps.expand(resized, border=10, fill="white"))
    try:
        readings = _read_tsv(crops, binary, "eng", "8", timeout=1.5)
    except (RuntimeError, OSError, ValueError):
        return  # The primary reading remains available on a slow/busy machine.
    for (_, part, match, candidate, check_ending), words in zip(candidates, readings):
        text = "".join(w["text"] for w in words)
        read = re.fullmatch(r"[^\w]*([A-Za-z]+)[^\w]*", text)
        if read and read[1].casefold() == candidate:
            if check_ending and (text.endswith("!") or min((w.get("confidence", 0) for w in words), default=0) < 65):
                continue
            part["text"] = match[1] + read[1] + ("" if check_ending else match[3])


def _ink(image):
    """Contrast against the local fill regardless of dark or light text."""
    pixels = image.convert('L')
    background = max(enumerate(pixels.histogram()), key=lambda pair: pair[1])[0]
    return pixels.point([min(255, max(0, abs(value-background)-21)*4) for value in range(256)])


def _ink_runs(ink, vertical=False):
    width, height = ink.size
    runs = []
    for i in range(width if vertical else height):
        strip = (i, 0, i+1, height) if vertical else (0, i, width, i+1)
        if ink.crop(strip).getbbox() is not None:
            if runs and runs[-1][1] == i:
                runs[-1] = (runs[-1][0], i+1)
            else:
                runs.append((i, i+1))
    return runs


def _colored_card_pages(image, cards, image_ops):
    left, top, right, bottom = cards[0]
    card_height = bottom - top
    if top < 6:
        return None
    padding = max(2, round(.005*(right-left)))
    left, right = max(0, left-padding), min(image.width, right+padding)
    top_limit = max(0, top-round(1.12*card_height))
    stem = _ink(image.crop((left, top_limit, right, top-2)))
    lines = []
    for start, end in _ink_runs(stem):
        bbox = stem.crop((0, start, stem.width, end)).getbbox()
        if end-start >= max(3, .024*card_height) and bbox[2]-bbox[0] >= .07*stem.width:
            lines.append((start, end))
    if not lines or stem.height-lines[-1][1] > .27*card_height:
        return None
    start, end = lines[-1]
    line_height = end-start
    for previous, last in reversed(lines[:-1]):
        if start-last > max(line_height, last-previous):
            break
        start = previous
    stem = stem.crop((0, start, stem.width, end))
    stem_box = (left, top_limit+start, right, top_limit+end)
    tiles, gutters = [], []
    for x, y, r, b in cards:
        inset = max(2, round(.18*(b-y)))
        side = max(2, round(.014*(r-x)))
        option = _ink(image.crop((x+side, y+inset, r-side, b-inset)))
        bbox = option.getbbox()
        if bbox is None:
            return None
        # The left geometry badge is a compact, isolated shape on every row.
        starts = _ink_runs(option, vertical=True)
        gutter = 0
        if len(starts) > 1:
            first, second = starts[:2]
            _, first_top, _, first_bottom = option.crop((first[0], 0, first[1], option.height)).getbbox()
            size = first_bottom-first_top
            if (first[0] <= .12*option.width and .65*size <= first[1]-first[0] <= 1.5*size
                    and second[0]-first[1] >= max(3, .29*size)):
                gutter = second[0]
        gutters.append(gutter)
        tiles.append(option)
    if all(gutters) and max(gutters)-min(gutters) <= max(3, .018*tiles[0].width):
        tiles = [tile.crop((gutter, 0, tile.width, tile.height)) for tile,gutter in zip(tiles,gutters)]

    pages = []
    for ink in (stem, *tiles):
        cropped = ink.crop(ink.getbbox())
        scale = min(4, max(1, 28/line_height))
        enlarged = cropped.resize((round(cropped.width*scale), round(cropped.height*scale)), resample=3)
        pages.append(image_ops.expand(image_ops.invert(enlarged), border=10, fill=255))
    return pages, [stem_box, *cards]


def _read_colored_cards(image, cards, binary, languages):
    from PIL import ImageOps

    prepared = _colored_card_pages(image, cards, ImageOps)
    if prepared is None:
        return None
    pages, boxes = prepared
    try:
        groups = _read_tsv(pages, binary, languages, "11", timeout=5)
    except RuntimeError:
        return None
    texts = ocr_texts(groups)
    question = texts[0]
    metadata = re.match(r"^(?:игрок|раунд\s*\d|вопрос\s*\d|озвучка|player|round\s*\d|question\s*\d)\b", question, re.I)
    if metadata or (len(question.split()) < 3 and '?' not in question) or not all(texts) or len({s.casefold() for s in texts[1:]}) != 4:
        return None
    if not all(group and median(part['confidence'] for part in group) >= 65 for group in groups):
        return None
    width, height = image.size
    regions = [dict(x=x/width, y=y/height, w=(r-x)/width, h=(b-y)/height) for x,y,r,b in boxes]
    result = {'regions': regions, 'texts': texts, **selection_hint(texts[0], texts[1:], [])}
    warnings = field_warnings(pages, pages, groups, texts, None,
                              lambda crops: _read_tsv(crops, binary, languages, "7", timeout=1.4))
    if warnings:
        result['readingWarnings'] = warnings
    return result


def _unreadable_color_stack():
    return {'regions': [], 'texts': [], 'captureStatus': 'partial',
            'hint': 'Нашёл цветные карточки, но не смог прочитать весь текст. Увеличь текст в окне квиза и покажи все варианты.'}


def _tesseract(raw_image, regions, vocabulary=None, *, review=False):
    try:
        from PIL import Image
    except ImportError as exc:
        if sys.platform == "win32":
            raise RuntimeError("Открой раздел компонентов QuizLens на главной странице и нажми кнопку установки OCR.") from exc
        raise RuntimeError("Для Tesseract нужен Pillow. Установите python3-pil или пакет Pillow через pip.") from exc
    binary, languages = _tesseract_config()
    pages = []
    originals = []
    color_stack_found = False
    try:
        with Image.open(io.BytesIO(raw_image)) as image:
            width, height = image.size
            if max(image.size) > 8192 or width * height > 24_000_000:
                raise ValueError("Размер кадра превышает ограничение OCR.")
            if regions is None:
                if review:
                    cards = colored_card_stack(image)
                    color_stack_found = bool(cards)
                    if color_stack_found:
                        color_result = _read_colored_cards(image, cards, binary, languages)
                        if color_result is not None:
                            return color_result
                # Small browser fonts and digits next to '%' otherwise drop out
                # of sparse OCR. Bound both size and total CPU work per frame.
                scale = min(1.5, 2000 / max(image.size), math.sqrt(2_500_000 / (width * height)))
                target = (max(1, round(width * scale)), max(1, round(height * scale)))
                resampling = getattr(Image, "Resampling", Image)
                page = image.resize(target, resampling.LANCZOS) if target != image.size else image
                pages.append(page.convert("L"))
                if review:
                    originals.append(image.convert("L") if target != image.size else pages[0])
            else:
                for region in regions:
                    left = math.floor(region["x"] * width)
                    top = math.floor(region["y"] * height)
                    right = math.ceil((region["x"] + region["w"]) * width)
                    bottom = math.ceil((region["y"] + region["h"]) * height)
                    pages.append(image.crop((left, top, min(right, width), min(bottom, height))).convert("L"))
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение.") from exc
    if regions is not None and review:
        originals = pages
    try:
        groups = _read_tsv(pages, binary, languages, "11" if regions is None else "6", timeout=15)
    except RuntimeError:
        if color_stack_found:
            return _unreadable_color_stack()
        raise
    if regions is None and len(groups[0]) > 900:
        return {"regions": [], "texts": []} if review else ([], width / height)
    if vocabulary and "eng" in languages:
        _verify_lecture_words(pages, groups, binary, vocabulary)
    groups = [[piece for part in parts for piece in _separate_badge(page, part)]
              for page, parts in zip(pages, groups)]
    if regions is None:
        # Retain separate word boxes, even if Tesseract places both columns on one line.
        w, h = pages[0].size
        parts = [dict(text=p["text"], x=p["x"]/w, y=p["y"]/h, w=p["w"]/w, h=p["h"]/h)
                 for p in groups[0] if p["w"] > 0 and p["h"] > 0]
        parts = parts if len(parts) <= 900 else []
        if not review:
            return parts, width / height
        result = assess_layout(parts, aspect=width / height)
        if color_stack_found and not result['regions']:
            return _unreadable_color_stack()
    else:
        texts = ocr_texts(groups)
        if not review:
            return texts
        result = {"texts": texts}

    warnings = field_warnings(pages, originals, groups, result["texts"], result.get("regions"),
                              lambda crops: _read_tsv(crops, binary, languages, "7", timeout=1.4))
    if warnings:
        result["readingWarnings"] = warnings
    return result


def reviewed_capture(image_data_url, regions=None, vocabulary=None, *, auto=False):
    """Return current-frame text, layout and any conflicting pixel readings."""
    if not auto:
        regions = _validate_regions(regions)
    raw = _decode_image(image_data_url)
    if sys.platform == "darwin":
        return reviewed_vision(raw, regions)
    return _tesseract(raw, regions, vocabulary, review=True)


def layout_observations(image_data_url, vocabulary=None):
    """Return current-frame OCR boxes (normalized, top-left) and original width/height."""
    raw = _decode_image(image_data_url)
    if sys.platform == "darwin":
        return _vision(raw, None)
    return _tesseract(raw, None, vocabulary)


def recognize(image_data_url, regions, vocabulary=None):
    """Return question and choices in supplied region order."""
    regions = _validate_regions(regions)
    raw = _decode_image(image_data_url)
    if sys.platform == "darwin":
        return _vision(raw, regions)
    return _tesseract(raw, regions, vocabulary)
