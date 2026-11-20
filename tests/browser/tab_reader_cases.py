"""Exercise the extension's rendered DOM reader using the same Chromium build as the UI test."""
from pathlib import Path

READER = Path(__file__).resolve().parents[2] / "browser-extension" / "reader.js"
BUTTON_QUESTION = "Какое из этих деревьев хвойное?"
BUTTON_TASK = "Из этих деревьев выберите хвойное"
BUTTON_OPTIONS = ["Липа", "Ольха", "Сосна", "Клён"]


def button_quiz_html(*, marked=False):
    """A practice slide built with plain buttons, visual marks, and nearby controls."""
    options = "".join(
        f'<button type="button"><span>{mark}</span><span>{option}</span>'
        '<span hidden>Скрытые данные варианта</span></button>'
        for mark, option in zip("▲◆●■", BUTTON_OPTIONS)
    )
    prompt = (f'<p class="prompt" data-q="text">{BUTTON_TASK}</p>' if marked else
              f'<div class="prompt">{BUTTON_QUESTION}</div>')
    choices = ' data-q="options"' if marked else ''
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
      <title>Тренировка по ботанике</title>
      <style>
        body {{background:#182238;color:#f4f6fb;font:18px system-ui;margin:12px}}
        main {{max-width:610px;margin:auto}}
        nav {{display:flex;justify-content:space-between;gap:12px}}
        .progress {{margin:10px auto;text-align:center}}
        .prompt {{font-weight:650;text-align:center;margin:12px 0}}
        .choices {{display:flex;flex-direction:column;gap:8px}}
        .choices button {{min-height:106px;border:0;border-radius:10px;padding:12px 18px;
          background:#3565a4;color:white;text-align:left;font:600 18px system-ui}}
        .choices button:nth-child(2) {{background:#8d4059}}
        .choices button:nth-child(3) {{background:#396650}}
        .choices button:nth-child(4) {{background:#a66a34}}
        .choices button span:first-child {{margin-right:10px}}
        .compact .choices button {{min-height:38px}}
      </style>
      <main>
        <nav><button>В меню</button><button>Озвучка</button></nav>
        <div class="progress">Упражнение 2 из 6 · <span role="timer">20 секунд</span></div>
        <section class="slide">
          {prompt}
          <div class="choices"{choices}>{options}</div>
        </section>
      </main></html>"""


def check_dom_extraction(page):
    def load_fixture(html):
        page.set_content(html)
        # Stub only the transport so extraction still uses real DOM layout.
        page.evaluate("window.chrome = {runtime:{onMessage:{addListener() {}}}}")
        page.add_script_tag(path=READER)

    try:
        page.set_viewport_size({"width": 880, "height": 720})
        load_fixture("""
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

        load_fixture(button_quiz_html())
        frame = read()
        assert frame == {"texts": [BUTTON_QUESTION, *BUTTON_OPTIONS], "selectionMode": "single"}, frame

        buttons = page.locator(".choices button")
        buttons.last.evaluate("button => {button.disabled = true}")
        assert read() == {"texts": [], "reason": "partial"}
        page.locator(".choices").evaluate("group => {for (const button of group.children) button.disabled = true}")
        assert read() == {"texts": [], "reason": "waiting"}

        label_bottom = page.locator(".choices").evaluate("""group => {
          for (const button of group.children) button.disabled = false;
          const label = group.lastElementChild.children[1];
          const bottom = label.getBoundingClientRect().bottom - group.getBoundingClientRect().top;
          group.style.maxHeight = `${bottom + 3}px`;
          group.style.overflow = 'auto';
          return bottom;
        }""")
        # A tile border may be clipped while its label is still completely visible.
        assert read() == frame
        page.locator(".choices").evaluate("(group, bottom) => {group.style.maxHeight = `${bottom - 7}px`}",
                                          label_bottom)
        assert read() == {"texts": [], "reason": "partial"}
        page.locator(".choices").evaluate("group => group.removeAttribute('style')")

        page.locator("main").evaluate("""main => {
          document.body.classList.add('compact');
          main.insertAdjacentHTML('beforeend', `
            <section id="other-slide" class="slide">
              <div class="prompt">Какой прибор показывает температуру?</div>
              <div class="choices"><button>Термометр</button><button>Барометр</button></div>
            </section>`);
        }""")
        assert read() == {"texts": [], "reason": "multiple"}
        page.locator(".slide").first.evaluate("slide => {slide.hidden = true}")
        assert read() == {"texts": ["Какой прибор показывает температуру?", "Термометр", "Барометр"],
                          "selectionMode": "single"}

        page.evaluate("""() => { document.body.innerHTML = `
          <nav aria-label="Справка"><div>Открыть настройки?</div>
            <div class="choices"><button>Назад</button><button>Далее</button><button>Помощь</button></div>
          </nav>
          <section><h2>Настройки аккаунта</h2><div class="choices">
            <button>Войти</button><button>Регистрация</button><button>Забыли пароль?</button>
          </div></section>`; }""")
        assert read() == {"texts": [], "reason": "waiting"}

        load_fixture(button_quiz_html(marked=True))
        frame = read()
        assert frame == {"texts": [BUTTON_TASK, *BUTTON_OPTIONS], "selectionMode": "single"}, frame
        title = page.locator("[data-q=text]")
        title.evaluate("node => {node.textContent = 'Выберите 1 вариант'}")
        assert read()["selectionMode"] == "single"
        title.evaluate("node => {node.textContent = 'Выберите 2 варианта'}")
        assert read()["selectionMode"] == "multiple"

        # Public Pikuco reader layout: an ordinary div prompt over an image and native labels.
        load_fixture("""
          <style>
            .test_window { margin:60px auto; width:700px; }
            .question_block { display:flex; gap:10px; margin-top:12px; }
            .question_picture { width:420px; height:360px; background:linear-gradient(#555,#333); }
            .answers { width:260px; }
            .answers_wrapper { display:grid; gap:5px; }
            .answer input { display:none; }
            .answer label { display:grid; place-items:center; height:86px; color:white; background:#263238; }
            .choose_title { margin:0 0 4px; text-align:right; }
          </style>
          <header>Тренировка · Вопрос 2 / 20</header>
          <div id="test-interface">
            <div class="test_window">
              <div class="question">
                <div class="question_name">Какая планета ближе всего к Солнцу?</div>
                <div class="question_description">Вопрос про астрономию</div>
                <div class="question_block">
                  <div class="question_picture"></div>
                  <div class="answers">
                    <p class="choose_title">0 / 1</p>
                    <div class="answers_wrapper">
                      <div class="answer"><input id="pikuco0" type="radio" name="ANSWER" value="internal0">
                        <label for="pikuco0">Марс <span hidden>Скрытые данные</span></label></div>
                      <div class="answer"><input id="pikuco1" type="radio" name="ANSWER" value="internal1">
                        <label for="pikuco1">Меркурий</label></div>
                      <div class="answer"><input id="pikuco2" type="radio" name="ANSWER" value="internal2">
                        <label for="pikuco2">Венера</label></div>
                      <div class="answer"><input id="pikuco3" type="radio" name="ANSWER" value="internal3">
                        <label for="pikuco3">Сатурн</label></div>
                    </div>
                    <div class="submit_question">Далее</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        """)
        pikuco = {"texts": ["Какая планета ближе всего к Солнцу?", "Марс", "Меркурий", "Венера", "Сатурн"],
                  "selectionMode": "single"}
        assert read() == pikuco
        page.locator(".answers_wrapper").evaluate("""node => {
            node.style.maxHeight = '300px';
            node.style.overflow = 'auto';
        }""")
        assert read() == {"texts": [], "reason": "partial"}
        page.locator(".answers_wrapper").evaluate("""node => {
            node.removeAttribute('style');
            for (const input of node.querySelectorAll('input')) input.type = 'checkbox';
        }""")
        assert read() == {**pikuco, "selectionMode": "multiple"}
        page.locator(".question_name").evaluate("node => {node.hidden = true}")
        assert read() == {"texts": [], "reason": "partial"}
    finally:
        page.close()
