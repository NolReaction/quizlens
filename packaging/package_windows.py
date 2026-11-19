"""Package the Windows browser-capture build, including only executable code and public samples."""

import argparse
import os
from pathlib import Path
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
SAMPLES = ("analytics-demo.txt", "analytics-demo.quiz.json", "ocr-warmup.png", "QuizLens-demo.ipynb")
MODULES = ("app", "advisor", "model_bench", "auto_layout", "engine", "journal", "native_mode", "ocr",
           "ocr_layout", "quiz_mode", "semantic", "training", "visual")


def package(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    inputs = {
        "Start.cmd": source / "packaging/Start.cmd",
        "Install-OCR.cmd": source / "packaging/Install-OCR.cmd",
        "START-HERE.txt": source / "packaging/START-HERE-Windows.txt",
        "README.md": source / "docs/GUIDE.md",
        "app/packaging/VERSION": source / "packaging/VERSION",
        "app/packaging/windows_start.py": source / "packaging/windows_start.py",
    }
    repository = source / "packaging/REPOSITORY"
    if repository.is_file():
        inputs["app/packaging/REPOSITORY"] = repository
    for name in MODULES:
        inputs[f"app/{name}.py"] = source / (name + ".py")
    for file in (source / "ui").iterdir():
        if file.is_file() and file.suffix in (".html", ".js", ".css", ".json", ".png", ".ico", ".svg"):
            inputs[f"app/ui/{file.name}"] = file
    for name in SAMPLES:
        inputs[f"app/examples/{name}"] = source / "examples" / name
    for required_page in ("index", "setup", "check", "train", "journal", "visual", "demo"):
        if f"app/ui/{required_page}.html" not in inputs:
            raise ValueError("Missing page: " + required_page)
    with tempfile.TemporaryDirectory(prefix="quizlens-windows-", dir=target.parent) as stage:
        archive = Path(stage) / "package.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as bundle:
            for name, file in sorted(inputs.items()):
                contents = file.read_bytes()
                if name.endswith(".cmd") or name == "START-HERE.txt":
                    # cmd.exe requires batch files without BOM, and CRLF also supports old Notepad.
                    contents = contents.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                bundle.writestr("QuizLens-Windows/" + name, contents)
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip():
                raise ValueError("Corrupt Windows ZIP")
        os.replace(archive, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.source.resolve(), args.output.resolve())
    print(args.output.resolve())
