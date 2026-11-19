"""Prepare native code once and run the local QuizLens server. Python 3.9+."""

import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
RESOURCES = ("ui", "native", "examples")
COMPILED = ("RegionOCR", "ReadPDF", "WindowReader")


def runtime_inputs(root):
    files = list(root.glob("*.py"))
    files.extend([root / "packaging" / "bootstrap.py", root / "packaging" / "VERSION"])
    repository = root / "packaging" / "REPOSITORY"
    if repository.is_file():
        files.append(repository)
    for name in RESOURCES:
        files.extend(p for p in (root / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")
                     and not (name == "native" and p.suffix != ".swift"))
    return sorted(files)


def build_name(root, paths, architecture):
    digest = hashlib.sha256(architecture.encode() + b"\0")
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:20]


def compile_helpers(root, architecture, output=None):
    for name in COMPILED:
        source = root / "native" / (name + ".swift")
        binary = (output or root / "native") / name
        command = ["/usr/bin/xcrun", "--sdk", "macosx", "swiftc", "-swift-version", "5", "-O",
                   "-target", architecture + "-apple-macosx14.0"]
        if "@main" in source.read_text(encoding="utf-8"):
            command.append("-parse-as-library")
        print("Компилирую " + name + "…", flush=True)
        sources = [str(source)]
        if name in ("RegionOCR", "WindowReader"):
            sources.append(str(root / "native" / "LayoutFragments.swift"))
        subprocess.run(command + sources + ["-o", str(binary)], check=True)


def build_runtime(root, cache, architecture):
    paths = runtime_inputs(root)
    key = build_name(root, paths, architecture)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / key
    with (cache / ".build.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if (target / "app.py").is_file() and all(os.access(target / "native" / n, os.X_OK) for n in COMPILED):
            return target
        # Build privately and publish only after all helpers compile.
        if target.exists():
            shutil.rmtree(target)
        with tempfile.TemporaryDirectory(dir=cache, prefix="build-") as work:
            stage = Path(work) / "app"
            for path in paths:
                destination = stage / path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
            compile_helpers(stage, architecture)
            stage.rename(target)
    return target


def main():
    if platform.system() != "Darwin":
        print("Для приложения требуется macOS 14 или новее.", file=sys.stderr)
        return 1
    cache = Path.home() / "Library" / "Application Support" / "QuizLens" / "runtime"
    runtime = build_runtime(SOURCE, cache, platform.machine())
    os.chdir(runtime)
    os.execv(sys.executable, [sys.executable, "-u", str(runtime / "app.py"), *sys.argv[1:]])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, subprocess.CalledProcessError) as error:
        print("Не удалось собрать или запустить QuizLens. Подробности выше.", file=sys.stderr)
        print(str(error), file=sys.stderr)
        sys.exit(1)
