#!/usr/bin/env python3
"""Assemble a development archive or a notarized, stapled macOS release."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
PRODUCT = "QuizLens"
ARCHITECTURES = ("arm64", "x86_64")
HELPERS = ("RegionOCR", "ReadPDF", "WindowReader")
PUBLIC_EXAMPLES = {"analytics-demo.txt", "analytics-demo.quiz.json", "ocr-warmup.png", "QuizLens-demo.ipynb"}


def run(*args):
    subprocess.run(args, check=True)


def signing_settings(enabled, environ):
    if not enabled:
        return None
    if sys.platform != "darwin":
        raise ValueError("Developer ID signing and notarization require a macOS build machine.")
    identity = environ.get("QUIZLENS_SIGN_IDENTITY", "").strip()
    profile = environ.get("QUIZLENS_NOTARY_PROFILE", "").strip()
    if not identity.startswith("Developer ID Application: ") or not profile:
        raise ValueError("Set QUIZLENS_SIGN_IDENTITY (Developer ID Application) and QUIZLENS_NOTARY_PROFILE.")
    keychain = environ.get("QUIZLENS_KEYCHAIN", "").strip()
    return identity, profile, ["--keychain", keychain] if keychain else []


def copy_inputs(root, resources):
    resources.mkdir(parents=True)
    for file in sorted(root.glob("*.py")):
        shutil.copy(file, resources / file.name)
    for name in ("ui", "native", "examples"):
        for file in sorted((root / name).rglob("*")):
            if not file.is_file() or any(part.startswith(".") or part == "__pycache__" for part in file.relative_to(root).parts):
                continue
            if name == "native" and file.suffix != ".swift":
                continue
            if name == "examples" and file.relative_to(root / name).as_posix() not in PUBLIC_EXAMPLES:
                continue
            out = resources / file.relative_to(root)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(file, out)
    shutil.copy(root / "launch.command", resources / "launch.command")
    (resources / "launch.command").chmod(0o755)
    destination = resources / "packaging"
    destination.mkdir()
    for name in ("bootstrap.py", "FinderLauncher.m", "VERSION"):
        shutil.copy(root / "packaging" / name, destination / name)
    repo = root / "packaging" / "REPOSITORY"
    if repo.is_file():
        shutil.copyfile(repo, destination / "REPOSITORY")
    for name in ("app.py", "engine.py", "ocr.py", "ocr_terms.py", "ocr_review.py", "numeric_evidence.py", "ocr_layout.py", "auto_layout.py", "quiz_mode.py",
                 "journal.py", "visual.py", "training.py", "advisor.py", "model_bench.py",
                 "ui/setup.html", "ui/setup.js", "ui/setup.css", "ui/visual-transfer.js",
                 "ui/index.html", "ui/app.js", "ui/journal.html", "ui/journal.js", "ui/journal.css",
                 "ui/train.html", "ui/train.js", "ui/train.css", "ui/visual.html", "ui/visual.js", "ui/visual.css",
                 "native/ReadPDF.swift", "native/RegionOCR.swift", "native/WindowReader.swift",
                 "native/LayoutFragments.swift", *("examples/" + name for name in sorted(PUBLIC_EXAMPLES))):
        if not (resources / name).is_file():
            raise ValueError("Missing runtime input: " + name)


def build_universal_code(root, app):
    # Imported only for actual macOS builds; the source builder uses Darwin/POSIX libraries.
    from bootstrap import compile_helpers

    contents = app / "Contents"
    launcher = contents / "MacOS" / PRODUCT
    run("/usr/bin/xcrun", "--sdk", "macosx", "clang", "-fobjc-arc", "-O2", "-mmacosx-version-min=14.0",
        "-arch", "arm64", "-arch", "x86_64", root / "packaging/FinderLauncher.m", "-framework", "AppKit",
        "-o", launcher)
    run("/usr/bin/xcrun", "lipo", launcher, "-verify_arch", *ARCHITECTURES)
    helpers = contents / "Helpers"
    helpers.mkdir()
    with tempfile.TemporaryDirectory(prefix="quizlens-swift-") as tmp:
        stage = Path(tmp)
        for architecture in ARCHITECTURES:
            target = stage / architecture
            target.mkdir()
            compile_helpers(root, architecture, output=target)
        for name in HELPERS:
            executable = helpers / name
            run("/usr/bin/xcrun", "lipo", "-create", *(stage / arch / name for arch in ARCHITECTURES),
                "-output", executable)
            run("/usr/bin/xcrun", "lipo", executable, "-verify_arch", *ARCHITECTURES)
            executable.chmod(0o755)
    (contents / "Resources/quiz-lens/packaging/PRECOMPILED").write_text("1\n", encoding="ascii")


def sign_app(app, settings):
    if settings:
        identity, _, keychain = settings
        flags = ["--sign", identity, "--timestamp", "--options", "runtime", *keychain]
    else:
        flags = ["--sign", "-", "--timestamp=none"]
    for name in HELPERS:
        run("/usr/bin/codesign", "--force", *flags, "--identifier", "local.quizlens." + name,
            app / "Contents/Helpers" / name)
    run("/usr/bin/codesign", "--force", *flags, app)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--all-architectures", app)


def ditto_archive(path, target):
    run("/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", path, target)


def notarize(app, work, settings):
    _, profile, keychain = settings
    submission = work / "submission.zip"
    ditto_archive(app, submission)
    process = subprocess.run(["/usr/bin/xcrun", "notarytool", "submit", str(submission),
                              "--keychain-profile", profile, *keychain, "--wait", "--output-format", "json"],
                             stdout=subprocess.PIPE, text=True, check=False)
    try:
        receipt = json.loads(process.stdout)
    except (ValueError, TypeError):
        receipt = {}
    if not isinstance(receipt, dict):
        receipt = {}
    if process.returncode != 0 or receipt.get("status") != "Accepted":
        raise RuntimeError("Apple did not accept notarization (status: {}, submission: {}).".format(
            receipt.get("status", "unavailable"), receipt.get("id", "unavailable")))
    run("/usr/bin/xcrun", "stapler", "staple", app)
    verify_ticket(app)


def verify_ticket(app):
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--all-architectures", app)
    run("/usr/bin/xcrun", "stapler", "validate", app)
    run("/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", app)


def make_bundle(root, parent, settings=None):
    app = parent / (PRODUCT + ".app")
    contents = app / "Contents"
    launcher = contents / "MacOS" / PRODUCT
    launcher.parent.mkdir(parents=True)
    version = (root / "packaging" / "VERSION").read_text(encoding="ascii").strip()
    metadata = {
        "CFBundleName": PRODUCT, "CFBundleDisplayName": PRODUCT, "CFBundleIdentifier": "local.quizlens.desktop",
        "CFBundleExecutable": PRODUCT, "CFBundlePackageType": "APPL", "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleVersion": version, "CFBundleShortVersionString": version, "LSMinimumSystemVersion": "14.0",
        "LSUIElement": True, "CFBundleDevelopmentRegion": "ru", "CFBundleIconFile": "QuizLens.icns",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
    (contents / "PkgInfo").write_bytes(b"APPL????")
    copy_inputs(root, contents / "Resources/quiz-lens")
    shutil.copy(root / "packaging/QuizLens.icns", contents / "Resources/QuizLens.icns")
    if sys.platform == "darwin":
        build_universal_code(root, app)
        sign_app(app, settings)
    else:
        shutil.copy(root / "packaging/FinderLauncher", launcher)
        launcher.chmod(0o755)
    return app


def package(root, target, settings=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quizlens-zip-", dir=target.parent) as temp:
        work = Path(temp)
        folder = work / "QuizLens-Mac"
        folder.mkdir()
        app = make_bundle(root, folder, settings)
        extension = root / "browser-extension"
        target_extension = folder / extension.name
        target_extension.mkdir()
        shutil.copy(extension / "manifest.json", target_extension)
        for file in extension.iterdir():
            if file.is_file() and file.suffix in {".html", ".css", ".js", ".png"}:
                shutil.copy(file, target_extension)
        guide = root / "docs/GUIDE.md"
        shutil.copyfile(guide, folder / "README.md")
        archive = work / "package.zip"
        if sys.platform == "darwin":
            if settings:
                notarize(app, work, settings)
            ditto_archive(folder, archive)
            if settings:
                restored = work / "verify"
                restored.mkdir()
                run("/usr/bin/ditto", "-x", "-k", archive, restored)
                verify_ticket(restored / folder.name / app.name)
        else:
            if settings:
                raise ValueError("Notarization requires macOS.")
            # Source fallback, without a Developer ID signature or a ticket.
            for command in ("Start.command", "Install.command"):
                shutil.copy(root / "packaging" / command, folder / command)
                (folder / command).chmod(0o755)
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as bundle:
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(folder.parent))
        with zipfile.ZipFile(archive) as check:
            if check.testzip():
                raise RuntimeError("ZIP integrity check failed")
            launcher = f"{folder.name}/{app.name}/Contents/MacOS/{PRODUCT}"
            if not (check.getinfo(launcher).external_attr >> 16) & stat.S_IXUSR:
                raise RuntimeError("Missing executable bit on the app launcher")
        os.replace(archive, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", default=SOURCE, type=Path)
    parser.add_argument("--notarize", action="store_true", help="require Developer ID signing and Apple notarization")
    args = parser.parse_args()
    try:
        settings = signing_settings(args.notarize, os.environ)
    except ValueError as error:
        parser.error(str(error))
    package(args.source.resolve(), args.output.resolve(), settings)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
