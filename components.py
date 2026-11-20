"""Detect OCR components and install the Windows tools requested by the local user."""
from __future__ import annotations

import filecmp
import hashlib
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.request import urlopen

import ocr
from extension_package import EXTENSION_FILES

# The upstream tessdata_fast 4.1.0 language files, in bytes and SHA-256.
LANGUAGES = {
    "eng": (4113088, "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"),
    "rus": (3861738, "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225"),
}


class ExtensionFolder:
    def __init__(self, runtime, source):
        self.source = source
        self.target = runtime / "browser-extension"
        self.lock = threading.Lock()

    def sync(self):
        with self.lock:
            self.target.mkdir(parents=True, exist_ok=True)
            # Publish the manifest last, after every script from the same release is available.
            with tempfile.TemporaryDirectory(dir=self.target.parent) as directory:
                for name in (*[name for name in EXTENSION_FILES if name != "manifest.json"], "manifest.json"):
                    source, target = self.source / name, self.target / name
                    if target.is_file() and filecmp.cmp(source, target, shallow=False):
                        continue
                    pending = Path(directory) / name
                    shutil.copyfile(source, pending)
                    pending.replace(target)

    def open(self):
        if sys.platform not in {"win32", "darwin"}:
            raise ValueError("Скачай ZIP расширения и распакуй его в постоянную папку.")
        self.sync()
        if sys.platform == "win32":
            os.startfile(str(self.target))
        else:
            subprocess.Popen(["/usr/bin/open", str(self.target)])
        return {"path": str(self.target)}


def python_packages(runtime):
    return runtime / ("packages-" + sys.implementation.cache_tag + "-" + str(sys.maxsize.bit_length() + 1))


def activate(runtime):
    packages = python_packages(runtime)
    if packages.is_dir() and str(packages) not in sys.path:
        sys.path.insert(0, str(packages))
        importlib.invalidate_caches()
    language_dir = runtime / "tessdata-fast-4.1.0"
    if all((language_dir / (name + ".traineddata")).is_file() for name in LANGUAGES):
        os.environ["TESSDATA_PREFIX"] = str(language_dir)
    ocr._tesseract_config.cache_clear()


def pillow_ready():
    try:
        from PIL import Image
        Image.new("L", (1, 1))
        return True
    except (ImportError, OSError):
        return False


def install_python(runtime):
    # Publish a complete, importable folder. The active Python process can load it immediately.
    target = python_packages(runtime)
    with tempfile.TemporaryDirectory(dir=runtime) as directory:
        pending = Path(directory) / "packages"
        subprocess.run([sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                        "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--target",
                        str(pending), "Pillow", "pypdf"], stdin=subprocess.DEVNULL, check=True, timeout=300)
        subprocess.run([sys.executable, "-I", "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); from PIL import Image; from pypdf import PdfReader",
                        str(pending)], check=True, timeout=30)
        if not target.exists():
            pending.rename(target)
    activate(runtime)
    if not pillow_ready():
        raise RuntimeError("Компоненты Python повреждены. Запусти Install-OCR.cmd из архива для восстановления.")


def install_languages(runtime):
    target = runtime / "tessdata-fast-4.1.0"
    with tempfile.TemporaryDirectory(dir=runtime) as directory:
        pending = Path(directory) / "tessdata"
        pending.mkdir()
        for name, (size, digest) in LANGUAGES.items():
            url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{name}.traineddata"
            with urlopen(url, timeout=45) as response:
                content = response.read(size + 1)
            if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError("Проверка загруженных языковых данных не прошла. Повтори установку.")
            (pending / (name + ".traineddata")).write_bytes(content)
        (pending / "configs").mkdir()
        (pending / "configs" / "tsv").write_text("tessedit_create_tsv 1\n", encoding="ascii")
        if not target.exists():
            pending.rename(target)
        else:
            # Repair missing files after an interrupted manual change.
            (target / "configs").mkdir(exist_ok=True)
            for item in pending.rglob("*"):
                if item.is_file():
                    os.replace(item, target / item.relative_to(pending))
    activate(runtime)


def install_windows(runtime, progress):
    runtime.mkdir(parents=True, exist_ok=True)
    if not pillow_ready():
        progress("Устанавливаю компоненты Python для изображений и PDF…")
        install_python(runtime)
    try:
        ocr._tesseract_binary()
    except RuntimeError:
        if os.environ.get("QUIZ_LENS_TESSERACT"):
            raise
        winget = shutil.which("winget.exe")
        if not winget:
            raise RuntimeError("Установи Tesseract для Windows по ссылке в разделе компонентов и нажми «Проверить».")
        progress("Устанавливаю Tesseract. Если Windows запросит разрешение, подтверди установку.")
        result = subprocess.run([winget, "install", "--id", "tesseract-ocr.tesseract", "--exact",
                                 "--source", "winget", "--accept-source-agreements", "--accept-package-agreements",
                                 "--silent", "--disable-interactivity"], stdin=subprocess.DEVNULL, timeout=420)
        try:
            ocr._tesseract_binary()
        except RuntimeError as exc:
            raise RuntimeError(f"Windows не завершила установку Tesseract (код {result.returncode}). "
                               "Можно повторить или установить Tesseract по ссылке в разделе компонентов.") from exc
    ocr._tesseract_config.cache_clear()
    try:
        languages = ocr._tesseract_config()[1]
    except RuntimeError:
        languages = ""
    if languages != "rus+eng":
        progress("Скачиваю русский и английский языки для распознавания…")
        install_languages(runtime)
    if ocr._tesseract_config()[1] != "rus+eng":
        raise RuntimeError("Tesseract не смог прочитать русский и английский пакеты. Повтори установку.")


class OCRComponents:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.RLock()
        self.progress = ""
        self.error = ""
        self.platform = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, "other")
        if self.platform == "windows":
            activate(runtime)

    def is_installing(self):
        with self.lock:
            return bool(self.progress)

    def _result(self, status, message):
        return {"platform": self.platform, "status": status, "message": message,
                "canInstall": self.platform == "windows" and status in {"missing", "error"}}

    def snapshot(self):
        with self.lock:
            if self.progress:
                return self._result("installing", self.progress)
            if self.platform == "macos":
                native = ocr.warmup_status()
                if native["state"] == "failed":
                    return self._result("error", native["detail"])
                return self._result("ready", "Распознавание встроено в macOS.")
            if self.platform not in {"windows", "linux"}:
                return self._result("unavailable", "Для этого устройства доступно чтение вкладки.")
            has_images = pillow_ready()
            try:
                # A user may have installed either language from outside the app.
                ocr._tesseract_config.cache_clear()
                bilingual = ocr._tesseract_config()[1] == "rus+eng"
            except RuntimeError:
                bilingual = False
            if has_images and bilingual:
                self.error = ""
                return self._result("ready", "Распознавание готово. Доступны русский и английский.")
            if self.error:
                return self._result("error", self.error)
            return self._result("missing", "Для захвата изображения нужны Pillow и Tesseract с языками rus и eng.")

    def install(self):
        with self.lock:
            current = self.snapshot()
            if not current["canInstall"]:
                return current
            self.progress, self.error = "Подготавливаю установку…", ""
            threading.Thread(target=self._run, name="quizlens-install-ocr", daemon=True).start()
            return self._result("installing", self.progress)

    def _set_progress(self, message):
        with self.lock:
            self.progress = message

    def _run(self):
        message = "Не удалось завершить установку компонентов."
        try:
            install_windows(self.runtime, self._set_progress)
        except subprocess.TimeoutExpired:
            message = "Установка заняла слишком много времени. Проверь подключение и повтори."
        except subprocess.CalledProcessError:
            message = "Не удалось установить компоненты Python. Проверь подключение к PyPI и наличие pip."
        except RuntimeError as exc:
            message = str(exc)
        except OSError as exc:
            message = "Не удалось установить компоненты. Проверь подключение и доступ к папке QuizLens. " + str(exc)
        else:
            message = ""
        finally:
            with self.lock:
                self.error = message
                self.progress = ""
