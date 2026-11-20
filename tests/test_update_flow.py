"""Exercise the packaged supervisor, update API and rollback across real child restarts."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ASSET = "QuizLens-Windows.zip"
BUNDLE = "QuizLens-Windows/app/"
REPOSITORY = "NolReaction/quizlens"
BASE_VERSION = "5.0.0"


def fixture_transport(source, feed):
    # Insert into a private copy. The production updater retains its fixed Github release channel.
    tree = ast.parse(source)
    entry = next(stmt for stmt in tree.body if isinstance(stmt, ast.If)
                 and "__name__" in ast.unparse(stmt.test) and "__main__" in ast.unparse(stmt.test))
    lines = source.splitlines(keepends=True)
    offline = f'''
def platform_asset():
    return {ASSET!r}


def fetch_official(url, limit):
    feed = Path({str(feed)!r})
    if url == RELEASES + "/latest/download/release.json":
        path = feed / "release.json"
    else:
        match = re.fullmatch(re.escape(RELEASES) + r"/download/v([0-9.]+)/QuizLens-Windows\\.zip", url)
        if not match:
            raise AssertionError("Unexpected download URL " + url)
        path = feed / (match[1] + ".zip")
    data = path.read_bytes()
    assert len(data) <= limit
    return data


'''
    return "".join(lines[:entry.lineno - 1]) + offline + "".join(lines[entry.lineno - 1:])


def make_release(template, output, feed, version, broken=False):
    # Reuse exactly the files selected by the production packager, including its real UI and journal.
    with zipfile.ZipFile(template) as original, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        repository_stamp = BUNDLE + "packaging/REPOSITORY"
        for name in original.namelist():
            if name == repository_stamp:
                continue
            contents = original.read(name)
            if name == BUNDLE + "packaging/VERSION":
                contents = (version + "\n").encode("ascii")
            elif name == BUNDLE + "updater.py":
                contents = fixture_transport(contents.decode("utf-8"), feed).encode("utf-8")
            elif broken and name == BUNDLE + "app.py":
                contents = b"raise SystemExit('Deliberate startup failure in test fixture')\n"
            bundle.writestr(name, contents)
        bundle.writestr(repository_stamp, REPOSITORY)


class UpdateFlow(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="QuizLens update verification ")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.feed = self.work / "release feed"
        self.feed.mkdir()
        self.template = self.work / ASSET
        subprocess.run([sys.executable, str(ROOT / "packaging/package_windows.py"), "--output", str(self.template)],
                       check=True, stdout=subprocess.DEVNULL)
        self.data = self.work / "user documents"
        self.data.mkdir()
        base = self.work / "base.zip"
        make_release(self.template, base, self.feed, BASE_VERSION)
        with zipfile.ZipFile(base) as bundle:
            bundle.extractall(self.work / "installation")
        self.root = self.work / "installation" / BUNDLE
        self.publish(BASE_VERSION)
        self.client = build_opener(ProxyHandler({}))
        self.log = self.work / "supervisor.log"

    def publish(self, version, broken=False):
        archive = self.feed / (version + ".zip")
        make_release(self.template, archive, self.feed, version, broken)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {"tag": "v" + version, "version": version, "sha256": {ASSET: digest}, "repository": REPOSITORY}
        stage = self.feed / "next-manifest"
        stage.write_text(json.dumps(manifest), encoding="utf-8")
        stage.replace(self.feed / "release.json")

    def start(self):
        with self.log.open("wb") as out:
            self.service = subprocess.Popen(
                [sys.executable, "-u", str(self.root / "updater.py"), "--port", "0", "--no-browser",
                 "--data-dir", str(self.data)], cwd=self.root, stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                env={**os.environ, "PYTHONUTF8": "1", "QUIZ_LENS_LM_URL": "http://127.0.0.1:1/v1"},
            )
        self.addCleanup(self.stop)
        match = self.wait("server address", lambda: re.search(r"QuizLens [\d.]+ · (http://127\.0\.0\.1:\d+)",
                                                               self.log.read_text(encoding="utf-8", errors="replace")))
        self.url = match[1]

    def stop(self):
        if self.service.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/pid", str(self.service.pid), "/t", "/f"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(self.service.pid, signal.SIGTERM)
        self.service.wait(timeout=10)

    def wait(self, description, poll, timeout=35):
        deadline = time.monotonic() + timeout
        while self.service.poll() is None and time.monotonic() < deadline:
            try:
                found = poll()
                if found:
                    return found
            except HTTPError:
                raise
            except (OSError, HTTPException):
                # The socket closes briefly between versions; always reconnect at the original address.
                pass
            time.sleep(.08)
        self.fail(f"Waiting for {description}; supervisor={self.service.poll()}:\n" +
                  self.log.read_text(encoding="utf-8", errors="replace")[-2700:])

    def request(self, path, *, body=None, text=None):
        contents = json.dumps(body).encode() if body is not None else text
        request = Request(self.url + path, data=contents, headers={"Content-Type": "application/json"
                          if text is None else "text/plain", "Origin": self.url})
        try:
            with self.client.open(request, timeout=2) as response:
                return json.load(response)
        except HTTPError as error:
            self.fail(f"{path} [{error.code}]: {error.read().decode('utf-8', errors='replace')}")

    def state_is(self, value):
        return self.request("/api/update")["state"] == value

    def apply(self, version, broken=False):
        self.publish(version, broken)
        self.request("/api/update", body={"action": "check"})
        self.wait("new release availability", lambda: self.state_is("available"))
        self.request("/api/update", body={"action": "apply"})
        self.wait("checksum verified and candidate staged", lambda: self.state_is("ready"))
        self.request("/api/update", body={"action": "activate"})

    def test_update_then_failed_successor_preserves_origin_documents_and_last_good_runtime(self):
        self.start()
        self.assertEqual(self.request("/api/health")["version"], BASE_VERSION)
        self.assertTrue(self.request("/api/update")["supported"])
        self.request("/api/update", body={"action": "preferences", "automatic": False})
        result = self.request("/api/import?name=update-test.txt", text=b"QuizLens update fixture: existing notes")
        document_id = result["lecture"]["id"]
        self.apply("5.0.1")
        self.wait("new runtime on original port", lambda: self.request("/api/health")["version"] == "5.0.1")
        self.assertIn(document_id, [entry["id"] for entry in self.request("/api/lectures")["lectures"]])
        self.apply("5.0.2", broken=True)
        self.wait("failed version rolled back", lambda: self.state_is("error"))
        self.assertEqual(self.request("/api/health")["version"], "5.0.1")
        self.assertIn(document_id, [entry["id"] for entry in self.request("/api/lectures")["lectures"]])
        self.assertFalse(self.request("/api/update")["automatic"])
        # A later launch from the untouched original ZIP must also choose the last healthy version.
        self.stop()
        self.start()
        self.assertEqual(self.request("/api/health")["version"], "5.0.1")
        self.assertIn(document_id, [entry["id"] for entry in self.request("/api/lectures")["lectures"]])
