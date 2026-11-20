"""Exercise the actual update and component controls with controlled releases on the local origin."""
from playwright.sync_api import expect


def check_local_updates(browser, origin):
    context = browser.new_context()
    page = context.new_page()
    version = "1.8.1"
    successor = "1.8.2"
    state = "current"
    automatic = False
    posts = []
    installs = []
    ocr_status = "missing"

    def updates(route):
        nonlocal state, automatic, version
        if route.request.method == "POST":
            data = route.request.post_data_json
            command = data["action"]
            posts.append(command)
            if command == "preferences":
                automatic = data["automatic"]
            elif command == "check":
                state = "available"
            elif command == "apply":
                state = "ready"
            elif command == "activate":
                state = "restarting"
        route.fulfill(json={"state":state, "supported":True, "automatic":automatic,
                            "currentVersion":version, "latestVersion":successor, "message":state})

    def health(route):
        nonlocal state, version
        if state == "restarting":
            state, version = "current", successor
        route.fulfill(json={"app":"QuizLens", "version":version, "platform":"windows", "nativeCapture":False})

    def ocr(route):
        nonlocal ocr_status
        if route.request.method == "POST":
            installs.append(route.request.post_data_json["action"])
            ocr_status = "installing"
        route.fulfill(json={"platform":"windows", "status":ocr_status, "canInstall":ocr_status == "missing",
                            "message":{"installing":"Устанавливаю OCR", "ready":"Готово", "missing":"Требуется OCR"}[ocr_status]})

    context.route("**/api/update", updates)
    context.route("**/api/health", health)
    context.route("**/api/components/ocr", ocr)
    context.route("**/api/components/extension", lambda route: route.fulfill(json={"path":"C:/QuizLens/browser-extension"}))
    try:
        page.goto(origin)
        expect(page.locator("#extensionDownload")).to_have_attribute("href", "/download/QuizLens-Extension.zip")
        page.locator("[data-extension-folder]").click()
        expect(page.locator("[data-extension-status]")).to_contain_text("C:/QuizLens/browser-extension")
        page.locator("#ocrInstall").click()
        expect(page.locator("#ocrComponentStatus")).to_have_text("Устанавливаю OCR")
        ocr_status = "ready"
        expect(page.locator("#ocrComponentStatus")).to_have_text("Готово")
        assert installs == ["install"]
        expect(page.locator("#ocrInstall")).to_be_hidden()

        blocked = context.new_page()
        blocked.goto(origin + "/demo?lobby=1")
        blocked.locator("#duration").select_option("0")
        blocked.locator("#action").click()
        blocked.wait_for_function("""async () => (await navigator.locks.query()).held.some(lock =>
            lock.name === 'quizlens.active-session' && lock.mode === 'shared')""")
        page.bring_to_front()
        page.locator("#updateAction").click()
        expect(page.locator("#updateAction")).to_have_text("Обновить QuizLens")
        page.locator("#automaticUpdates").check()
        expect(page.locator("#updateMessage")).to_contain_text("когда завершится", timeout=9000)
        assert posts == ["check", "preferences"], posts
        blocked.close()
        expect(page.locator("#installedVersion")).to_have_text("v" + successor, timeout=16000)
        assert posts.count("apply") == 1 and posts.count("activate") == 1, posts
        expect(page.locator("#updateAction")).to_be_enabled()

        page.goto(origin + "/tab-reader-help.html")
        expect(page.locator("[data-extension-folder]")).to_be_visible()
        expect(page.locator("a[download]")).to_have_attribute("href", "/download/QuizLens-Extension.zip")
    finally:
        context.close()
