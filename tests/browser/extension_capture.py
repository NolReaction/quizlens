"""Drive activeTab pairing through real Chromium toolbar popups in a temporary profile."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from playwright.sync_api import expect, sync_playwright

from onboarding import ROOT, app_server
from tab_reader_cases import check_dom_extraction


def press_action(source, harness, expected):
    source.bring_to_front()
    # Xvfb has no window manager to focus the Chromium window for the keyboard shortcut.
    windows = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--class", "chromium"], text=True)
    subprocess.run(["xdotool", "windowfocus", "--sync", windows.splitlines()[-1]], check=True, timeout=8)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "alt+shift+y"], check=True)
    harness.wait_for_function("""label => chrome.extension.getViews({type:'popup'}).some(view => {
      const button = view.document.querySelector('#action');
      return button && !button.disabled && button.textContent === label;
    })""", arg=expected, polling=100, timeout=12000)
    # Chromium itself has granted activeTab to this page in response to the shortcut.
    harness.evaluate("""() => chrome.extension.getViews({type:'popup'})[0].document.querySelector('#action').click()""")


def run(url, temp):
    extension = temp / "extension"
    shutil.copytree(ROOT / "browser-extension", extension)
    manifest_file = extension / "manifest.json"
    manifest = json.loads(manifest_file.read_bytes())
    assert not manifest.get("host_permissions") and "activeTab" in manifest["permissions"]
    # Assign a shortcut to Chromium's existing toolbar action; keep the shipped permissions and scripts.
    manifest["commands"] = {"_execute_action": {"suggested_key": {"default": "Alt+Shift+Y"}}}
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            temp / "profile", channel="chromium", headless=False,
            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
        )
        try:
            worker = (context.service_workers or [context.wait_for_event("serviceworker")])[0]
            extension_id = worker.url.split("/")[2]
            harness = context.pages[0]
            harness.goto(f"chrome-extension://{extension_id}/popup.html")
            capture = context.new_page()
            capture.goto(url)
            expect(capture.locator("#materials input:checked").first).to_be_visible()
            press_action(capture, harness, "Подключить QuizLens")
            expect(capture.locator("#tabReaderStatus")).to_contain_text("подключён")
            subprocess.run(["xdotool", "key", "Escape"], check=True)
            quiz = context.new_page()
            quiz.goto(url.replace("127.0.0.1", "localhost") + "/demo?lobby=1")
            quiz.locator("#duration").select_option("0")
            quiz.locator("#action").click()
            press_action(quiz, harness, "Читать вопросы")
            expect(capture.locator("#answer .answer-title")).to_have_text("Recall", timeout=15000)
            subprocess.run(["xdotool", "key", "Escape"], check=True)
            quiz.locator("#options button:visible").first.click()
            quiz.locator("#action").click()
            expect(capture.locator("#answer .answer-title")).to_have_text("75 %", timeout=15000)
            expect(quiz.locator("#feedback")).to_contain_text("Выберите")
            quiz.reload()
            expect(capture.locator("#tabReaderStatus")).to_contain_text("остановлено")
            check_dom_extraction(context.new_page())
            print("Verified explicit activeTab grants, two questions on a separate origin, and disconnect on reload.")
        finally:
            context.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="quizlens-extension-") as directory, app_server() as origin:
        run(origin, Path(directory))
