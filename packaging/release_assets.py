"""Validate the release tag, embed the repository link, and assemble public assets."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = {
    "QuizLens-Mac.zip": "QuizLens-Mac/QuizLens.app/Contents/Resources/quiz-lens/",
    "QuizLens-Windows.zip": "QuizLens-Windows/app/",
}
GUIDE = "QuizLens-Guide.md"


def validate(tag, repository):
    version = (ROOT / "packaging/VERSION").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", tag) or tag != "v" + version:
        raise ValueError(f"Expected the release tag v{version}, got {tag!r}.")
    if not re.fullmatch(r"[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+", repository) or repository.split("/")[1] in (".", ".."):
        raise ValueError("Expected a GitHub repository of the form OWNER/REPOSITORY.")
    return version


def digest(file):
    with file.open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def assemble(directory, tag, repository, signing):
    version = validate(tag, repository)
    if signing not in {"notarized", "unsigned"}:
        raise ValueError("A verified macOS build status is required.")
    guide = ROOT / "docs/GUIDE.md"
    for name, root in ARCHIVES.items():
        with zipfile.ZipFile(directory / name) as archive:
            if archive.testzip():
                raise ValueError(f"The archive is corrupt: {name}")
            if archive.read(root + "packaging/VERSION").decode("ascii").strip() != version:
                raise ValueError(f"Wrong version in {name}")
            if archive.read(root + "packaging/REPOSITORY").decode("ascii").strip() != repository:
                raise ValueError(f"Wrong repository in {name}")
            # Windows checkout may have CRLF; compare UTF-8 text rather than newline conventions.
            packaged = archive.read(root.split("/")[0] + "/README.md").decode("utf-8").splitlines()
            if packaged != guide.read_text(encoding="utf-8").splitlines():
                raise ValueError(f"The guide in {name} differs from the guide at {tag}")
    shutil.copyfile(guide, directory / GUIDE)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = {name: digest(directory / name) for name in (*ARCHIVES, GUIDE)}
    manifest = {"version": version, "tag": tag, "repository": repository, "commit": commit,
                "macos": {"notarized": signing == "notarized"}, "sha256": files.copy()}
    (directory / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files["release.json"] = digest(directory / "release.json")
    (directory / "SHA256SUMS.txt").write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(files.items())),
                                              encoding="ascii")
    status = ("Сборка для Mac подписана Developer ID Application. Нотарификация Apple и прикреплённый билет проверены."
              if signing == "notarized" else "Сборка для Mac без Developer ID и нотарификации Apple. При открытии macOS может показать предупреждение.")
    release_url = f"https://github.com/{repository}/releases/download/{tag}"
    body = (f"<!-- quizlens-release: {commit} -->\n<!-- quizlens-macos: {signing} -->\n\n"
            f"[Скачать для Mac]({release_url}/QuizLens-Mac.zip) · "
            f"[Скачать для Windows x64]({release_url}/QuizLens-Windows.zip) · "
            f"[Инструкция](https://github.com/{repository}/blob/{tag}/docs/GUIDE.md)\n\n"
            f"{status}\n\nКонтрольные суммы файлов доступны в SHA256SUMS.txt.\n")
    (directory / "release-notes.md").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    build = commands.add_parser("assemble")
    for command in (prepare, build):
        command.add_argument("tag")
        command.add_argument("repository")
    build.add_argument("signing", choices=["notarized", "unsigned"])
    build.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        validate(args.tag, args.repository)
        (ROOT / "packaging/REPOSITORY").write_text(args.repository + "\n", encoding="ascii")
    else:
        assemble(args.directory, args.tag, args.repository, args.signing)
