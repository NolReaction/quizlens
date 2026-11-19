'use strict';

const el = id => document.getElementById(id);
const formatDate = new Intl.DateTimeFormat('ru-RU', {
  day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'
});
let entries = [];
let listVersion = 0;
let detailVersion = 0;
let activeId = '';
let modelNames = [];

async function api(path, body) {
  const options = body === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}

function node(tag, text = '', className = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function notice(message = '') {
  el('notice').textContent = message;
  el('notice').hidden = !message;
}

function numberList(choices) { return choices.map(n => '№' + n).join(', '); }
function duration(ms) { return (ms / 1000).toLocaleString('ru-RU', {maximumFractionDigits:2}) + ' с'; }
function validHash() {
  const value = location.hash.slice(1);
  return /^[0-9a-f]{32}$/.test(value) ? value : '';
}

function renderList() {
  const list = el('entryList');
  list.replaceChildren();
  for (const item of entries) {
    const link = node('a', '', 'entry-link');
    link.href = '#' + item.id;
    if (item.id === activeId) link.setAttribute('aria-current', 'true');
    const state = item.corrected ? 'Моя карточка' : item.answered ? 'Есть ответ' : 'Без ответа';
    link.append(node('small', formatDate.format(new Date(item.createdAt)) + ' · ' + state),
                node('span', item.question));
    list.append(link);
  }
  el('entryCount').textContent = String(entries.length);
}

async function refresh(pickEntry = true) {
  const version = ++listVersion;
  try {
    const data = await api('/api/journal');
    if (version !== listVersion) return;
    entries = data.entries;
    el('empty').hidden = entries.length > 0;
    el('workspace').hidden = entries.length === 0;
    if (!entries.length) {
      activeId = '';
      detailVersion++;
      history.replaceState(null, '', location.pathname);
      return;
    }
    const requested = validHash();
    const id = entries.some(item => item.id === requested) ? requested : entries[0].id;
    if (id !== requested) history.replaceState(null, '', '#' + id);
    renderList();
    if (pickEntry || id !== activeId) await selectEntry(id);
  } catch (error) {
    if (version === listVersion) notice(error.message);
  }
}

async function selectEntry(id) {
  if (!id) return;
  const version = ++detailVersion;
  activeId = id;
  renderList();
  const detail = el('entry');
  detail.textContent = 'Открываю вопрос…';
  try {
    const data = await api('/api/journal/entry?id=' + encodeURIComponent(id));
    if (version === detailVersion) renderEntry(data.entry);
  } catch (error) {
    if (version === detailVersion) detail.textContent = error.message;
  }
}

function block(title, expanded = true) {
  const element = node(expanded ? 'section' : 'details', '', 'study-block');
  element.append(node(expanded ? 'h3' : 'summary', title));
  return element;
}

function proof(parent, label, source, quote) {
  const part = node('div', '', 'proof-item');
  if (label) part.append(node('small', label));
  if (quote) part.append(node('blockquote', quote));
  if (source) part.append(node('p', source, 'meta'));
  parent.append(part);
}

function timingInfo(row) {
  const line = node('div', '', 'timing');
  if (row.captureMs != null) line.append(node('small', 'Подтверждение кадра до запроса · ' + duration(row.captureMs)));
  if (row.solveMs != null) line.append(node('small', 'Поиск и ответ · ' + duration(row.solveMs)));
  return line;
}

function renderTrace(row) {
  const frames = row.captureTrace || [];
  const snapshotText = (value, limit) => Array.from(value.trim().replace(/\s+/gu, ' ')).slice(0, limit).join('');
  const question = snapshotText(row.question, 500);
  const options = row.options.map(value => snapshotText(value, 350));
  const differs = frame => frame.question !== question || frame.options.length !== options.length ||
    frame.options.some((value, i) => value !== options[i]);
  if (!frames.some(frame => frame.status !== 'complete' || differs(frame))) return null;

  const replay = block('Что видел OCR до ответа', false);
  const labels = {partial:'Квиз виден частично', unstable:'Текст менялся', complete:'Прочитан полный вопрос'};
  for (const frame of frames) {
    const observation = node('section', '', 'trace-frame');
    observation.append(node('h3', labels[frame.status]), node('small',
      frame.ageMs === 0 ? 'В момент запроса подсказки' : 'За ' + duration(frame.ageMs) + ' до запроса подсказки'));
    if (!frame.question && !frame.options.some(Boolean)) {
      observation.append(node('p', 'Видна часть вопроса или вариантов. Текст этого кадра не распознан.'));
    } else {
      if (frame.question && frame.question !== question) observation.append(node('p', 'Вопрос · ' + frame.question));
      if (!frame.question) observation.append(node('p', 'Вопрос не прочитан.'));
      if (frame.options.length !== options.length || frame.options.some(value => !value)) {
        observation.append(node('p', 'Прочитано вариантов · ' + frame.options.filter(Boolean).length));
      }
      frame.options.forEach((value, i) => {
        if (value && value !== options[i]) observation.append(node('p', (i + 1) + '. ' + value));
      });
      if (!differs(frame)) observation.append(node('p', 'Текст совпадает с переданным на разбор.'));
    }
    replay.append(observation);
  }
  return replay;
}

function addReplay(parent, row) {
  const result = row.response;
  const choices = result.choices || (result.choice ? [result.choice] : []);
  const recognized = block('Распознанные варианты');
  const list = node('ol', '', 'answer-list');
  row.options.forEach((text, i) => list.append(node('li', text, choices.includes(i + 1) ? 'marked' : '')));
  recognized.append(list);
  parent.append(recognized);
  const trace = renderTrace(row);
  if (trace) parent.append(trace);

  const titles = {model:'Выдержки из лекции', exact:'Совпадение с готовой карточкой',
    correction:'Совпадение с твоей карточкой', retrieval:'Найденная выдержка'};
  const proofs = block(titles[result.kind] || 'Источник подсказки', false);
  if (result.evidence?.length) {
    for (const item of result.evidence) proof(proofs, 'Вариант ' + item.choice, item.source, item.excerpt);
  } else if (result.excerpt || result.source) {
    proof(proofs, '', result.source, result.excerpt);
  } else {
    proofs.append(node('p', 'В этой записи нет выдержки.'));
  }
  parent.append(proofs);

  const answer = block('Ответ при захвате');
  answer.append(node('p', choices.length ? numberList(choices) : result.kind === 'error'
    ? 'Поиск прервался.' : 'Подтверждённого варианта нет.'));
  if (result.detail) answer.append(node('p', result.detail, 'meta'));
  if (row.correctionChoices.length) answer.append(node('p', 'Твоя карточка сейчас · ' + numberList(row.correctionChoices)));
  answer.append(timingInfo(row));
  parent.append(answer);
}

function addExplanation(parent, row) {
  const section = block('Почему этот вариант подходит или ошибочен?');
  const form = node('div', '', 'explain-form');
  const select = node('select');
  select.setAttribute('aria-label', 'Вариант для разбора');
  row.options.forEach((text, i) => select.add(new Option((i + 1) + '. ' + text, String(i + 1))));
  const chosen = row.response.choices || [];
  const alternate = row.options.findIndex((_text, i) => !chosen.includes(i + 1));
  if (alternate !== -1) select.value = String(alternate + 1);
  const go = node('button', 'Разобрать по лекции', 'button');
  go.type = 'button';
  form.append(select, go);
  const model = node('select');
  model.setAttribute('aria-label', 'Модель для разбора');
  const names = [...new Set([row.model, ...modelNames].filter(Boolean))];
  model.add(new Option('Поиск по карточкам и лекции', ''));
  names.forEach(name => model.add(new Option(name, name)));
  model.value = names.includes(row.model) ? row.model : names[0] || '';
  const settings = node('details');
  settings.append(node('summary', 'Модель для разбора'), model);
  const output = node('div', '', 'explanation');
  output.setAttribute('role', 'status');
  select.onchange = model.onchange = () => output.replaceChildren();
  go.onclick = async () => {
    const option = Number(select.value);
    go.disabled = select.disabled = model.disabled = true;
    output.textContent = 'Сверяю вариант с лекцией…';
    try {
      const data = await api('/api/journal/explain', {id:row.id, option, model:model.value});
      const verdict = {supports:'Есть подтверждение', contradicts:'Есть основание отклонить вариант',
        insufficient:'Недостаточно сведений'};
      output.replaceChildren(node('p', 'Вариант ' + option + ' · ' + verdict[data.verdict]));
      if (data.explanation) output.append(node('p', data.explanation));
      if (data.source || data.excerpt) proof(output, '', data.source, data.excerpt);
    } catch (error) { output.textContent = error.message; }
    finally { go.disabled = select.disabled = model.disabled = false; }
  };
  section.append(form, settings, output);
  parent.append(section);
}

function notifyCorrection() {
  try { localStorage.setItem('quizlens.corrections.rev', String(Date.now()) + '-' + Math.random()); }
  catch { /* The saved card is already available to subsequent server requests. */ }
}

function addCorrection(parent, row) {
  const section = block(row.correctionChoices.length ? 'Изменить мою карточку' : 'Исправить и сохранить ответ', false);
  const label = row.selectionMode === 'single' ? 'Отметь один правильный вариант.' :
    row.expectedCount ? 'Сколько вариантов нужно отметить · ' + row.expectedCount : 'Отметь все правильные варианты.';
  section.append(node('p', label + ' Карточка будет действовать для этого вопроса при том же выборе лекций.'));
  const checklist = node('fieldset', '', 'correction-list');
  checklist.append(node('legend', 'Правильные варианты', 'sr-only'));
  const selected = new Set(row.correctionChoices.length ? row.correctionChoices : row.response.choices || []);
  const inputs = row.options.map((option, i) => {
    const wrapper = node('label');
    const input = node('input');
    input.name = 'correct-answer';
    input.type = row.selectionMode === 'single' ? 'radio' : 'checkbox';
    input.checked = selected.has(i + 1);
    input.value = String(i + 1);
    wrapper.append(input, node('span', (i + 1) + '. ' + option));
    checklist.append(wrapper);
    return input;
  });
  const save = node('button', 'Сохранить карточку', 'button primary');
  const forget = node('button', 'Убрать мою карточку', 'button text-button');
  forget.hidden = !row.correctionChoices.length;
  const status = node('p', '', 'save-feedback');
  status.setAttribute('role', 'status');
  const controls = node('div', '', 'explain-form');
  controls.append(save, forget);
  async function persist(isForget) {
    const choices = inputs.filter(input => input.checked).map(input => Number(input.value));
    if (!isForget && (!choices.length || (row.expectedCount && choices.length !== row.expectedCount))) {
      status.textContent = row.expectedCount ? 'Нужно отметить ровно ' + row.expectedCount + '.' : 'Отметь хотя бы один вариант.';
      return;
    }
    if (isForget && !confirm('Убрать сохранённый ответ для этого вопроса? Записи журнала сохранятся.')) return;
    save.disabled = forget.disabled = checklist.disabled = true;
    try {
      const data = await api('/api/journal/' + (isForget ? 'forget' : 'correct'), isForget ? {id:row.id} : {id:row.id, choices});
      notifyCorrection();
      if (row.id === activeId) {
        renderEntry(data.entry);
        notice(isForget ? 'Карточка убрана.' : 'Ответ сохранён в твою карточку.');
        await refresh(false);
      }
    } catch (error) { status.textContent = error.message; }
    finally { save.disabled = forget.disabled = checklist.disabled = false; }
  }
  save.onclick = () => persist(false);
  forget.onclick = () => persist(true);
  section.append(checklist, controls, status);
  parent.append(section);
}

function renderEntry(row) {
  const parent = el('entry');
  const channel = row.channel === 'native' ? 'Окно macOS' : 'Захват в браузере';
  parent.replaceChildren(node('p', formatDate.format(new Date(row.createdAt)) + ' · ' + channel, 'meta'),
    node('h2', row.question));
  addReplay(parent, row);
  addExplanation(parent, row);
  addCorrection(parent, row);
  const remove = node('button', 'Удалить запись', 'mini-button');
  remove.onclick = async () => {
    const saved = row.correctionChoices.length ? ' Твоя карточка сохранится.' : '';
    if (!confirm('Удалить запись об этом захвате?' + saved)) return;
    remove.disabled = true;
    try {
      await api('/api/journal/delete', {id:row.id});
      notice('Запись удалена.');
      await refresh();
    } catch (error) { notice(error.message); remove.disabled = false; }
  };
  parent.append(remove);
}

window.addEventListener('hashchange', () => {
  notice();
  const id = validHash();
  if (id) selectEntry(id); else refresh();
});
el('refresh').onclick = () => { notice(); refresh(); };
async function start() {
  // The journal stays available even if Bionic has been closed after the quiz.
  const models = api('/api/models').then(data => {
    modelNames = data.models || [];
  }).catch(() => {});
  await refresh();
  await models;
  // Populate the current selector without discarding an in-progress review.
  const select = document.querySelector('select[aria-label="Модель для разбора"]');
  if (select) {
    const ids = new Set(Array.from(select.options, item => item.value));
    for (const name of modelNames) if (!ids.has(name)) select.add(new Option(name, name));
    if (select.options.length > 1 && !select.value && !select.disabled) select.value = select.options[1].value;
  }
}
start();
