"""Follow the public download path against the real built site, with GitHub API unavailable."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright

from onboarding import ROOT, page_fits, visible_action


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def check_page(browser, origin, viewport):
    with browser.new_context(viewport=viewport, locale="ru-RU", reduced_motion="reduce") as ctx:
        # The installation guide and release-list fallbacks also work during API downtime.
        ctx.route("https://api.github.com/**", lambda route: route.abort())
        page = ctx.new_page()
        page.goto(origin)
        for system in ("windows", "mac"):
            download = page.locator(f"#{system}-download")
            visible_action(download)
            expect(download).to_have_attribute("href", "https://github.com/NolReaction/quizlens/releases")
        expect(page.locator("a[href*='QuizLens-Extension.zip']")).to_have_count(0)
        page_fits(page)
        assert page.evaluate("getComputedStyle(document.documentElement).scrollBehavior") == "auto"
        menu = page.locator("#nav-toggle")
        nav = page.locator("#site-nav")
        mobile = viewport["width"] <= 740
        if mobile:
            visible_action(menu)
            expect(nav).to_be_hidden()
            menu.focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            expect(nav.get_by_role("link").first).to_be_focused()
            page.keyboard.press("Escape")
            expect(nav).to_be_hidden()
            expect(menu).to_be_focused()
            menu.click()
        else:
            expect(menu).to_be_hidden()
        if mobile:
            # Reverse a transition halfway through, and use the OS preference to finish instantly.
            page.emulate_media(reduced_motion="no-preference")
            states = page.evaluate("""() => {
                const button = document.getElementById('nav-toggle');
                const menu = document.getElementById('site-nav');
                function sample() {
                    const animation = menu.getAnimations()[0];
                    animation.pause();
                    animation.currentTime = animation.effect.getTiming().duration / 2;
                    return [button.getAttribute('aria-expanded'), menu.inert, menu.hidden];
                }
                button.click();
                const closing = sample();
                button.click();
                return [closing, sample()];
            }""")
            assert states == [["false", True, False], ["true", False, False]]
            page.emulate_media(reduced_motion="reduce")
            expect(nav).to_be_visible()
            assert nav.evaluate("el => el.getAnimations().length") == 0
        nav.get_by_role("link", name="Расширение").click()
        extension = page.locator("#extension")
        expect(extension).to_be_in_viewport()
        expect(extension).to_contain_text("Показать папку расширения")
        if mobile:
            expect(nav).to_be_hidden()
            expect(extension).to_be_focused()
            menu.click()
        nav.get_by_role("link", name="Компоненты").click()
        expect(page.locator("#components")).to_be_in_viewport()
        expect(page.locator("#components").get_by_role("link", name="Скачать LM Studio")).to_be_visible()
        expect(page.locator("#windows-ocr")).to_be_visible()
        page_fits(page)


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
                            print(f"Verified platform downloads, bundled extension guide and navigation at {width}px.")
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                thread.join()
