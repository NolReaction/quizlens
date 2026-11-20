"""Check manual OCR from captured pixels and the real calibration controls."""

from playwright.sync_api import expect


WIDTH = 1000
HEIGHT = 630
QUESTION = "Какой прибор измеряет температуру?"
OPTIONS = ("Термометр", "Линейка", "Часы", "Компас")
REGIONS = [(55, 63, 935, 133), *[(605, y, 938, y + 86) for y in (158, 266, 374, 482)]]


def uncheck_sources(page):
    page.locator("#sourceManager > summary").click()
    sources = page.locator('#lectureList input[type="checkbox"]')
    expect(sources.first).to_be_checked()
    for source in sources.all():
        source.uncheck()
    expect(page.locator("#lectureList input:checked")).to_have_count(0)


def setup_canvas_source(page):
    # Replace only the system picker. Live video, the canvas crops and the OCR request are real.
    page.evaluate("""({width, height, question, options, regions}) => {
        navigator.mediaDevices.getDisplayMedia = async () => {
            const canvas = document.createElement('canvas');
            canvas.width = width; canvas.height = height;
            const ctx = canvas.getContext('2d');
            const stream = canvas.captureStream(8);
            function paint() {
                ctx.fillStyle = '#283743';
                ctx.fillRect(0, 0, width, height);
                ctx.fillStyle = '#afc3cb';
                ctx.font = '18px sans-serif';
                ctx.fillText('Тренировка · Вопрос 1 из 3', 67, 40);
                ctx.fillStyle = '#fbfaf5';
                ctx.font = 'bold 31px sans-serif';
                ctx.fillText(question, 69, 112);

                ctx.fillStyle = '#344856';
                ctx.fillRect(69, 149, 496, 429);
                for (const [i, color] of ['#e4a754', '#4e8bac', '#76ae9d'].entries()) {
                    ctx.fillStyle = color;
                    ctx.fillRect(136 + i * 128, 305 - i * 32, 84, 177 + i * 32);
                }
                for (const [i, option] of options.entries()) {
                    const [left, top] = regions[i + 1];
                    ctx.fillStyle = '#18292e';
                    ctx.fillRect(left - 8, top - 8, 350, 102);
                    ctx.fillStyle = '#fbfaf5';
                    ctx.fillText(option, left + 15, top + 55);
                }
            }
            paint();
            const refresh = setInterval(() => {
                if (stream.getVideoTracks()[0].readyState === 'ended') clearInterval(refresh);
                else paint();
            }, 100);
            return stream;
        };
    }""", {"width": WIDTH, "height": HEIGHT, "question": QUESTION, "options": OPTIONS, "regions": REGIONS})


def check_manual_window(browser, url, directory):
    context = browser.new_context(viewport={"width": 1280, "height": 810}, locale="ru-RU", reduced_motion="reduce")
    page = context.new_page()
    solve_requests = []

    def record_solve(request):
        if request.url.endswith("/api/solve"):
            solve_requests.append(request)

    page.on("request", record_solve)
    try:
        page.goto(url)
        uncheck_sources(page)

        setup_canvas_source(page)
        page.locator("#share").click()
        expect(page.locator("#videoBox")).to_be_visible()
        expect(page.locator("#run")).to_be_enabled()
        page.locator("#captureSettings > summary").click()
        page.locator("#calibrate").click()
        dialog = page.locator("#calibration")
        expect(dialog).to_be_visible()
        page.locator("#areaCount").select_option("4")
        image = dialog.locator("#calImage")
        image.scroll_into_view_if_needed()
        bounds = image.bounding_box()
        assert bounds, "The calibration image needs a viewport for dragging areas"
        for x1, y1, x2, y2 in REGIONS:
            page.mouse.move(bounds["x"] + x1 * bounds["width"] / WIDTH, bounds["y"] + y1 * bounds["height"] / HEIGHT)
            page.mouse.down()
            page.mouse.move(bounds["x"] + x2 * bounds["width"] / WIDTH, bounds["y"] + y2 * bounds["height"] / HEIGHT, steps=4)
            page.mouse.up()
        expect(dialog.locator("#calAreas .area")).to_have_count(5)
        save_button = dialog.get_by_role("button", name="Сохранить и начать чтение", exact=True)
        expect(save_button).to_be_enabled()

        with page.expect_response(lambda response: response.url.endswith("/api/ocr"), timeout=20000) as observed:
            save_button.click()
        response = observed.value
        assert response.ok, response.text()
        payload = response.request.post_data_json
        assert payload["lectureIds"] == [] and len(payload["regions"]) == 5
        assert response.json()["texts"] == [QUESTION, *OPTIONS]

        expect(dialog).to_be_hidden()
        expect(page.locator("#run")).to_have_text("Остановить")
        expect(page.locator("#readingTranscript")).to_be_visible()
        expect(page.locator("#readText li")).to_have_text(list(OPTIONS))
        expect(page.locator("#answer .answer-title")).to_have_text("Текст распознан", timeout=15000)
        assert not solve_requests, "Reading without selected documents must not look up an answer"
        if directory:
            page.locator("#windowStep").scroll_into_view_if_needed()
            page.screenshot(path=directory / "manual-ocr.png", animations="disabled")
        print("Manual areas started real OCR and showed all four choices with no documents selected.")
    finally:
        context.close()
