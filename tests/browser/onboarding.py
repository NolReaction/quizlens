"""Exercise the first-run screens with a real browser and no local model server."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

from local_updates import check_local_updates


ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def app_server():
    with tempfile.TemporaryDirectory(prefix="quizlens-onboarding-") as directory:
        server = subprocess.Popen(
            [sys.executable, "-u", "app.py", "--no-browser", "--port", "0", "--data-dir", directory],
            cwd=ROOT, stdout=subprocess.PIPE, text=True, encoding="utf-8",
            env={**os.environ, "QUIZ_LENS_LM_URL": "http://127.0.0.1:1/v1", "PYTHONUTF8": "1"},
        )
        try:
            line = server.stdout.readline()
            match = re.search(r"http://127\.0\.0\.1:[0-9]+", line)
            if not match:
                raise RuntimeError(f"QuizLens did not report its address: {line!r}")
            yield match.group()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def screenshot(page, directory, name):
    if directory:
        page.screenshot(path=directory / f"{name}.png", animations="disabled")


def page_fits(page):
    width, viewport = page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert width <= viewport + 1, f"{page.url}: content is {width}px wide in a {viewport}px viewport"


def visible_action(action, above_fold=True):
    expect(action).to_be_visible()
    bounds = action.bounding_box()
    assert bounds and bounds["height"] >= 44, f"Small action: {action} {bounds}"
    if above_fold:
        expect(action).to_be_in_viewport(ratio=0.97)


def check_selected_window(page):
    # The OS source picker requires a person. Supply a harmless video track to exercise
    # the same controls the app presents after a source is selected.
    page.evaluate("""() => {
        navigator.mediaDevices.getDisplayMedia = async () => {
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            const stream = canvas.captureStream(4);
            const draw = setInterval(() => {
                if (stream.getVideoTracks()[0].readyState === 'ended') clearInterval(draw);
                else context.fillRect(0, 0, canvas.width, canvas.height);
            }, 100);
            return stream;
        };
    }""")
    page.locator("#share").click()
    expect(page.locator("#videoBox")).to_be_visible()
    visible_action(page.locator("#run"))
    page.locator("#release").click()
    expect(page.locator("#emptyStage")).to_be_visible()
    expect(page.locator("#run")).to_be_hidden()


def check_model_choices(page, directory, prefix):
    # CI machines differ in GPU and RAM. Use the same hardware as a reader
    # choosing models for a typical Windows desktop, through the visible form.
    page.locator("#device-switch").click()
    page.locator("#platform").select_option("windows")
    page.locator("#accelerator").select_option("discrete")
    page.locator("#ram").fill("32")
    page.locator("#vram").fill("12")
    page.locator("#calculate").click()
    for candidate in ("#primary-model", "#other-model"):
        card = page.locator(candidate)
        specs = card.locator(".model-specs")
        expect(specs).to_be_visible()
        expect(specs).to_contain_text(re.compile("параметр", re.I))
        expect(specs).to_contain_text(re.compile("контекст", re.I))
        expect(specs).to_contain_text(re.compile("ответ", re.I))
        expect(specs).to_contain_text(re.compile(r"\d[\d,]*\s*млрд"))
        expect(card.get_by_role("link", name=re.compile("GGUF")).first).to_have_attribute(
            "href", re.compile(r"^https://huggingface\.co/.+\.gguf(?:\?|$)", re.I))
    page.locator("#primary-model").scroll_into_view_if_needed()
    page_fits(page)
    screenshot(page, directory, prefix + "-recommendations")

    budget = page.locator("#quiz-budget")
    expect(budget).to_be_visible()
    expect(budget).to_contain_text(re.compile("вход", re.I))
    expect(budget).to_contain_text(re.compile("выход", re.I))
    for count in ("#example-input", "#example-output"):
        expect(budget.locator(count)).to_contain_text(re.compile(r"≈\s*[1-9]"))
    page.locator("#catalog-link").click()
    entries = page.locator("#model-catalog article")
    expect(entries.nth(4)).to_be_visible()
    names = page.locator(".shortlist article h3, #model-catalog article h3").all_inner_texts()
    assert len(set(names)) >= 7, f"Expected at least seven distinct model choices: {names}"
    page_fits(page)
    screenshot(page, directory, prefix + "-catalog")
    page.locator("#device-switch").click()


def check_first_run(browser, url, width, height, directory):
    ctx = browser.new_context(viewport={"width": width, "height": height}, locale="ru-RU")
    errors = []
    ctx.on("page", lambda p: p.on("pageerror", lambda exc: errors.append(str(exc))))
    page = ctx.new_page()
    prefix = "desktop" if width > 800 else "mobile"
    try:
        page.goto(url)
        steps = page.locator("#modelStep, #materials, #windowStep")
        expect(steps).to_have_count(3)
        assert steps.evaluate_all("items => items.map(item => item.id)") == [
            "modelStep", "materials", "windowStep"]
        demo_link = page.locator("#windowStep a[data-demo-window]").first
        visible_action(page.locator("#modelStep #modelAdvisor"))
        visible_action(page.locator("#materials #addLecture"), above_fold=width > 800)
        visible_action(page.locator("#windowStep #share"), above_fold=width > 800)
        visible_action(demo_link, above_fold=width > 800)
        if width <= 800:
            visible_action(page.locator("#windowJump"))
        expect(page.locator("#navSetup")).to_be_in_viewport()
        page_fits(page)
        screenshot(page, directory, prefix + "-capture")
        page.locator("#modelAdvisor").click()
        expect(page).to_have_url(url + "/setup#connection")
        expect(page.locator("#connection")).to_be_in_viewport()
        expect(page.get_by_role("link", name=re.compile("Скачать LM Studio")).first).to_have_attribute(
            "href", "https://lmstudio.ai/download")
        page_fits(page)
        screenshot(page, directory, prefix + "-lm-studio")
        check_model_choices(page, directory, prefix)
        page.locator("#jump-check").click()
        expect(page.locator("#server-status")).to_have_attribute("data-state", "offline")
        expect(page.locator("#refresh")).to_be_enabled()
        expect(page.locator("#server-status")).to_contain_text(re.compile(r"LM Studio|Bionic"))
        page.get_by_role("link", name=re.compile("Перейти к документам")).click()
        expect(page).to_have_url(url + "/#materials")
        expect(page.locator("#addLecture")).to_be_in_viewport()

        with page.expect_file_chooser() as chosen:
            page.locator("#addLecture").click()
        lecture = f"ux-{prefix}.txt"
        chosen.value.set_files({"name": lecture, "mimeType": "text/plain",
                                "buffer": "Precision учитывает ложные срабатывания.".encode()})
        selected = page.locator("#materials #lectureList").get_by_role("checkbox", name=re.compile(re.escape(lecture)))
        expect(selected).to_be_checked()
        selected.uncheck()
        page.reload()
        expect(selected).not_to_be_checked()
        selected.check()
        expect(selected).to_be_checked()

        if width <= 800:
            page.locator("#windowJump").click()
            expect(page.locator("#windowStep #share")).to_be_in_viewport()
        with ctx.expect_page() as spawned:
            demo_link.click()
        popup = spawned.value
        expect(popup).to_have_url(re.compile(r"/demo\?lobby=1"))
        expect(popup.locator("#action")).to_be_enabled()
        expect(popup.locator("#round")).to_be_hidden()
        popup.locator("#duration").select_option("0")
        popup.locator("#mode").select_option("multiple")
        popup.locator("#action").click()
        expect(popup.get_by_role("checkbox")).to_have_count(4)
        question = popup.locator("#question").inner_text()
        assert question.strip()
        page.bring_to_front()
        demo_link.click()
        expect(popup.locator("#question")).to_have_text(question)
        expect(popup.locator("#round")).to_be_visible()
        assert len(ctx.pages) == 2, "Repeated click should reuse the running quiz"
        popup.close()

        check_selected_window(page)
        page.goto(url + "/train")
        expect(page.locator("#practice")).to_be_enabled()
        expect(page.locator("#bench")).to_be_disabled()
        visible_action(page.locator("#cards"), above_fold=width > 800)
        visible_action(page.locator("a[data-demo-window]").first, above_fold=width > 800)
        page_fits(page)
        screenshot(page, directory, prefix + "-training")
        page.locator("#cards").click()
        expect(page.locator("#cardQuestion")).to_have_text(re.compile(r"\S"))
        first_question = page.locator("#cardQuestion").inner_text()
        expect(page.locator("#cardAnswer")).to_be_hidden()
        page.locator("#flipCard").click()
        expect(page.locator("#cardAnswer")).to_be_visible()
        expect(page.locator("#correctOptions")).to_contain_text(re.compile(r"\S"))
        expect(page.locator("#cardReason")).to_contain_text(re.compile(r"\S"))
        page_fits(page)
        screenshot(page, directory, prefix + "-study-card")
        page.locator("#followingCard").click()
        expect(page.locator("#cardQuestion")).not_to_have_text(first_question)
        expect(page.locator("#cardAnswer")).to_be_hidden()
        page.locator("#previousCard").click()
        expect(page.locator("#cardQuestion")).to_have_text(first_question)
        page.locator("#closeCards").click()
        expect(page.locator("#practice")).to_be_enabled()
        page.goto(url + "/train#cards")
        expect(page.locator("#flashcards")).to_be_visible()
        page.goto(url + "/check")
        page.locator("#questionSet").select_option("metrics")
        page.get_by_role("button", name="Проверить расчёты по тексту").click()
        expect(page.locator("#message")).to_contain_text("Верных вариантов — 3 из 3")
        expect(page.locator("#results > li")).to_have_count(3)
        page.goto(url + "/native")
        visible_action(page.locator("#list"))
        visible_action(page.locator("a[data-demo-window]").first)
        page_fits(page)
        assert not errors, f"Uncaught browser errors: {errors}"
        print(f"First-run capture, trial window, LM Studio and study cards passed at {width}×{height}.")
    except Exception:
        screenshot(page, directory, prefix + "-failure")
        raise
    finally:
        ctx.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, help="Store first-run and model comparison screenshots")
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    chrome = os.environ.get("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("Install Chrome or set CHROME_BIN to a Chromium executable.")
    with app_server() as origin, sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=chrome)
        try:
            check_local_updates(browser, origin)
            for viewport in [(1280, 720), (390, 844)]:
                check_first_run(browser, origin, *viewport, args.screenshots)
        finally:
            browser.close()
