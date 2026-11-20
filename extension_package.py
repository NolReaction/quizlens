"""Build the same unpackable extension ZIP for Pages, releases and the local app."""

import io
from pathlib import Path
import zipfile


EXTENSION_ARCHIVE = "QuizLens-Extension.zip"
EXTENSION_FILES = ("manifest.json", "background.js", "popup.css", "popup.html",
                   "popup.js", "reader.js", "receiver.js")


def extension_archive(extension_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in EXTENSION_FILES:
            # Fixed timestamps, line endings and permissions keep the ZIP identical on all platforms.
            item = zipfile.ZipInfo("browser-extension/" + name)
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            source = (extension_dir / name).read_bytes().replace(b"\r\n", b"\n")
            archive.writestr(item, source, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
