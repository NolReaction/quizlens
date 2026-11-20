"""Build the same unpackable extension ZIP for Pages, releases and the local app."""

import io
from pathlib import Path
import zipfile


EXTENSION_ARCHIVE = "QuizLens-Extension.zip"
EXTENSION_FILES = ("manifest.json", "background.js", "popup.css", "popup.html",
                   "popup.js", "reader.js", "receiver.js",
                   "icon-16.png", "icon-32.png", "icon-48.png", "icon-128.png")


def package_bytes(name: str, data: bytes) -> bytes:
    if Path(name).suffix in {".js", ".css", ".json", ".html"}:
        return data.replace(b"\r\n", b"\n")
    return data


def extension_archive(extension_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in EXTENSION_FILES:
            # Fixed timestamps, line endings and permissions keep the ZIP identical on all platforms.
            item = zipfile.ZipInfo("browser-extension/" + name)
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            source = package_bytes(name, (extension_dir / name).read_bytes())
            archive.writestr(item, source, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
