"""Run the shipped entrypoint from its extracted archive and test the local service."""

import argparse
import hashlib
import importlib.util
import io
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener
import zipfile


def smoke(archive, signing):
    expected = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="ascii").strip()
    with tempfile.TemporaryDirectory(prefix="QuizLens release ") as directory:
        work = Path(directory)
        if archive.name == "QuizLens-Mac.zip" and sys.platform == "darwin":
            if signing not in {"notarized", "unsigned"}:
                raise ValueError("Missing macOS signing result")
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(work)], check=True)
            folder = work / "QuizLens-Mac"
            app = folder / "QuizLens.app"
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(app)], check=True)
            if signing == "notarized":
                subprocess.run(["/usr/bin/xcrun", "stapler", "validate", str(app)], check=True)
                subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)], check=True)
            command = ["/bin/bash", str(app / "Contents/Resources/quiz-lens/launch.command")]
            expected_platform = "macos"
            data_dir = str(work / "smoke-data")
        elif archive.name == "QuizLens-Windows.zip" and os.name == "nt":
            if signing:
                raise ValueError("Unexpected Apple status for Windows")
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(work)
            folder = work / "QuizLens-Windows"
            command = ["cmd.exe", "/d", "/c", "Start.cmd"]
            expected_platform = "windows"
            data_dir = "smoke-data"
        else:
            raise ValueError("Run the ZIP on its target operating system")

        extension = folder / "browser-extension"
        manifest = json.loads((extension / "manifest.json").read_bytes())
        if manifest["version"] != expected or manifest["manifest_version"] != 3:
            raise ValueError("Wrong extension in the packaged archive")
        for script in (manifest["action"]["default_popup"], manifest["background"]["service_worker"],
                       "reader.js", "receiver.js", "popup.js", "popup.css"):
            if not (extension / script).is_file():
                raise ValueError(f"Missing packaged extension file: {script}")

        log = work / "server.log"
        # Relative data path keeps cmd.exe arguments independent of spaces in TEMP.
        with log.open("wb") as output:
            process = subprocess.Popen(command + ["--port", "0", "--no-browser", "--data-dir", data_dir],
                                       cwd=folder, stdout=output, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
                                       env={**os.environ, "PYTHONUTF8": "1"})
        try:
            url = None
            until = time.monotonic() + 70
            while time.monotonic() < until and process.poll() is None:
                message = log.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"QuizLens " + re.escape(expected) + r" · (http://127\.0\.0\.1:\d+)", message)
                if match:
                    url = match.group(1)
                    break
                time.sleep(.15)
            if not url:
                raise RuntimeError("Archive did not start:\n" + log.read_text(encoding="utf-8", errors="replace")[-2000:])
            local = build_opener(ProxyHandler({}))
            with local.open(url + "/api/health", timeout=7) as response:
                info = json.load(response)
            if info.get("version") != expected or info.get("platform") != expected_platform:
                raise ValueError(f"Unexpected running server: {info}")
            version_root = (folder / "app" if expected_platform == "windows" else
                            app / "Contents/Resources/quiz-lens") / "packaging"
            official = (version_root / "REPOSITORY").is_file() and (
                    version_root / "REPOSITORY").read_text(encoding="ascii").strip() == "NolReaction/quizlens"
            # Disable unattended network checks in this temporary profile before inspecting update state.
            settings = Request(url + "/api/update", data=b'{"action":"preferences","automatic":false}',
                               headers={"Content-Type": "application/json"})
            with local.open(settings, timeout=7) as response:
                json.load(response)
            with local.open(url + "/api/update", timeout=7) as response:
                update = json.load(response)
            if update.get("supported") != official or update.get("currentVersion") != expected:
                raise ValueError(f"Packaged launcher failed the update status check: {update}")
            if official:
                # Stage these exact bytes through the shipped extractor and native signature checks.
                # The release is still private here, so only the network fetch uses local fixture bytes.
                from unittest.mock import patch
                spec = importlib.util.spec_from_file_location("packaged_updater", version_root.parent / "updater.py")
                shipped_updater = importlib.util.module_from_spec(spec)
                with patch.object(sys, "dont_write_bytecode", True), patch.object(
                        sys, "path", [str(version_root.parent), *sys.path]):
                    spec.loader.exec_module(shipped_updater)
                archive_bytes = archive.read_bytes()
                digest = hashlib.sha256(archive_bytes).hexdigest()
                update_cache = work / "verify update staging"
                with patch.object(shipped_updater, "fetch_official", return_value=archive_bytes) as fetch:
                    shipped_updater.stage_release(update_cache, expected, digest, archive.name)
                    fetch.assert_called_once_with(f"{shipped_updater.RELEASES}/download/v{expected}/{archive.name}",
                                                  shipped_updater.MAX_ZIP)
                if not shipped_updater.target_root(update_cache, expected, archive.name):
                    raise ValueError("The shipped updater could not activate the contents of this archive")
            with local.open(url + "/download/QuizLens-Extension.zip", timeout=7) as response:
                if response.headers.get_content_type() != "application/zip":
                    raise ValueError("Extension download returned the wrong content type")
                with zipfile.ZipFile(io.BytesIO(response.read())) as extracted:
                    names = {f"browser-extension/{file.name}" for file in extension.iterdir() if file.is_file()}
                    if extracted.testzip() or set(extracted.namelist()) != names:
                        raise ValueError("Local app served an incomplete extension archive")
                    for name in names:
                        if extracted.read(name) != (folder / name).read_bytes().replace(b"\r\n", b"\n"):
                            raise ValueError(f"Different extension on the live local download route: {name}")
            profile = {"platform": "windows", "accelerator": "discrete", "ramGiB": 32, "vramGiB": 10}
            request = Request(url + "/api/advisor", data=json.dumps(profile).encode(),
                              headers={"Content-Type": "application/json"})
            with local.open(request, timeout=7) as response:
                advice = json.load(response)
            if not advice.get("primary") or not advice.get("catalog"):
                raise ValueError("The packaged advisor could not load its model catalog.")
            with local.open(url + "/api/lectures", timeout=7) as response:
                lectures = json.load(response)["lectures"]
            source = next(row for row in lectures if row["builtIn"] and row["name"] == "analytics-demo.txt")
            question = {"question": "Из 1000 клиентов ушли 10. Модель предсказала всем клиентам «останется». Чему равна Accuracy?",
                        "options": ["50 %", "99 %", "0 %", "1 %"], "lectureIds": [source["id"]], "channel": "check"}
            request = Request(url + "/api/solve", data=json.dumps(question).encode(),
                              headers={"Content-Type": "application/json"})
            with local.open(request, timeout=7) as response:
                answer = json.load(response)
            if answer.get("choices") != [2] or answer.get("verification", {}).get("kind") != "calculation":
                raise ValueError(f"The packaged service could not verify the Accuracy example: {answer}")
            for path in ("/", "/setup", "/train", "/journal", "/demo?lobby=1"):
                with local.open(url + path, timeout=7) as response:
                    if response.status != 200 or b"<!doctype html>" not in response.read().lower():
                        raise ValueError(f"Could not open {path}")
            for path in ("/demo-window.js", "/capture.css", "/train.js", "/token-usage.js", "/chart-assist.js", "/tab-reader.js", "/tab-reader-help.html"):
                with local.open(url + path, timeout=7) as response:
                    if response.status != 200 or not response.read(1):
                        raise ValueError(f"Missing UI asset: {path}")
            print(f"{archive.name}: launched the {expected_platform} entrypoint and verified the {expected} interface.")
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
        if expected_platform == "macos":
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(app)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--signing", choices=["unsigned", "notarized"])
    args = parser.parse_args()
    smoke(args.archive.resolve(), args.signing)
