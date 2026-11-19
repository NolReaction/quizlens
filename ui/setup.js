'use strict';

const element = id => document.getElementById(id);
const preferenceKey = 'quizlens.settings.v1';
const historyKey = 'quizlens.advisor.bench.v1';
const systems = {macos:'macOS', windows:'Windows', linux:'Linux', unknown:'Система неизвестна'};
let localAdvice = null;
let manual = false;
let viewVersion = 0;
let runNumber = 0;
let history = readHistory();

async function request(path, payload) {
  const options = {cache:'no-store'};
  if (payload !== undefined) {
    Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Нет ответа от QuizLens.');
  return data;
}

function textNode(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function preference() {
  try { return JSON.parse(localStorage.getItem(preferenceKey)) || {}; } catch { return {}; }
}

function readHistory() {
  try {
    const rows = JSON.parse(localStorage.getItem(historyKey));
    if (Array.isArray(rows)) return rows.filter(row=>row && typeof row.model==='string' &&
      Number.isInteger(row.passed) && row.passed>=0 && row.passed<=3 && Number.isFinite(row.warmMs) &&
      row.warmMs>=0).slice(0,5);
  } catch { /* The current session still supports comparing models. */ }
  return [];
}

function displayHistory() {
  element('comparison').hidden = manual || history.length<2;
  const rows = history.map(item=>{
    const row = document.createElement('tr');
    const name = textNode('th',item.model);
    name.scope = 'row';
    row.append(name,textNode('td',item.passed+' из 3'),
      textNode('td',(item.warmMs/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})+' с'));
    return row;
  });
  element('history-rows').replaceChildren(...rows);
}

function saveRun(model, results, passed) {
  if (results.length!==3 || !results.slice(1).every(item=>Number.isFinite(item.elapsedMs))) return;
  const warmMs = (results[1].elapsedMs+results[2].elapsedMs)/2;
  history = [{model,passed,warmMs}, ...history.filter(item=>item.model!==model)].slice(0,5);
  try { localStorage.setItem(historyKey,JSON.stringify(history)); } catch { /* In-memory comparison remains available. */ }
  displayHistory();
}

function setIssue(message = '') {
  element('advice-error').textContent = message;
  element('advice-error').hidden = !message;
}

function describe(profile) {
  if (!profile) return 'Характеристики уточняются.';
  const system = systems[profile.platform] || systems.unknown;
  const parts = [system];
  if (profile.cpuName) parts.push(profile.cpuName);
  if (profile.ramGiB) parts.push(profile.ramGiB+' ГБ '+(profile.accelerator==='apple'?'общей памяти':'ОЗУ'));
  if (profile.accelerator==='discrete') {
    parts.push([profile.gpuName || 'Отдельная видеокарта', profile.vramGiB && profile.vramGiB+' ГБ видеопамяти'].filter(Boolean).join(' · '));
  } else if (profile.accelerator==='cpu') {
    parts.push('Расчёт для процессора');
  }
  return parts.join(' · ');
}

function fileLink(raw) {
  try {
    const url = new URL(raw);
    return url.protocol==='https:' && url.hostname==='huggingface.co' ? url : null;
  } catch { return null; }
}

function renderCard(container, model) {
  const name = textNode('h3', model.name);
  const meta = textNode('p', [model.quant, model.fileSize && 'Файл '+model.fileSize].filter(Boolean).join(' · '),'model-meta');
  const actions = document.createElement('div');
  actions.className = 'actions';
  let fileName = '';
  const url = fileLink(model.downloadUrl);
  if (url) {
    fileName = decodeURIComponent(url.pathname.split('/').pop());
    const link = textNode('a','Скачать GGUF ↗','button');
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    actions.append(link);
  }
  const copy = textNode('button','Скопировать для поиска','quiet');
  copy.type = 'button';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(model.search || model.name);
      copy.textContent = 'Название скопировано';
    } catch {
      copy.textContent = 'Для поиска: '+(model.search || model.name);
    }
  };
  actions.append(copy);
  if (model.installedId && !manual && localAdvice?.models?.includes(model.installedId)) {
    const select = textNode('button','Проверить установленную модель','button');
    select.type = 'button';
    select.onclick = () => {
      element('installed-model').value = model.installedId;
      element('installed-model').onchange();
      element('benchmark').focus();
    };
    actions.prepend(select);
    meta.textContent += ' · Есть на этом компьютере';
  }
  container.replaceChildren(name, meta, textNode('p',model.reason,'model-reason'),actions);
  if (fileName) container.append(textNode('code',fileName,'file-name'));
}

function advice(data) {
  element('recommendation').hidden = false;
  if (data.primary) {
    renderCard(element('primary-model'), data.primary);
  } else {
    element('primary-model').replaceChildren(textNode('p', data.needsHardware
      ? 'Для подбора укажи систему, тип графики и доступный объём памяти.'
      : 'Начни с готовых карточек. Их можно использовать без загрузки модели.', 'model-reason'));
  }
  element('alternative').hidden = !data.alternative;
  element('alternative').open = false;
  if (data.alternative) renderCard(element('other-model'), data.alternative);
  element('install').hidden = !data.primary;
  element('install').open = Boolean(data.primary && !manual && data.serverAvailable===false);
  const notes = data.notes || [];
  element('notes').replaceChildren(...notes.map(note=>textNode('li',note)));
  element('notes').hidden = !notes.length;
}

function available(data) {
  const input = element('installed-model');
  const models = data.models || [];
  const choices = [input.value, preference().model, data.installedMatch, data.primary?.installedId, models[0]];
  const selected = choices.find(id=>id && models.includes(id)) || '';
  input.replaceChildren(...models.map(id=>new Option(id,id)));
  input.value=selected;
  element('server-status').textContent = data.serverAvailable
    ? models.length ? 'Локальный сервер подключён. Можно проверить любую текстовую модель из списка.'
      : 'Сервер подключён. Загрузи текстовую модель в Bionic или LM Studio и обнови список.'
    : 'Включи локальный API-сервер в Bionic или LM Studio, затем обнови список. Подбор по характеристикам уже доступен.';
  element('available').hidden = !selected;
  element('cards-only').hidden = Boolean(selected);
  displayHistory();
  updateApply();
}

function updateApply() {
  const chosen = element('installed-model').value;
  const applied = chosen && preference().model===chosen;
  element('apply-model').disabled = !chosen || Boolean(applied);
  element('apply-model').textContent = applied ? 'Выбрана для захвата' : 'Выбрать для захвата';
}

function clearBench() {
  runNumber++;
  element('bench-result').hidden = true;
  element('bench-cases').replaceChildren();
  element('bench-summary').textContent = '';
  element('benchmark').disabled = false;
  element('applied').textContent = '';
}

async function loadLocal() {
  const version = ++viewVersion;
  setIssue();
  element('refresh').disabled = true;
  try {
    const data = await request('/api/advisor');
    if (manual || version!==viewVersion) return;
    localAdvice = data;
    element('hardware').textContent = describe(data.profile);
    advice(data);
    available(data);
  } catch {
    if (manual || version!==viewVersion) return;
    element('hardware').textContent = 'Характеристики недоступны.';
    element('available').hidden = true;
    element('recommendation').hidden = true;
    element('server-status').textContent = 'Не удалось получить данные от QuizLens.';
    setIssue('Проверь, что QuizLens запущен, и нажми «Обновить список».');
  } finally {
    if (!manual && version===viewVersion) element('refresh').disabled = false;
  }
}

function formOptions() {
  const system = element('platform').value;
  const gpu = element('accelerator');
  const appleChoice = gpu.querySelector('option[value="apple"]');
  appleChoice.disabled = system==='windows' || system==='linux';
  if (appleChoice.disabled && gpu.value==='apple') gpu.value = 'unknown';
  element('vram-field').hidden = gpu.value!=='discrete';
  element('vram').disabled = gpu.value!=='discrete';
  element('memory-help').textContent = gpu.value==='apple'
    ? 'У Apple M-серии процессор и графика используют общую память.'
    : gpu.value==='discrete' ? 'Для видеокарты укажи объём выделенной видеопамяти (VRAM).' : '';
}

function selectMode() {
  manual = !manual;
  viewVersion++;
  clearBench();
  setIssue();
  element('device-switch').textContent = manual ? 'Вернуться к этому компьютеру' : 'Указать характеристики';
  element('device-switch').setAttribute('aria-expanded', String(manual));
  element('device-title').textContent = manual ? 'Ручной подбор' : 'Этот компьютер';
  element('profile-form').hidden = !manual;
  element('install').open = false;
  element('local-panel').hidden = manual;
  element('hardware').hidden = manual;
  element('recommendation').hidden = true;
  if (manual) {
    const profile = localAdvice?.profile || {};
    element('platform').value = profile.platform || 'unknown';
    element('accelerator').value = profile.accelerator || 'unknown';
    element('ram').value = profile.ramGiB ?? '';
    element('vram').value = profile.vramGiB ?? '';
    element('calculate').disabled = false;
    formOptions();
    element('platform').focus();
  } else {
    loadLocal();
  }
}

async function calculate(event) {
  event.preventDefault();
  if (!manual || !element('profile-form').reportValidity()) return;
  const profile = {platform:element('platform').value, accelerator:element('accelerator').value,
    ramGiB:Number(element('ram').value) || null,
    vramGiB:element('accelerator').value==='discrete' ? Number(element('vram').value) || null : null};
  const version = ++viewVersion;
  setIssue();
  element('calculate').disabled = true;
  element('recommendation').hidden = true;
  try {
    const result = await request('/api/advisor', profile);
    if (manual && version===viewVersion) advice(result);
  } catch (error) {
    if (manual && version===viewVersion) setIssue(error.message || 'Не удалось выполнить подбор.');
  } finally {
    if (manual && version===viewVersion) element('calculate').disabled = false;
  }
}

function appendCase(result) {
  const elapsed = Number.isFinite(result.elapsedMs)
    ? ' · '+(result.elapsedMs/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})+' с' : '';
  const status = {ok:'верно',late:'верно, больше 15 с',wrong:'ошибка в ответе',unconfirmed:'нет подтверждённого ответа',error:'сбой запроса'};
  const label = status[result.status] || result.detail || 'без результата';
  const title = (result.firstCall?'Первый запрос · ':'')+result.title;
  element('bench-cases').append(textNode('li', title+' — '+label+elapsed));
}

async function benchmark() {
  const model = element('installed-model').value;
  if (!model || manual || element('benchmark').disabled) return;
  const run = ++runNumber;
  const count = localAdvice?.caseCount || 3;
  const results = [];
  element('benchmark').disabled = true;
  element('refresh').disabled = true;
  element('bench-result').hidden = false;
  element('bench-cases').replaceChildren();
  try {
    for (let index=0; index<count; index++) {
      element('bench-summary').textContent = 'Проверяем вопрос '+(index+1)+' из '+count+'…';
      const item = await request('/api/advisor/benchmark', {model, caseIndex:index});
      if (run!==runNumber) return;
      appendCase(item);
      results.push(item);
      if (item.status==='error') {
        element('bench-summary').textContent = 'Проверка прервана. '+(item.detail || 'Проверь соединение с локальным сервером.');
        return;
      }
    }
    const passed = results.filter(item=>item.passed).length;
    saveRun(model, results, passed);
    let next;
    if (results[0].withinLiveTimeout===false && results[0].correct && results.slice(1).every(item=>item.passed && item.withinLiveTimeout)) {
      next = 'Первый запрос был долгим, следующие два успели. Перед квизом задай пробный вопрос и оставь модель загруженной.';
    } else if (passed===count && results.some(item=>item.withinLiveTimeout===false)) {
      next = 'На часть текстовых вопросов ушло больше 10 с. При захвате столько отводится на запрос к модели. Попробуй более лёгкую модель.';
    } else if (passed===count) {
      next = 'Модель успела во всех трёх случаях. Перед занятием проверь полный цикл с распознаванием экрана.';
    } else if (results.some(item=>item.withinBudget===false)) {
      next = 'Часть запросов заняла больше 15 с. Загрузи модель заранее или сравни результат с более лёгкой.';
    } else {
      next = 'Для сравнения выбери другую локальную модель и повтори проверку.';
    }
    element('bench-summary').textContent = 'Верно за 15 с — '+passed+' из '+count+'. '+next;
  } catch (error) {
    if (run===runNumber) element('bench-summary').textContent = 'Проверка прервана. '+(error.message || 'Проверь соединение с QuizLens.');
  } finally {
    if (run===runNumber) {
      element('benchmark').disabled = false;
      element('refresh').disabled = false;
    }
  }
}

element('device-switch').onclick = selectMode;
element('profile-form').onsubmit = calculate;
element('profile-form').oninput = () => {
  if (!manual) return;
  viewVersion++;
  element('recommendation').hidden = true;
  element('calculate').disabled = false;
  setIssue();
};
element('platform').onchange = formOptions;
element('accelerator').onchange = formOptions;
element('refresh').onclick = () => { clearBench(); return loadLocal(); };
element('benchmark').onclick = benchmark;
element('installed-model').onchange = () => { clearBench(); element('refresh').disabled = false; updateApply(); };
element('apply-model').onclick = () => {
  const model = element('installed-model').value;
  if (!model || manual) return;
  try {
    localStorage.setItem(preferenceKey, JSON.stringify({...preference(),model}));
    element('applied').textContent = 'Сохранено. Модель появится в поле «Локальная модель» на странице захвата.';
    updateApply();
  } catch {
    element('applied').textContent = 'Браузер не сохранил настройки. Выбери эту модель в разделе «Захват».';
  }
};
loadLocal();
