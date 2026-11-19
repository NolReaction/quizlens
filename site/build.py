"""Build the static GitHub Pages download page for the current repository."""

import argparse
from pathlib import Path
import re
import shutil

SOURCE = Path(__file__).resolve().parent
REPOSITORY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/[a-zA-Z0-9_.-]{1,100}")


def build(repository, target):
    if not REPOSITORY.fullmatch(repository) or repository.split("/")[1] in (".", ".."):
        raise ValueError("Укажи репозиторий GitHub в формате owner/name.")
    if target.resolve() == SOURCE:
        raise ValueError("Выбери отдельный каталог для готовой страницы.")
    template = (SOURCE / "index.html").read_text(encoding="utf-8")
    target.mkdir(parents=True, exist_ok=True)
    html = template.replace("{{REPOSITORY}}", repository).replace("{{REPO_URL}}", "https://github.com/" + repository)
    (target / "index.html").write_text(html, encoding="utf-8")
    for asset in ("download.css", "download.js"):
        shutil.copyfile(SOURCE / asset, target / asset)
    (target / ".nojekyll").touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        build(args.repository, args.output)
    except ValueError as error:
        parser.error(str(error))
