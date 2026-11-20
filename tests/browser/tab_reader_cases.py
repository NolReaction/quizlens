"""Exercise the extension's rendered DOM reader using the same Chromium build as the UI test."""
from pathlib import Path

READER = Path(__file__).resolve().parents[2] / "browser-extension" / "reader.js"


def check_dom_extraction(page):
    try:
        page.set_viewport_size({"width": 880, "height": 720})
        page.set_content("""
          <section id="quiz">
            <h2 id="prompt">Какой процент посетителей вернулся?</h2>
            <fieldset aria-labelledby="prompt">
              <div style="display:flex;flex-direction:column">
                <label style="order:2"><input type="radio" name="q" value="SECRET">5%</label>
                <label style="order:1"><input type="radio" name="q" value="SECRET"><span>0</span><span>,</span><span>5%</span>
                   <span aria-hidden="true">ключ: вариант 1</span></label>
                <label style="order:3"><input type="radio" name="q" value="SECRET">−5%</label>
                <label hidden><input type="radio" name="q">Скрытый ответ</label>
              </div>
            </fieldset>
          </section>
        """)
        # Only stub the transport: read() still uses real layout, visibility, and accessible names.
        page.evaluate("window.chrome = {runtime:{onMessage:{addListener() {}}}}")
        page.add_script_tag(path=READER)
        read = lambda: page.evaluate("quizLensReadVisibleQuestion()")
        result = read()
        assert result["texts"] == ["Какой процент посетителей вернулся?", "0,5%", "5%", "−5%"], result
        assert result["selectionMode"] == "single"

        page.locator('input[value="SECRET"]').first.evaluate("(node) => {node.disabled=true}")
        assert read() == {"texts": [], "reason": "partial"}
        page.locator("fieldset").evaluate("(node) => { node.disabled=true; }")
        assert read() == {"texts": [], "reason": "waiting"}

        page.evaluate("""() => { document.body.innerHTML = `
          <section><h2>Выбери два показателя</h2><fieldset>
             <label><input type="checkbox">Recall</label><label><input type="checkbox">Precision</label>
          </fieldset></section>
          <section id="next"><h2>Другой вопрос?</h2><fieldset>
             <label><input type="radio" name="q">Нет</label><label><input type="radio" name="q">Да</label>
          </fieldset></section>` }""")
        assert read() == {"texts": [], "reason": "multiple"}
        page.locator("#next").evaluate("(node) => {node.hidden=true}")
        assert read() == {"texts": ["Выбери два показателя", "Recall", "Precision"], "selectionMode": "multiple"}
        page.locator("fieldset").first.locator("label").last.evaluate("(n) => n.style.marginTop='1500px'")
        # Position the option past the actual viewport, as inside a clipped/scrollable list.
        page.locator("fieldset").first.locator("label").last.evaluate("(n) => n.style.display='block'")
        assert read() == {"texts": [], "reason": "partial"}
    finally:
        page.close()
