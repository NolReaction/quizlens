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

from general_answers import MODEL, PROVENANCE, local_model
from manual_window import uncheck_sources
from onboarding import ROOT, app_server
from tab_reader_cases import BUTTON_OPTIONS, BUTTON_TASK, button_quiz_html, check_dom_extraction


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


def expect_popup_session(harness, connected, reading, can_toggle):
    harness.wait_for_function("""session => {
      const doc = chrome.extension.getViews({type:'popup'})[0]?.document;
      if (!doc) return false;
      const control = doc.querySelector('#readingSwitch[role="switch"]');
      const receiver = doc.querySelector('#receiverState');
      const readingValue = doc.querySelector('#readingValue');
      const version = doc.querySelector('#extensionVersion');
      return control?.checkVisibility() && receiver?.checkVisibility() && readingValue?.checkVisibility() &&
        version?.checkVisibility() && version.textContent === 'v' + chrome.runtime.getManifest().version &&
        control.getAttribute('aria-checked') === String(session.reading) &&
        control.disabled === !session.canToggle &&
        receiver.textContent.trim() === (session.connected ? 'Подключено' : 'Не подключено') &&
        readingValue.textContent.trim() === (session.reading ? 'Включено' : 'Выключено');
    }""", arg={"connected": connected, "reading": reading, "canToggle": can_toggle}, timeout=8000)


def close_popup(harness):
    subprocess.run(["xdotool", "key", "Escape"], check=True)
    harness.wait_for_function("() => !chrome.extension.getViews({type:'popup'}).length", timeout=5000)


def press_action(harness, command):
    # The browser itself granted activeTab when the user opened the popup.
    harness.evaluate("""command => {
      const selector = ['start', 'stop'].includes(command) ? '#readingSwitch' : '#action';
      const button = chrome.extension.getViews({type:'popup'})[0].document.querySelector(selector);
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


def run(url, temp, screenshots, model_calls):
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
            capture.locator("#model").select_option(MODEL)
            expect(capture.locator("#withoutDocuments")).to_be_checked()
            expect(capture.locator("#withoutDocuments")).to_be_disabled()
            expect_reader(capture, "available")
            quiz = context.new_page()
            quiz_url = url.replace("127.0.0.1", "localhost") + "/demo?lobby=1"
            quiz.goto(quiz_url)
            quiz.locator("#duration").select_option("0")

            open_popup(quiz, harness, "need-connection")
            expect_popup_session(harness, connected=False, reading=False, can_toggle=False)
            close_popup(harness)
            open_popup(capture, harness, "can-bind")
            screenshot_popup(harness, screenshots, "connect")
            press_action(harness, "bind")
            expect_reader(capture, "paired")
            expect_popup(harness, "bound")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=False)
            close_popup(harness)

            # Refreshing the capture page reconnects the receiver without another toolbar action.
            capture.reload()
            expect_reader(capture, "paired")
            open_popup(capture, harness, "bound")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=False)
            close_popup(harness)

            protected = context.new_page()
            protected.goto("chrome://version")
            open_popup(protected, harness, "unsupported")
            close_popup(harness)
            protected.close()

            open_popup(quiz, harness, "can-start")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=True)
            press_action(harness, "start")
            expect_popup(harness, "waiting")
            expect_reader(capture, "waiting")
            expect_popup_session(harness, connected=True, reading=True, can_toggle=True)
            screenshot_popup(harness, screenshots, "waiting")
            # Start the quiz without closing its toolbar popup. Both surfaces should update live.
            quiz.locator("#action").evaluate("button => button.click()")
            expect(capture.locator("#answer .answer-title")).to_have_text("Recall", timeout=15000)
            expect(capture.locator("#answer .answer-source")).to_be_visible()
            assert not model_calls, "A selected question bank should take precedence over model knowledge"
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

            # The source and its question do not change while the receiver reloads.
            capture.reload()
            expect_reader(capture, "question")
            expect(capture.locator("#answer .answer-title")).to_have_text("Recall", timeout=15000)
            open_popup(capture, harness, "question")
            expect_popup_session(harness, connected=True, reading=True, can_toggle=True)
            close_popup(harness)

            quiz.bring_to_front()
            quiz.locator("#options button:visible").first.click()
            quiz.locator("#action").click()
            expect(capture.locator("#answer .answer-title")).to_have_text("75 %", timeout=15000)
            expect(quiz.locator("#feedback")).to_contain_text("Выберите")

            # Navigating the source revokes its activeTab read. Pairing to the receiver survives.
            quiz.reload()
            expect_reader(capture, "stopped")
            open_popup(capture, harness, "bound")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=False)
            close_popup(harness)
            open_popup(quiz, harness, "can-start")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=True)
            close_popup(harness)

            # An ordinary page with colored HTML buttons reaches the model even with no documents.
            uncheck_sources(capture)
            expect(capture.locator("#withoutDocuments")).to_be_enabled()
            expect(capture.locator("#withoutDocuments")).to_be_checked()
            another = context.new_page()
            fixture_url = url.replace("127.0.0.1", "localhost") + "/button-quiz-fixture"
            another.route(fixture_url, lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=button_quiz_html(marked=True)))
            another.goto(fixture_url)
            another.set_viewport_size({"width": 880, "height": 720})
            open_popup(another, harness, "can-start")
            press_action(harness, "start")
            expect_reader(capture, "question")
            expect_popup(harness, "question")
            expect(capture.locator("#answer .answer-title")).to_have_text("Сосна", timeout=15000)
            expect(capture.locator("#answer .answer-mode")).to_have_text(PROVENANCE)
            assert len(model_calls) == 1, model_calls
            prompt = json.loads(model_calls[0]["messages"][1]["content"])
            assert prompt["question"] == BUTTON_TASK, prompt
            assert [option["text"] for option in prompt["options"]] == BUTTON_OPTIONS, prompt
            rows = capture.request.get(url + "/api/journal").json()["entries"]
            recorded = [row for row in rows if row["question"] == BUTTON_TASK]
            assert len(recorded) == 1 and recorded[0]["reviewable"] and recorded[0]["review"] is None, recorded
            assert recorded[0]["kind"] == "model_knowledge", recorded
            transcript = capture.locator("#readText")
            for text in [BUTTON_TASK, *BUTTON_OPTIONS]:
                expect(transcript).to_contain_text(text)
            expect(transcript).not_to_contain_text("Скрытые данные варианта")
            screenshot_popup(harness, screenshots, "button-question")

            # A retained but disabled option is not a new, shorter question.
            last_option = another.locator(".choices button").last
            last_option.evaluate("button => { button.disabled = true; }")
            expect_reader(capture, "partial")
            expect_popup(harness, "partial")
            expect(capture.locator("#readingTranscript")).to_be_hidden()
            last_option.evaluate("button => { button.disabled = false; }")
            expect_reader(capture, "question")
            expect_popup(harness, "question")

            press_action(harness, "stop")
            expect_reader(capture, "stopped")
            expect_popup(harness, "can-start")
            expect_popup_session(harness, connected=True, reading=False, can_toggle=True)
            screenshot_popup(harness, screenshots, "paused")
            close_popup(harness)
            another.close()
            for source in capture.locator('#lectureList input[type="checkbox"]').all():
                source.check()

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
            print("Verified activeTab grants, local model recordings, source precedence, capture refresh, and removal.")
        finally:
            context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="quizlens-extension-") as directory, local_model(choice=3) as (model_url, calls):
        with app_server(model_url) as origin:
            run(origin, Path(directory), args.screenshots, calls)
