"""Follow the public download path against the real built site, with GitHub API unavailable."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

from playwright.sync_api import expect, sync_playwright

from onboarding import ROOT, page_fits, visible_action


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def check_page(browser, origin, viewport):
    with browser.new_context(viewport=viewport, locale="ru-RU", accept_downloads=True) as ctx:
        # A blocked GitHub API (or an out-of-date release) must not hide the standalone extension.
        ctx.route("https://api.github.com/**", lambda route: route.abort())
        page = ctx.new_page()
        page.goto(origin)
        download_link = page.locator("#extension-download")
        visible_action(download_link)
        page_fits(page)
        with page.expect_download() as result:
            download_link.click()
        download = result.value
        assert download.suggested_filename == "QuizLens-Extension.zip", download.suggested_filename
        with zipfile.ZipFile(download.path()) as bundle:
            assert bundle.testzip() is None
            source = ROOT / "browser-extension"
            files = [name for name in bundle.namelist() if not name.endswith("/")]
            expected = {f"browser-extension/{file.name}" for file in source.iterdir() if file.is_file()}
            assert set(files) == expected and len(files) == len(expected), files
            for name in files:
                assert bundle.read(name).replace(b"\r\n", b"\n") == (ROOT / name).read_bytes().replace(b"\r\n", b"\n"), name
            manifest = json.loads(bundle.read("browser-extension/manifest.json"))
            assert "activeTab" in manifest["permissions"] and not manifest.get("host_permissions")
        installation = page.locator("#components")
        expect(installation).to_be_visible()
        expect(installation.get_by_role("link", name="LM Studio", exact=False).first).to_be_visible()
        expect(page.locator("#windows-ocr")).to_be_visible()


if __name__ == "__main__":
    chrome = os.environ.get("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("Install Chrome or set CHROME_BIN.")
    with tempfile.TemporaryDirectory(prefix="quizlens-install-") as directory:
        subprocess.run([sys.executable, str(ROOT / "site/build.py"), "--repository", "NolReaction/quizlens",
                        "--output", directory], check=True)
        handler = functools.partial(QuietFiles, directory=directory)
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as driver:
                    browser = driver.chromium.launch(executable_path=chrome)
                    try:
                        for width, height in ((1280, 720), (390, 844)):
                            check_page(browser, f"http://127.0.0.1:{server.server_port}", {"width": width, "height": height})
                            print(f"Verified standalone extension download and component links at {width}px.")
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                thread.join()
