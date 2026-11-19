'use strict';

const byId = id => document.getElementById(id);
const LIMIT = 15000;
const STORAGE = 'quizlens.practice.decks.v1';
let builtinDeck = null;
let lectures = [];
let deck = [];
let lectureId = '';
let busy = false;
let generated = false;
let session = null;
let timer = null;
let generationRevision = 0;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function request(path, payload, signal) {
  const init = {signal};
  if (payload !== undefined) Object.assign(init, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const response = await fetch(path, init);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Ошибка локального сервера.');
  return result;
}

function shuffle(values) {
  const copy = [...values];
  for (let i = copy.length - 1; i > 0; i--) {
    const next = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[next]] = [copy[next], copy[i]];
  }
  return copy;
}

function sameSelection(a, b) {
  return a.length === b.length && new Set(a).size === a.length && b.every(i => a.includes(i));
}

function clockTime(ms) {
  return (ms / 1000).toLocaleString('ru-RU', {maximumFractionDigits:1}) + ' с';
}

function storedDecks() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE));
    return Array.isArray(data) ? data : [];
  } catch { return []; }
}

function saveDeck(cards) {
  const entries = storedDecks().filter(item => item.id !== lectureId);
  entries.unshift({id:lectureId, cards});
  try {
    localStorage.setItem(STORAGE, JSON.stringify(entries.slice(0, 8)));
    return true;
  } catch { return false; }
}

function selectMaterial() {
  generationRevision++;
  const selection = byId('material').value;
  generated = selection !== 'builtin';
  lectureId = generated ? selection : (lectures.find(l => l.name === builtinDeck?.lectureName)?.id || '');
  deck = generated ? storedDecks().find(row => row.id === lectureId)?.cards || [] : builtinDeck?.cards || [];
  byId('generation').hidden = !generated;
  byId('review').replaceChildren();
  byId('materialNote').textContent = generated
    ? (deck.length ? 'Сохранено проверенных тобой карточек — ' + deck.length + '.' : 'Модель предложит пары вопросов. Сверь правильные варианты с цитатами перед тренировкой.')
    : 'Десять вопросов с изменяемыми условиями. На каждый ответ даётся 15 секунд.';
  byId('status').textContent = '';
  updateSetup();
}

function updateSetup() {
  const hasModel = Boolean(byId('localModel').value);
  byId('material').disabled = busy;
  byId('localModel').disabled = busy;
  byId('refresh').disabled = busy;
  byId('practice').disabled = busy || !deck.length;
  byId('bench').disabled = busy || !hasModel || !lectureId || !deck.length;
  byId('generate').disabled = busy || !hasModel || !lectureId;
  byId('generate').textContent = deck.length && generated ? 'Подготовить новый набор' : 'Подготовить вопросы';
}

async function loadModels() {
  const model = byId('localModel');
  const previous = model.value;
  busy = true;
  updateSetup();
  let models = [];
  try {
    models = (await request('/api/models')).models || [];
  } catch { /* Practice runs locally even while the model server is stopped. */ }
  model.replaceChildren(new Option('Модель не выбрана', ''));
  models.forEach(name => model.add(new Option(name, name)));
  model.value = models.includes(previous) ? previous : models[0] || '';
  byId('status').textContent = models.length ? '' : 'Можно решать самому. Для проверки модели запусти сервер в LM Studio и нажми «Обновить».';
  busy = false;
  updateSetup();
}

function cite(card) {
  const details = node('details', undefined, 'proof');
  details.append(node('summary', 'Фрагмент лекции'), node('p', card.excerpt), node('small', card.source));
  return details;
}

function createDrafts(cards) {
  const root = byId('review');
  root.replaceChildren(node('p', 'Модель предложила черновики. Проверь обе версии вопроса и укажи по одному верному ответу.', 'hint'));
  const editors = [];
  for (const [i, card] of cards.entries()) {
    const box = node('div', undefined, 'draft-card');
    const keep = node('input');
    keep.type = 'checkbox';
    const approved = node('label', undefined, 'keep');
    approved.append(keep, ' Я сверил обе версии с лекцией');
    const rows = [];
    box.append(node('h3', 'Карточка ' + (i + 1)));
    for (const [v, variant] of card.variants.entries()) {
      const field = node('fieldset');
      field.append(node('legend', variant.question));
      const radios = [];
      card.options.forEach((opt, n) => {
        const radio = node('input');
        radio.type = 'radio';
        radio.name = 'draft-' + i + '-' + v;
        radio.checked = variant.correct.includes(n + 1);
        radio.addEventListener('change', () => { keep.checked = false; });
        const label = node('label');
        label.append(radio, ' ' + opt);
        field.append(label);
        radios.push(radio);
      });
      rows.push(radios);
      box.append(field);
    }
    const source = cite(card);
    source.open = true;
    box.append(source, approved);
    root.append(box);
    editors.push({card, keep, rows});
  }
  const save = node('button', 'Сохранить проверенные', 'button');
  save.type = 'button';
  save.onclick = () => {
    const accepted = [];
    for (const {card, keep, rows} of editors) {
      if (!keep.checked) continue;
      const choices = rows.map(radios => radios.findIndex(r => r.checked) + 1);
      if (choices[0] === choices[1]) {
        byId('status').textContent = 'В этой паре изменение условия должно менять правильный ответ. Исправь варианты или сними отметку с карточки.';
        return;
      }
      accepted.push({...card, variants:card.variants.map((v, i) => ({...v, correct:[choices[i]],
        reason:v.correct[0] === choices[i] ? v.reason : 'Ответ исправлен при сверке с фрагментом лекции.'}))});
    }
    if (!accepted.length) {
      byId('status').textContent = 'Отметь хотя бы одну проверенную карточку.';
      return;
    }
    deck = accepted;
    const persisted = saveDeck(accepted);
    byId('review').replaceChildren();
    byId('materialNote').textContent = 'Проверенных тобой карточек — ' + deck.length + '.';
    byId('status').textContent = persisted ? 'Набор сохранён в этом браузере.' : 'Набор доступен до закрытия страницы.';
    updateSetup();
  };
  root.append(save);
}

async function generate() {
  const revision = ++generationRevision;
  busy = true;
  updateSetup();
  byId('review').replaceChildren();
  byId('status').textContent = 'Готовлю вопросы по выбранной лекции на этом устройстве…';
  try {
    const result = await request('/api/train/generate', {model:byId('localModel').value, lectureIds:[lectureId]});
    if (generationRevision !== revision) return;
    createDrafts(result.cards);
    byId('status').textContent = '';
  } catch (error) {
    if (generationRevision === revision) byId('status').textContent = error.message;
  } finally {
    busy = false;
    updateSetup();
  }
}

function begin(kind) {
  const model = byId('localModel').value;
  if (!deck.length || (kind === 'bench' && (!model || !lectureId))) return;
  session = {kind, model, records:[], cards:shuffle(deck), position:0, lecture:lectureId,
    generated, controller:null, requestNo:0};
  byId('setup').hidden = true;
  byId('report').hidden = true;
  byId('round').hidden = false;
  firstVersion();
}

function firstVersion() {
  const card = session.cards[session.position];
  session.card = card;
  session.order = shuffle(card.options.map((_, i) => i));
  session.previous = null;
  session.alternate = false;
  showVersion(Math.floor(Math.random() * card.variants.length));
}

function showVersion(index) {
  const state = session;
  const {card, order} = state;
  state.version = index;
  state.answered = false;
  const variant = card.variants[index];
  byId('question').textContent = variant.question;
  byId('position').textContent = (state.kind === 'bench' ? 'Проверка модели' : 'Самостоятельно') + ' · ' +
    (state.position + 1) + ' из ' + state.cards.length;
  byId('feedback').replaceChildren();
  byId('changes').hidden = true;
  byId('next').hidden = true;
  const opts = byId('answers');
  opts.replaceChildren();
  const benchmark = state.kind === 'bench';
  byId('instruction').textContent = benchmark ? 'Модель ищет ответ по тексту лекции…' :
    card.mode === 'multiple' ? 'Можно отметить несколько.' : 'Выбери один ответ.';
  for (const [i, old] of order.entries()) {
    const label = node('label', undefined, 'option');
    const input = node('input');
    input.type = card.mode === 'multiple' ? 'checkbox' : 'radio';
    input.name = 'choice';
    input.value = String(i + 1);
    input.disabled = benchmark;
    input.addEventListener('change', () => { byId('submit').disabled = !opts.querySelector('input:checked'); });
    label.append(input, (i + 1) + '. ' + card.options[old]);
    opts.append(label);
  }
  byId('submit').hidden = benchmark;
  byId('submit').disabled = true;
  clearInterval(timer);
  state.started = performance.now();
  const tick = () => {
    if (state !== session || state.answered) return;
    const left = Math.max(0, LIMIT - (performance.now() - state.started));
    byId('time').textContent = Math.ceil(left / 1000) + ' с';
    byId('timeBar').value = left / 1000;
    if (left > 0) return;
    clearInterval(timer);
    if (!benchmark) finishAnswer([], performance.now() - state.started);
    else byId('instruction').textContent = '15 секунд истекли. Жду завершения запроса для разбора.';
  };
  tick();
  timer = setInterval(tick, 120);
  if (benchmark) runModel(state, variant);
}

async function runModel(state, variant) {
  const token = ++state.requestNo;
  const controller = new AbortController();
  state.controller = controller;
  const timeout = setTimeout(() => controller.abort(), 35000);
  const expected = state.card.mode === 'single' ? 1 : null;
  // Only visible question data reaches the solver. Keys, source excerpts and
  // the altered condition are held locally for scoring after the response.
  const payload = {question:variant.question, options:state.order.map(i => state.card.options[i]),
    model:state.model, lectureIds:[state.lecture], selectionMode:state.card.mode,
    expectedCount:expected, record:false, channel:'training'};
  let selected = [], message = '';
  try {
    const answer = await request('/api/solve', payload, controller.signal);
    selected = answer.choices ?? (answer.choice ? [answer.choice] : []);
    if (!Array.isArray(selected)) selected = [];
    if (!selected.length) message = answer.detail || 'Модель не подтвердила ответ.';
  } catch (error) {
    message = error.name === 'AbortError' ? 'Время запроса истекло.' : error.message;
  } finally { clearTimeout(timeout); }
  if (state !== session || token !== state.requestNo || state.answered) return;
  state.controller = null;
  finishAnswer(selected, performance.now() - state.started, message);
}

function expectedPositions(state) {
  const expected = state.card.variants[state.version].correct;
  return state.order.flatMap((old, n) => expected.includes(old + 1) ? [n + 1] : []);
}

function answerNames(card, variant) {
  return variant.correct.map(n => card.options[n - 1]).join(' · ');
}

function finishAnswer(selected, ms, message = '') {
  const state = session;
  if (!state || state.answered) return;
  clearInterval(timer);
  state.answered = true;
  const {card} = state;
  const variant = card.variants[state.version];
  const expected = expectedPositions(state);
  const inTime = ms <= LIMIT;
  if (state.kind === 'practice' && !inTime) selected = [];
  const correct = sameSelection(selected, expected);
  const title = correct ? (inTime ? 'Верно' : 'Верно, после таймера') :
    !selected.length ? (message ? 'Ответ не получен' : 'Время вышло') : 'Есть расхождение';
  const record = {card, variant, correct, inTime, ms, message, title,
    picked:selected.map(i => card.options[state.order[i - 1]]).filter(Boolean)};
  if (!state.alternate) state.records.push(record);
  byId('time').textContent = clockTime(ms);
  byId('timeBar').value = Math.max(0, LIMIT - ms) / 1000;
  byId('instruction').textContent = '';
  for (const input of byId('answers').querySelectorAll('input')) {
    input.disabled = true;
    input.checked = selected.includes(Number(input.value));
  }
  byId('submit').hidden = true;
  const feedback = byId('feedback');
  feedback.replaceChildren(node('h3', title, correct && inTime ? 'result-note' : 'miss'));
  if (message) feedback.append(node('p', message, 'hint'));
  if (state.previous) {
    const previous = state.previous;
    feedback.append(node('p', 'До изменения · ' + previous.value + ' → ' + answerNames(card, previous)),
                    node('p', previous.reason, 'hint'));
  }
  const label = state.previous ? 'После изменения' : 'Правильный ответ';
  feedback.append(node('p', label + ' · ' + answerNames(card, variant)), node('p', variant.reason, 'hint'), cite(card));
  if (state.kind === 'practice') {
    const selector = byId('condition');
    selector.replaceChildren();
    card.variants.forEach((v, i) => { if (i !== state.version) selector.add(new Option(v.value, String(i))); });
    byId('changes').hidden = false;
  }
  byId('next').textContent = state.position + 1 === state.cards.length ? 'Показать результат' : 'Дальше';
  byId('next').hidden = false;
  if (state.kind === 'bench') {
    // Record immediately; keep the full breakdown on the report.
    state.advance = setTimeout(() => { if (session === state && state.answered) next(); }, 650);
  }
}

function next() {
  if (!session || !session.answered) return;
  clearTimeout(session.advance);
  if (++session.position >= session.cards.length) end();
  else firstVersion();
}

function end() {
  const state = session;
  if (!state) return;
  session = null;
  clearInterval(timer);
  clearTimeout(state.advance);
  state.controller?.abort();
  byId('round').hidden = true;
  byId('report').hidden = false;
  const rows = state.records;
  const timely = rows.filter(r => r.correct && r.inTime).length;
  byId('score').textContent = rows.length ? timely + ' из ' + rows.length + ' правильно за 15 секунд' : 'Серия завершена до первого ответа';
  const model = state.kind === 'bench' ? state.model + ' · от показа готового текста до ответа, включая поиск. ' : '';
  const source = state.generated ? 'Эталоны проверены тобой.' : 'Эталоны взяты из встроенных задач.';
  const partial = rows.length < state.cards.length ? ' Завершено вопросов из серии — ' + rows.length + ' из ' + state.cards.length + '.' : '';
  byId('scope').textContent = model + source + partial;
  const summary = byId('summary');
  summary.replaceChildren();
  rows.forEach((r, i) => {
    const details = node('details');
    details.append(node('summary', (i + 1) + '. ' + r.variant.question),
                   node('small', r.title + ' · ' + clockTime(r.ms)),
                   node('small', 'Полученный ответ · ' + (r.picked.join(' · ') || 'нет')),
                   node('small', 'Правильный ответ · ' + answerNames(r.card, r.variant)),
                   node('p', r.variant.reason), cite(r.card));
    if (r.message) details.append(node('small', r.message));
    summary.append(details);
  });
}

byId('material').onchange = selectMaterial;
byId('localModel').onchange = updateSetup;
byId('refresh').onclick = loadModels;
byId('generate').onclick = generate;
byId('practice').onclick = () => begin('practice');
byId('bench').onclick = () => begin('bench');
byId('submit').onclick = () => {
  if (session && !session.answered) finishAnswer([...byId('answers').querySelectorAll('input:checked')].map(input => Number(input.value)),
    performance.now() - session.started);
};
byId('next').onclick = next;
byId('stop').onclick = end;
byId('tryChange').onclick = () => {
  if (!session?.answered || session.kind !== 'practice') return;
  session.previous = session.card.variants[session.version];
  session.alternate = true;
  showVersion(Number(byId('condition').value));
};
byId('restart').onclick = () => { byId('report').hidden = true; byId('setup').hidden = false; updateSetup(); };

async function init() {
  try {
    const [material, library] = await Promise.all([request('/api/train'), request('/api/lectures')]);
    builtinDeck = material;
    lectures = library.lectures;
    for (const item of lectures.filter(l => !l.name.endsWith('.quiz.json'))) {
      byId('material').add(new Option('Свои вопросы · ' + item.name, item.id));
    }
    selectMaterial();
    await loadModels();
  } catch (error) { byId('status').textContent = error.message; }
}

init();
