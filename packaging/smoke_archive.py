"""Run the shipped entrypoint from its extracted archive and test the local service."""

import argparse
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
            for path in ("/demo-window.js", "/capture.css", "/train.js", "/token-usage.js"):
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
