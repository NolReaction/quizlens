"""Launch QuizLens or install the optional image/PDF dependencies on Windows."""

import os
from pathlib import Path
import subprocess
import sys
import venv

APP = Path(__file__).resolve().parents[1]


def prepare():
    parent = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    environment = parent / "QuizLens" / "runtime" / "windows-venv"
    print("Установка Pillow (кадры) и pypdf (текстовые PDF) из PyPI.")
    print("Пакеты будут находиться в отдельной папке этого пользователя:", environment)
    if input("Нажми Enter для установки или введи 0 для выхода: ").strip():
        return 0
    venv.EnvBuilder(with_pip=True).create(environment)
    executable = environment / "Scripts" / "python.exe"
    subprocess.run([str(executable), "-m", "pip", "--disable-pip-version-check", "install",
                    "--only-binary=:all:", "Pillow", "pypdf"], check=True)
    subprocess.run([str(executable), "-c", "from PIL import Image; from pypdf import PdfReader"], check=True)
    print("Компоненты Python готовы. Установи Tesseract с языками rus и eng по инструкции START-HERE.txt.")
    print("Открой Start.cmd для запуска.")
    return 0


def main():
    if sys.platform != "win32":
        raise SystemExit("Этот файл запуска предназначен для Windows.")
    if sys.argv[1:] == ["--prepare"]:
        return prepare()
    sys.path.insert(0, str(APP))
    import app
    import ocr
    print("QuizLens · оставь это окно открытым. Для остановки нажми Ctrl+C.")
    print(ocr.windows_ocr_status()["detail"])
    app.main()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except (OSError, subprocess.CalledProcessError) as error:
        print("QuizLens не завершил операцию.", error, file=sys.stderr)
        sys.exit(1)
