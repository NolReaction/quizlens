"""Exercise the first-run screens with a real browser and no local model server."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

from local_updates import check_local_updates
from manual_window import check_manual_window


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
    page.locator("#serviceMenu > summary").click()
    page.locator("#navUpdates").click()
    expect(page.locator("dialog#appUpdates")).to_be_visible()
    page.get_by_role("button", name="Закрыть обновления").click()
    expect(page.locator("dialog#appUpdates")).to_be_hidden()
    assert page.locator("#preview").evaluate("v => v.srcObject?.getVideoTracks()[0]?.readyState") == "live"
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


def check_disclosure_motion(page):
    manager = page.locator("#sourceManager")
    sizes = manager.evaluate("""async details => {
        const summary = details.querySelector('summary');
        const body = details.querySelector('.disclosure-body');
        const height = () => details.getBoundingClientRect().height;
        async function toggle() {
            summary.click();
            const animation = body.getAnimations()[0];
            if (!animation) throw Error('Missing source disclosure animation');
            animation.pause();
            await animation.ready;
            animation.currentTime = animation.effect.getTiming().duration / 2;
            const halfway = height();
            // The finish handler restores natural layout and the native open state.
            const finished = new Promise(resolve => animation.addEventListener('finish', resolve, {once:true}));
            animation.finish();
            await finished;
            return [halfway, height()];
        }
        return [height(), ...await toggle(), ...await toggle()];
    }""")
    closed, growing, full, shrinking, final = sizes
    assert closed < growing < full and final < shrinking < full and abs(closed - final) < 1, sizes
    page.emulate_media(reduced_motion="reduce")
    assert manager.evaluate("""el => {
        const summary = el.querySelector('summary');
        summary.click();
        const opened = el.open && el.getAnimations({subtree:true}).length === 0;
        summary.click();
        return opened && !el.open;
    }""")
    page.emulate_media(reduced_motion="no-preference")


def check_service_menu(page, origin):
    toggle = page.locator("#serviceMenu > summary")
    visible_action(toggle)
    toggle.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#navCheck")).to_be_visible()
    expect(page.locator("#navSetup")).to_have_attribute("href", "/setup#connection")
    expect(page.locator("#navUpdates")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#navUpdates")).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.press("Enter")
    page.locator("#navUpdates").press("Enter")
    dialog = page.locator("dialog#appUpdates")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#installedVersion")).to_contain_text(re.compile(r"v?\d+\.\d+"))
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page).to_have_url(origin + "/")
    expect(toggle).to_be_focused()
    page.goto(origin + "/#appUpdates")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


def check_sources(page, prefix):
    manager = page.locator("#sourceManager")
    summary = manager.locator("summary")
    source_list = manager.locator("#lectureList")
    name = f"ux-{prefix}.ipynb"
    contents = [f"Материал номер {n:02}. Отношение числа верных прогнозов к общему числу объектов."
                for n in range(1, 9)]
    cells = [{"cell_type":"markdown", "metadata":{}, "source":[line]} for line in contents]
    with tempfile.TemporaryDirectory(prefix="quizlens-user-doc-") as files_dir:
        document = Path(files_dir) / name
        original = json.dumps({"cells":cells, "metadata":{}, "nbformat":4, "nbformat_minor":5}).encode()
        document.write_bytes(original)
        with page.expect_file_chooser() as chosen:
            page.locator("#addLecture").click()
        chosen.value.set_files(document)
        selected = source_list.get_by_role("checkbox", name=re.compile(re.escape(name)))
        expect(selected).to_be_visible()
        expect(selected).to_be_checked()
        selected.uncheck()
        page.reload()
        # Reopen the manager through its user control if the new session starts collapsed.
        if not manager.evaluate("el => el.open"):
            summary.click()
        expect(selected).not_to_be_checked()
        # Capture and native capture share a document selection, including changes made in another open tab.
        native = page.context.new_page()
        try:
            native.goto(page.url.split("#")[0].rstrip("/") + "/native")
            native.locator("#sourceManager > summary").click()
            native_choice = native.locator("#lectures").get_by_role("checkbox", name=re.compile(re.escape(name)))
            expect(native_choice).to_be_visible()
            expect(native_choice).not_to_be_checked()
            native_choice.check()
            expect(selected).to_be_checked()
        finally:
            native.close()
            page.bring_to_front()

        source_list.get_by_role("button", name=f"Посмотреть «{name}»").click()
        dialog = page.locator("#sourceDialog")
        expect(dialog).to_be_visible()
        content = dialog.locator("#sourceContent")
        expect(content).to_contain_text(contents[0])
        dialog.locator("#sourceNext").click()
        expect(content).not_to_contain_text(contents[0])
        expect(content).to_contain_text(contents[-1])
        dialog.locator("#sourcePrevious").click()
        expect(content).to_contain_text(contents[0])
        dialog.locator("#sourceClose").click()
        expect(dialog).to_be_hidden()

        remove = source_list.get_by_role("button", name=f"Удалить «{name}»")
        remove.click()
        expect(dialog.locator("#sourceConfirm")).to_be_visible()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(selected).to_be_checked()
        remove.click()
        dialog.locator("#sourceConfirm").click()
        expect(dialog).to_be_hidden()
        expect(selected).to_have_count(0)
        assert document.read_bytes() == original, "Local document library deletion affected the original file"
        page.reload()
        if not manager.evaluate("el => el.open"):
            summary.click()
        expect(source_list.get_by_role("checkbox").first).to_be_visible()
        expect(selected).to_have_count(0)


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
        expect(page.locator("#visual")).to_be_hidden()
        expect(page.locator("a[href*='QuizLens-Extension.zip']")).to_have_count(0)
        expect(page.locator("#sourceManager")).not_to_have_attribute("open", "")
        expect(page.locator("#tabReaderStatus")).to_have_attribute("data-state", "undetected", timeout=15000)
        expect(page.locator("#stopTabReader")).to_be_hidden()
        check_disclosure_motion(page)
        check_service_menu(page, url)
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

        check_sources(page, prefix)

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
        expect(page.get_by_role("link", name="Задачи по графикам и таблицам")).to_have_attribute("href", "/visual")
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
            check_manual_window(browser, origin, args.screenshots)
            for viewport in [(1280, 720), (390, 844)]:
                check_first_run(browser, origin, *viewport, args.screenshots)
        finally:
            browser.close()
