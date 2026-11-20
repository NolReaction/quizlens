"""Launch QuizLens or install the optional image/PDF dependencies on Windows."""

import os
from pathlib import Path
import subprocess
import sys
import venv

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from console_log import event


def prepare():
    parent = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    environment = parent / "QuizLens" / "runtime" / "windows-venv"
    event("Компоненты", "Установка Pillow для кадров и pypdf для PDF из PyPI в папку текущего пользователя.")
    if input("Нажми Enter для установки или введи 0 для выхода: ").strip():
        return 0
    venv.EnvBuilder(with_pip=True).create(environment)
    executable = environment / "Scripts" / "python.exe"
    subprocess.run([str(executable), "-m", "pip", "--disable-pip-version-check", "install",
                    "--only-binary=:all:", "Pillow", "pypdf"], check=True)
    subprocess.run([str(executable), "-c", "from PIL import Image; from pypdf import PdfReader"], check=True)
    event("Компоненты", "Pillow и pypdf готовы. Установи Tesseract с языками rus и eng по инструкции START-HERE.txt.")
    event("Сервер", "Открой Start.cmd для запуска.")
    return 0


def main():
    if sys.platform != "win32":
        raise SystemExit("Этот файл запуска предназначен для Windows.")
    if sys.argv[1:] == ["--prepare"]:
        return prepare()
    import updater
    return updater.supervise()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except (OSError, subprocess.CalledProcessError) as error:
        event("Запуск", "Не удалось завершить операцию. Проверь доступ к папке приложения и инструкцию START-HERE.txt.",
              level="ОШИБКА", error=error)
        sys.exit(1)
