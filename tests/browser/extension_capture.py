"""Drive activeTab pairing through real Chromium toolbar popups in a temporary profile."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import ImageGrab
from playwright.sync_api import expect, sync_playwright

from onboarding import ROOT, app_server
from tab_reader_cases import check_dom_extraction


def open_popup(source, harness, expected):
    source.bring_to_front()
    # Xvfb has no window manager to focus the Chromium window for the keyboard shortcut.
    windows = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--class", "chromium"], text=True)
    subprocess.run(["xdotool", "windowfocus", "--sync", windows.splitlines()[-1]], check=True, timeout=8)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "alt+shift+y"], check=True)
    expect_popup(harness, expected)


def expect_popup(harness, state):
    harness.wait_for_function("""state => {
      const view = chrome.extension.getViews({type:'popup'})[0];
      return view?.document.body?.dataset.state === state &&
        view.document.querySelector('#actions').getAttribute('aria-busy') !== 'true';
    }""", arg=state, polling=100, timeout=12000)


def close_popup(harness):
    subprocess.run(["xdotool", "key", "Escape"], check=True)
    harness.wait_for_function("() => !chrome.extension.getViews({type:'popup'}).length", timeout=5000)


def press_action(harness, command):
    # The browser itself granted activeTab when the user opened the popup.
    harness.evaluate("""command => {
      const button = chrome.extension.getViews({type:'popup'})[0].document.querySelector('#action');
      if (button.dataset.command !== command || button.disabled) throw Error('Unexpected popup action: ' + button.textContent);
      button.click();
    }""", command)


def screenshot_popup(harness, directory, name):
    if not directory:
        return
    directory.mkdir(exist_ok=True, parents=True)
    x, y, width, height = harness.evaluate("""() => {
      const view = chrome.extension.getViews({type:'popup'})[0];
      return [view.screenX, view.screenY, view.innerWidth, view.innerHeight];
    }""")
    # Capture the actual toolbar surface on the Xvfb display, including its current connection state.
    ImageGrab.grab(bbox=(x, y, x + width, y + height), xdisplay=os.environ["DISPLAY"]).save(directory / f"popup-{name}.png")


def expect_reader(capture, state):
    expect(capture.locator("#tabReaderStatus")).to_have_attribute("data-state", state, timeout=15000)


def run(url, temp, screenshots):
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
            expect(capture.locator("#lectureList input:checked")).to_have_count(3)
            expect_reader(capture, "available")
            quiz = context.new_page()
            quiz_url = url.replace("127.0.0.1", "localhost") + "/demo?lobby=1"
            quiz.goto(quiz_url)
            quiz.locator("#duration").select_option("0")

            open_popup(quiz, harness, "need-connection")
            close_popup(harness)
            open_popup(capture, harness, "can-bind")
            screenshot_popup(harness, screenshots, "connect")
            press_action(harness, "bind")
            expect_reader(capture, "paired")
            expect_popup(harness, "bound")
            close_popup(harness)

            protected = context.new_page()
            protected.goto("chrome://version")
            open_popup(protected, harness, "unsupported")
            close_popup(harness)
            protected.close()

            open_popup(quiz, harness, "can-start")
            press_action(harness, "start")
            expect_popup(harness, "waiting")
            expect_reader(capture, "waiting")
            screenshot_popup(harness, screenshots, "waiting")
            # Start the quiz without closing its toolbar popup. Both surfaces should update live.
            quiz.locator("#action").evaluate("button => button.click()")
            expect(capture.locator("#answer .answer-title")).to_have_text("Recall", timeout=15000)
            expect_reader(capture, "question")
            expect_popup(harness, "question")
            harness.wait_for_function("""async () => {
                const [quiz] = await chrome.tabs.query({active:true, currentWindow:true});
                return (await chrome.action.getBadgeText({tabId:quiz.id})) === '✓';
            }""")
            title = harness.evaluate("""() => chrome.extension.getViews({type:'popup'})[0]
                .document.querySelector('#sourceTitle').textContent""")
            assert quiz.title() in title, "The active source must be identifiable in the popup"
            screenshot_popup(harness, screenshots, "reading")
            harness.evaluate("""() => chrome.extension.getViews({type:'popup'})[0]
                .document.querySelector('#focusCapture').click()""")
            harness.wait_for_function("() => !chrome.extension.getViews({type:'popup'}).length")
            capture.wait_for_function("() => document.visibilityState === 'visible'")
            quiz.bring_to_front()
            quiz.locator("#options button:visible").first.click()
            quiz.locator("#action").click()
            expect(capture.locator("#answer .answer-title")).to_have_text("75 %", timeout=15000)
            expect(quiz.locator("#feedback")).to_contain_text("Выберите")

            # Navigating the source revokes its activeTab read. Pairing to the receiver survives.
            quiz.reload()
            expect_reader(capture, "stopped")
            open_popup(capture, harness, "bound")
            close_popup(harness)
            another = context.new_page()
            another.goto(quiz_url)
            open_popup(another, harness, "can-start")
            press_action(harness, "start")
            expect_reader(capture, "waiting")
            expect_popup(harness, "waiting")
            press_action(harness, "stop")
            expect_reader(capture, "stopped")
            close_popup(harness)
            another.close()

            # An extension that disappears during reading must not leave the last answer marked as fresh.
            quiz.locator("#duration").select_option("0")
            quiz.locator("#action").click()
            open_popup(quiz, harness, "can-start")
            press_action(harness, "start")
            expect_reader(capture, "question")
            close_popup(harness)
            capture.bring_to_front()
            # This is the temporary unpacked test copy; uninstallSelf needs no management permission.
            harness.evaluate("() => { setTimeout(() => chrome.management.uninstallSelf({showConfirmDialog:false}), 0); }")
            expect_reader(capture, "disconnected")
            expect(capture.locator("#stopTabReader")).to_be_hidden()
            expect(capture.locator("#answer .answer-title")).not_to_have_text("Recall")
            check_dom_extraction(context.new_page())
            print("Verified activeTab grants and real popup/capture states through pairing, reading, navigation, stopping and removal.")
        finally:
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="quizlens-extension-") as directory, app_server() as origin:
        run(origin, Path(directory), args.screenshots)
