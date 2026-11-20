'use strict';

window.QuizSources = (() => {
  let dialog;
  const revisionKey = 'quizlens.sources.revision';

  function changed() {
    try { localStorage.setItem(revisionKey, crypto.randomUUID()); } catch { /* Storage may be disabled. */ }
  }

  async function request(path, options) {
    const response = await fetch(path, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось открыть документ.');
    return data;
  }

  function mount() {
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.id = 'sourceDialog';
    dialog.setAttribute('aria-labelledby', 'sourceDialogTitle');
    dialog.innerHTML = `
      <header><h2 id="sourceDialogTitle" tabindex="-1"></h2></header>
      <div id="sourceContent"></div>
      <p id="sourceProgress" role="status"></p>
      <footer>
        <div class="source-navigation">
          <button type="button" id="sourcePrevious" class="button">Назад</button>
          <button type="button" id="sourceNext" class="button">Дальше</button>
        </div>
        <button type="button" id="sourceClose" class="button">Закрыть</button>
        <button type="button" id="sourceConfirm" class="button source-danger" hidden>Удалить из QuizLens</button>
      </footer>`;
    document.body.append(dialog);
    dialog.querySelector('#sourceClose').onclick = () => dialog.close();
    dialog.addEventListener('cancel', event => {
      if (dialog.getAttribute('aria-busy') === 'true') event.preventDefault();
    });
    return dialog;
  }

  function openDialog(item, deleting) {
    const modal = mount();
    const get = id => modal.querySelector('#' + id);
    const title = get('sourceDialogTitle');
    title.textContent = deleting ? 'Удалить «' + item.name + '»?' : item.name;
    get('sourceContent').replaceChildren();
    get('sourceProgress').textContent = '';
    get('sourceConfirm').hidden = !deleting;
    get('sourceConfirm').disabled = false;
    get('sourceClose').textContent = deleting ? 'Отмена' : 'Закрыть';
    get('sourceClose').disabled = false;
    modal.querySelector('.source-navigation').hidden = deleting;
    modal.removeAttribute('aria-busy');
    modal.showModal();
    (deleting ? get('sourceClose') : title).focus();
    return get;
  }

  async function preview(item) {
    const get = openDialog(item, false);
    const content = get('sourceContent');
    const progress = get('sourceProgress');
    const previous = get('sourcePrevious');
    const next = get('sourceNext');
    const controller = new AbortController();
    dialog.addEventListener('close', () => controller.abort(), {once:true});
    async function page(number) {
      previous.disabled = true;
      next.disabled = true;
      progress.textContent = 'Открываю фрагменты…';
      try {
        const data = await request('/api/lectures/preview?' + new URLSearchParams({id:item.id, page:number}), {signal:controller.signal});
        if (controller.signal.aborted || !dialog.open) return;
        content.replaceChildren();
        for (const part of data.sections) {
          const section = document.createElement('section');
          const heading = document.createElement('h3');
          heading.textContent = part.location;
          const paragraph = document.createElement('p');
          paragraph.textContent = part.text;
          section.append(heading, paragraph);
          content.append(section);
        }
        content.scrollTop = 0;
        previous.disabled = data.page === 1;
        next.disabled = data.page === data.pages;
        previous.onclick = () => page(data.page - 1);
        next.onclick = () => page(data.page + 1);
        progress.textContent = 'Страница ' + data.page + ' из ' + data.pages + ' · фрагментов ' + data.count;
      } catch (error) {
        if (!controller.signal.aborted) progress.textContent = error.message;
      }
    }
    await page(1);
  }

  function confirmRemoval(item, onDelete, list) {
    const get = openDialog(item, true);
    get('sourceContent').textContent = 'QuizLens удалит копию для поиска. Исходный файл и записи в журнале сохранятся.';
    get('sourceConfirm').onclick = async () => {
      dialog.setAttribute('aria-busy', 'true');
      get('sourceConfirm').disabled = true;
      get('sourceClose').disabled = true;
      get('sourceProgress').textContent = 'Удаляю документ…';
      try {
        await request('/api/lectures/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:item.id})});
        changed();
        await onDelete(item.id);
        dialog.close();
        list.closest('details')?.querySelector('summary')?.focus();
      } catch (error) {
        get('sourceProgress').textContent = error.message;
      } finally {
        dialog.removeAttribute('aria-busy');
        get('sourceConfirm').disabled = false;
        get('sourceClose').disabled = false;
      }
    };
  }

  function action(label, name, run) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'source-action';
    button.textContent = label;
    button.setAttribute('aria-label', label + ' «' + name + '»');
    button.onclick = run;
    return button;
  }

  function render(list, {lectures, selected, onToggle, onDelete}) {
    const groups = [
      ['Твои документы', lectures.filter(item => !item.builtIn)],
      ['Примеры для пробного квиза', lectures.filter(item => item.builtIn)]
    ];
    list.replaceChildren();
    if (!lectures.length) list.textContent = 'Добавь первый документ для поиска ответов.';
    for (const [title, items] of groups) {
      if (!items.length) continue;
      const group = document.createElement('h3');
      group.className = 'source-group';
      group.textContent = title;
      list.append(group);
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'source-row';
        const label = document.createElement('label');
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.value = item.id;
        check.checked = selected.has(item.id);
        check.onchange = () => onToggle(item.id, check.checked);
        const name = document.createElement('span');
        name.textContent = item.name;
        label.append(check, name);
        const actions = document.createElement('div');
        actions.className = 'source-actions';
        actions.append(action('Посмотреть', item.name, () => preview(item)));
        if (!item.builtIn) actions.append(action('Удалить', item.name, () => confirmRemoval(item, onDelete, list)));
        row.append(label, actions);
        list.append(row);
      }
    }
  }

  mount();
  return {render, mount, changed, revisionKey};
})();
