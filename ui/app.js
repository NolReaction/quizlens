'use strict';

const el = id => document.getElementById(id);
const preview = el('preview');
const validFields = n => Number.isInteger(n) && n >= 3 && n <= 9;
const manualAreaCount = () => Number(el('areaCount').value) + 1;
const storageKey = 'quizlens.settings.v1';
const answerCache = new Map();
let checkingModels = false;
let modelPreferenceChanged = false;
let modelNames = [];
let modelError = '';
const state = {
  stream:null, regions:[], draftRegions:[], draftDrag:null, lectures:[], selected:new Set(),
  layoutMode:'auto', autoArmed:false, autoFocus:null, focusAspect:0, snapshotAspect:0,
  resumeCalibration:false, shareAttempt:0, tabReading:false, tabFrame:null,
  previousReading:null, warningKey:null, readingReview:null,
  running:false, session:0, signature:null, dirtyFrame:false, lastRead:-Infinity,
  ocrJob:null, solveJob:null, currentKey:null, candidateKey:null, pendingSince:null, displayedKey:null,
  captureTrace:[], candidateSeenAt:null, firstSeenAt:null, confirmedTexts:null, captureIssue:null,
  retryOcrAt:0, retrySolveAt:0, response:null, pip:null, timer:null
};

function settings() {
  try { return JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch { return {}; }
}
function remember(key, value) {
  try { localStorage.setItem(storageKey, JSON.stringify({...settings(), [key]:value})); } catch { /* Disk settings are optional. */ }
}
function notify(message='') {
  el('notice').textContent = message;
  el('notice').hidden = !message;
}
async function api(path, options={}) {
  const response = await fetch(path, {cache:'no-store', ...options});
  let data;
  try { data = await response.json(); } catch { throw new Error('Локальный сервер вернул нечитаемый ответ.'); }
  if (!response.ok) throw new Error(data.error || data.detail || 'Ошибка локального сервера ('+response.status+').');
  return data;
}
function postJson(path, payload, signal) {
  return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), signal});
}
function status(text, kind='') {
  const badge = el('sessionStatus');
  badge.className = 'session-status '+kind;
  badge.querySelector('span').textContent = text;
}
function addText(parent, cssClass, text) {
  if (!text) return;
  const item = parent.ownerDocument.createElement('div');
  item.className = cssClass;
  item.textContent = text;
  parent.append(item);
}
function drawWarningImage(figure, warning) {
  if (!warning.image) return;
  const image = figure.ownerDocument.createElement('img');
  image.src = warning.image;
  if (warning.fullRow) image.className = 'reading-row';
  image.alt = warning.field ? 'Фрагмент варианта ' + warning.field : 'Фрагмент вопроса';
  figure.append(image);
}
function drawAnswer(container, info) {
  container.replaceChildren();
  container.className = 'answer-body'+(info.unclear ? ' unclear':'');
  addText(container, 'answer-tag', info.tag);
  addText(container, 'answer-title', info.title);
  addText(container, 'answer-text', info.subtitle);
  addText(container, info.tentative ? 'answer-text problem-text' : 'answer-text', info.note);
  for (const warning of info.readingWarnings || []) {
    const figure = container.ownerDocument.createElement('figure');
    figure.className = 'reading-warning';
    figure.setAttribute('data-reading-warning', String(warning.field));
    const caption = container.ownerDocument.createElement('figcaption');
    caption.textContent = (warning.field ? 'Вариант ' + warning.field : 'Вопрос') +
      ' · «' + warning.readings[0] + '» или «' + warning.readings[1] + '»';
    figure.append(caption);
    drawWarningImage(figure, warning);
    container.append(figure);
  }
  if (info.reviewEditor) window.addQuizReadingEditor(container, info.reviewEditor.texts, info.readingWarnings, info.reviewEditor.confirm);
  if (info.selections) {
    for (const item of info.selections) addText(container, 'answer-text', item.choice + '. ' + item.text);
    const details = container.ownerDocument.createElement('details');
    const summary = container.ownerDocument.createElement('summary');
    details.open = true;
    summary.textContent = 'Цитаты из документов';
    details.append(summary);
    for (const item of info.selections) {
      addText(details, 'answer-source', 'Вариант ' + item.choice + ' · ' + item.source);
      addText(details, 'answer-excerpt', item.excerpt);
    }
    container.append(details);
  }
  addText(container, 'answer-source', info.source);
  addText(container, 'answer-excerpt', info.excerpt);
  if (info.journalId) {
    const link = container.ownerDocument.createElement('a');
    link.href = '/journal#' + encodeURIComponent(info.journalId);
    link.target = '_blank';
    link.rel = 'noopener';
    link.className = 'review-link';
    link.textContent = 'Разобрать ответ ↗';
    container.append(link);
  }
  if (info.tokenUsage) addText(container, 'hint', formatQuizTokenUsage(info.tokenUsage));
  addText(container, 'hint', info.journalWarning);
}
function answer(info) {
  state.response = info;
  drawAnswer(el('answer'), info);
  if (state.pip && !state.pip.closed) {
    const outlet = state.pip.document.getElementById('pipAnswer');
    if (outlet) drawAnswer(outlet, info);
  }
}
function showTexts(texts) {
  const box = el('readText');
  box.replaceChildren();
  const hasText = texts?.some(Boolean);
  el('readingTranscript').hidden = !hasText;
  if (!hasText) return;
  addText(box, '', texts[0] || 'Вопрос пока не прочитан.');
  const list = document.createElement('ol');
  texts.slice(1).forEach(text => {
    const item = document.createElement('li');
    item.textContent = text || '…';
    list.append(item);
  });
  box.append(list);
}
function idleAnswer() { answer({title:'Жду вопрос', subtitle:'Здесь появятся ответ и цитата из отмеченного документа.'}); }
function controls() {
  const sharing = Boolean(state.stream);
  const auto = state.layoutMode === 'auto';
  el('stage').hidden = state.tabReading;
  const ready = sharing && (auto || validFields(state.regions.length)) && state.selected.size > 0;
  el('modelAdvisor').target = (sharing || state.tabReading) ? '_blank' : '_self';
  el('sessionView').classList.toggle('sharing', sharing);
  el('run').hidden = !sharing;
  el('runHint').hidden = !sharing;
  el('captureHint').hidden = !sharing;
  el('share').textContent = sharing ? 'Сменить окно' : 'Выбрать окно';
  el('share').classList.toggle('primary', !sharing);
  el('sessionView').classList.toggle('reading', state.running);
  el('materialSummary').textContent = 'Отмечено '+state.selected.size+' из '+state.lectures.length;
  el('release').hidden = !sharing;
  el('autoLayout').hidden = !sharing || (auto && !state.autoFocus);
  el('autoLayout').textContent = auto ? 'Искать во всём окне' : 'Вернуть авторазметку';
  el('calibrate').hidden = !sharing;
  el('calibrate').disabled = !sharing;
  const active = state.running || state.autoArmed;
  el('run').disabled = !ready && !active;
  el('run').textContent = active ? 'Остановить' : 'Начать чтение';
  el('run').classList.toggle('stop', active);
  const saved = settings().regions;
  const aspect = preview.videoWidth / preview.videoHeight;
  el('reuse').hidden = !sharing || !auto || !saved || !Array.isArray(saved.areas)
    || !validFields(saved.areas.length) || !Number.isFinite(aspect) || Math.abs(aspect/saved.aspect-1) > .04;
  el('runHint').textContent = !sharing ? 'Окно не выбрано'
    : !state.selected.size ? 'Отметь документ'
    : !active ? 'Чтение на паузе'
    : auto ? (validFields(state.regions.length) ? 'Авторазметка · вопрос найден' : 'Авторазметка · ожидаю вопрос')
    : 'Чтение выделенных областей';
}

el('record').checked = settings().record !== false;
el('record').addEventListener('change', () => {
  remember('record', el('record').checked);
  answerCache.clear();
  if (state.running) start();
});

el('visual').addEventListener('click', event => {
  const fields = state.candidateKey === null && state.confirmedTexts ? state.confirmedTexts.slice() : [];
  const live = Boolean(state.stream && preview.readyState >= 2);
  if (!live && !(state.tabReading && fields.length)) return;
  event.preventDefault();
  // A quiz can advance while the new tab is opening. Bind the fields only to
  // their actual OCR frame, or carry text alone when reading a browser tab.
  const saved = live && window.QuizChart?.imageFor(fields, state.session);
  const carry = saved || !live ? fields : [];
  const frame = {image:saved || (live ? wholeWindowImage(true) : null),
    question:carry[0] || '', options:carry.slice(1), selectionMode:window.QuizChart?.selectionMode || el('selectionMode').value};
  if (!openQuizVisual(() => frame)) {
    notify('Разреши открыть вкладку, чтобы передать задание для разбора.');
  }
});

function applySettingsChange() {
  if (state.tabReading || (state.autoArmed && state.stream)) {
    start();
  } else if (state.running) {
    stop('Настройки изменены. Запусти чтение снова.');
  }
  controls();
}

let lectureListVersion = 0;

function renderLectures() {
  window.QuizSources.render(el('lectureList'), {
    lectures: state.lectures,
    selected: state.selected,
    onToggle(id, checked) {
      if (checked) state.selected.add(id); else state.selected.delete(id);
      remember('selected', [...state.selected]);
      applySettingsChange();
    },
    onDelete(id) {
      ++lectureListVersion;
      const wasSelected = state.selected.delete(id);
      state.lectures = state.lectures.filter(item => item.id !== id);
      remember('selected', [...state.selected]);
      answerCache.clear();
      renderLectures();
      if (wasSelected) applySettingsChange();
      el('fileInfo').textContent = 'Документ удалён из QuizLens.';
    }
  });
  controls();
}
async function loadLectures() {
  const version = ++lectureListVersion;
  const data = await api('/api/lectures');
  if (version !== lectureListVersion) return;
  const previous = state.selected;
  state.lectures = data.lectures;
  const initial = settings().selected;
  state.selected = new Set(state.lectures.map(l=>l.id).filter(id=>!Array.isArray(initial)||initial.includes(id)));
  renderLectures();
  if (previous.size !== state.selected.size || [...previous].some(id => !state.selected.has(id))) {
    answerCache.clear();
    applySettingsChange();
  }
}
el('addLecture').addEventListener('click', () => el('files').click());
el('files').addEventListener('change', async event => {
  const files = [...event.target.files];
  if (!files.length) return;
  const fileErrors=[];
  let complete=0;
  el('addLecture').disabled = true;
  if (state.running && !state.autoArmed && !state.tabReading) stop();
  try {
    for(const file of files) {
      el('fileInfo').textContent = 'Добавляю '+file.name+'…';
      try {
        if (file.size > 32 * 1024 * 1024) throw new Error('Максимальный размер файла — 32 МиБ.');
        const data=await api('/api/import?name='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
        ++lectureListVersion;
        state.lectures=state.lectures.filter(lecture=>lecture.id!==data.lecture.id);
        state.lectures.push(data.lecture);
        state.selected.add(data.lecture.id);
        complete++;
        el('sourceManager').open = true;
        remember('selected',[...state.selected]); renderLectures();
        if(state.autoArmed || state.tabReading) applySettingsChange();
      } catch(error) { fileErrors.push(file.name+' — '+error.message); }
    }
    if (complete) window.QuizSources.changed();
    el('fileInfo').textContent = 'Добавлено файлов — '+complete+'. '+fileErrors.join(' ');
  } finally { event.target.value=''; el('addLecture').disabled=false; }
});
function showModelStatus() {
  const hint = el('modelInfo');
  const current = el('model').value;
  if (modelError) {
    hint.textContent = modelError;
  } else if (!modelNames.length) {
    hint.textContent = 'Сервер подключён. Добавь текстовую модель в LM Studio или используй карточки.';
  } else if (current && modelNames.includes(current)) {
    hint.textContent = 'Сервер подключён. Выбранная модель доступна для ответов.';
  } else if (settings().model) {
    hint.textContent = 'Выбранной модели сейчас нет в списке сервера. Выбери доступную модель или карточки.';
  } else {
    hint.textContent = 'Включены карточки и поиск. Для ответов модели выбери её в списке.';
  }
}

async function loadModels() {
  if (checkingModels) return;
  checkingModels = true;
  el('refresh').disabled = true;
  try {
    const {models} = await api('/api/models');
    const input = el('model');
    const previous = input.value;
    const wasReading = state.running || state.tabReading;
    const desired = settings().model ?? previous;
    let selection = models.includes(desired) ? desired : '';
    if (wasReading && !modelPreferenceChanged) selection = previous;
    const options = ['', ...models];
    if (selection && !options.includes(selection)) options.push(selection);
    if (!input.options || input.options.length !== options.length ||
        options.some((name, i) => input.options[i].value !== name)) {
      input.replaceChildren(...options.map(name => new Option(name || 'Карточки и поиск (без модели)', name)));
    }
    input.value = selection;
    modelNames = models;
    modelError = '';
    if (previous !== selection) applySettingsChange();
  } catch (error) {
    modelError = error instanceof TypeError ? 'Нет связи с QuizLens. Проверь окно запуска приложения.'
      : error.message.startsWith('Нет связи с локальным сервером')
        ? 'Сервер модели недоступен. Запусти его в LM Studio или Bionic. Карточками можно пользоваться сразу.'
        : error.message;
  } finally {
    modelPreferenceChanged = false;
    checkingModels = false;
    el('refresh').disabled = false;
    showModelStatus();
  }
}

let lastReadinessCheck = -Infinity;
function refreshWhenIdle() {
  if (document.visibilityState !== 'visible' || state.stream || state.running || state.tabReading ||
      window.quizLensUpdateActivity?.busy() || performance.now() - lastReadinessCheck < 1000) return;
  lastReadinessCheck = performance.now();
  loadModels();
  window.dispatchEvent(new Event('quizlens-check-ocr'));
}
window.addEventListener('focus', refreshWhenIdle);
window.addEventListener('visibilitychange', refreshWhenIdle);
window.addEventListener('pageshow', event => { if (event.persisted) refreshWhenIdle(); });
el('refresh').addEventListener('click', loadModels);
el('selectionMode').addEventListener('change', () => {
  if (state.running) start();
});
el('model').addEventListener('change', () => {
  remember('model',el('model').value);
  showModelStatus();
  applySettingsChange();
});

el('compact').addEventListener('click', async () => {
  if(state.pip && !state.pip.closed) { state.pip.focus(); return; }
  if(!window.documentPictureInPicture) {
    notify('Для режима «Поверх окон» открой приложение в Chrome или Edge на компьютере. Также можно расположить окно помощника рядом с квизом.');
    return;
  }
  try {
    state.pip = await window.documentPictureInPicture.requestWindow({width:390,height:255});
    const doc = state.pip.document;
    doc.title='QuizLens · Ответ';
    doc.documentElement.lang='ru';
    const link=doc.createElement('link');
    link.rel='stylesheet'; link.href=new URL('/style.css',location.href).href;
    const captureStyle = doc.createElement('link');
    captureStyle.rel='stylesheet'; captureStyle.href=new URL('/capture.css',location.href).href;
    doc.head.append(link, captureStyle); doc.body.className='pip';
    const area=doc.createElement('div'); area.id='pipAnswer'; doc.body.append(area);
    if(state.response) drawAnswer(area,state.response);
    state.pip.addEventListener('pagehide',()=>{state.pip=null;},{once:true});
    notify();
  } catch(error) { notify('Окно с ответом не открылось. '+error.message); }
});

function fitVideo() {
  if(!preview.videoWidth) return;
  const stage=el('stage');
  const scale=Math.min(stage.clientWidth/preview.videoWidth,stage.clientHeight/preview.videoHeight);
  el('videoBox').style.width=(preview.videoWidth*scale)+'px';
  el('videoBox').style.height=(preview.videoHeight*scale)+'px';
}
new ResizeObserver(fitVideo).observe(el('stage'));
function paintRegions(overlay, regions, draft=null, draftLabel='') {
  overlay.replaceChildren();
  const areas=draft ? [...regions,draft] : regions;
  areas.forEach((region,i)=>{
    const rect=document.createElement('div');
    rect.className='area'+(i===0?' question':'')+(draft && i===regions.length?' draft':'');
    Object.assign(rect.style,{left:region.x*100+'%',top:region.y*100+'%',width:region.w*100+'%',height:region.h*100+'%'});
    const label=document.createElement('b');
    label.textContent=draftLabel && i===regions.length ? draftLabel : i===0?'В':String(i);
    rect.append(label);overlay.append(rect);
  });
}
function paintLiveAreas() {
  const overlay=el('liveAreas');
  if(state.autoFocus && !state.regions.length) {
    paintRegions(overlay,[],state.autoFocus,'Поиск');
  } else {
    paintRegions(overlay,state.regions);
  }
}
function release() {
  state.shareAttempt++;
  state.resumeCalibration=false;
  stop();
  const stream=state.stream;
  state.stream=null;
  if (stream) el('selectionMode').value='auto';
  if(stream) stream.getTracks().forEach(track=>track.stop());
  preview.srcObject=null;
  state.regions=[];
  state.layoutMode='auto';
  state.autoFocus=null;state.focusAspect=0;
  if(el('calibration').open) el('calibration').close();
  el('videoBox').hidden=true; el('emptyStage').hidden=false;
  el('captureHint').textContent='Для авторазметки приложение читает кадр выбранного окна на этом устройстве.';
  paintRegions(el('liveAreas'),[]);
  controls(); status('Ожидание');
}
el('release').addEventListener('click',release);
el('share').addEventListener('click',async () => {
  if(!navigator.mediaDevices?.getDisplayMedia) {
    notify('Браузер не поддерживает захват окон. Открой локальный адрес в Chrome или Edge.'+
      (el('nativeLink').hidden ? '' : ' Также доступен захват окна средствами macOS.'));
    return;
  }
  let stream=null;
  const attempt=++state.shareAttempt;
  el('share').disabled=true;
  try {
    stream=await navigator.mediaDevices.getDisplayMedia({
      video:{frameRate:{ideal:6,max:12},cursor:'never'},audio:false,selfBrowserSurface:'exclude',surfaceSwitching:'exclude',monitorTypeSurfaces:'exclude'
    });
    if(attempt!==state.shareAttempt) {stream.getTracks().forEach(track=>track.stop());return;}
    if (state.tabReading) {
      window.quizLensTabCapture.stop();
      window.dispatchEvent(new Event('quizlens-tab-disconnect'));
    }
    release();
    const track=stream.getVideoTracks()[0];
    state.stream=stream;
    track.addEventListener('ended',()=>{if(state.stream===stream){release(); notify('Захват завершён. При необходимости выбери окно заново.');}},{once:true});
    preview.srcObject=stream;
    await preview.play();
    if(state.stream!==stream) return;
    el('emptyStage').hidden=true;el('videoBox').hidden=false;
    fitVideo();
    start();
  } catch(error) {
    if(!stream && attempt!==state.shareAttempt) return;
    if(stream && state.stream!==stream) return;
    if(stream && state.stream===stream) release();
    notify(error.name==='NotAllowedError'
      ? 'Окно не подключено. Выбери окно в системном диалоге. На Mac разрешение браузеру находится в Настройки системы → Конфиденциальность и безопасность → Запись экрана.'
      : 'Не удалось начать захват. '+error.message);
  } finally { el('share').disabled=false; }
});

function takeSnapshot() {
  if(!preview.videoWidth || preview.readyState<2) return false;
  const canvas=el('snapshot');
  const scale=Math.min(1,1600/preview.videoWidth,1100/preview.videoHeight);
  canvas.width=Math.max(1,Math.round(preview.videoWidth*scale));canvas.height=Math.max(1,Math.round(preview.videoHeight*scale));
  canvas.getContext('2d').drawImage(preview,0,0,canvas.width,canvas.height);
  state.snapshotAspect=preview.videoWidth/preview.videoHeight;
  return true;
}
function calibrationStep() {
  const n=state.draftRegions.length;
  const target=manualAreaCount();
  el('calStep').textContent=n===0 ? 'Обведи весь квиз для автопоиска или только вопрос для ручной разметки.'
    : n<target ? (n+1)+' из '+target+' · Обведи текст варианта '+n+'.' : 'Проверь номера вариантов и сохрани разметку.';
  el('undo').disabled=n===0;
  el('apply').disabled=n!==target;
  el('focusSearch').disabled=n!==1;
  paintRegions(el('calAreas'),state.draftRegions);
}
el('calibrate').addEventListener('click',()=> {
  if(!takeSnapshot()) {notify('Кадр ещё загружается. Попробуй снова через секунду.');return;}
  state.resumeCalibration=state.running || state.autoArmed;
  if (validFields(state.regions.length)) el('areaCount').value=String(state.regions.length-1);
  stop();
  state.draftRegions=[];state.draftDrag=null;calibrationStep();
  el('calibration').showModal(); notify();
});
el('areaCount').addEventListener('change',() => {
  state.draftRegions=state.draftRegions.slice(0,manualAreaCount());
  state.draftDrag=null;
  calibrationStep();
});
el('newFrame').addEventListener('click',()=>{state.draftDrag=null;takeSnapshot();calibrationStep();});
el('undo').addEventListener('click',()=>{state.draftRegions.pop();calibrationStep();});
el('closeDialog').addEventListener('click',()=>el('calibration').close());
el('calibration').addEventListener('close',()=>{
  if(state.resumeCalibration && state.stream) start();
  state.resumeCalibration=false;
});
function pointerPoint(event) {
  const bounds=el('calImage').getBoundingClientRect();
  const clamp=n=>Math.max(0,Math.min(1,n));
  return {x:clamp((event.clientX-bounds.left)/bounds.width),y:clamp((event.clientY-bounds.top)/bounds.height)};
}
function fromPoints(a,b) {
  return {x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)};
}
el('calImage').addEventListener('pointerdown',event=>{
  if(event.button!==0 || state.draftRegions.length>=manualAreaCount()) return;
  state.draftDrag={pointer:event.pointerId,from:pointerPoint(event)};
  event.currentTarget.setPointerCapture(event.pointerId);
});
el('calImage').addEventListener('pointermove',event=>{
  const drag=state.draftDrag;
  if(!drag || drag.pointer!==event.pointerId) return;
  paintRegions(el('calAreas'),state.draftRegions,fromPoints(drag.from,pointerPoint(event)));
});
el('calImage').addEventListener('pointerup',event=>{
  const drag=state.draftDrag;
  if(!drag || drag.pointer!==event.pointerId) return;
  const region=fromPoints(drag.from,pointerPoint(event));
  state.draftDrag=null;
  if(region.w>=.015 && region.h>=.01) state.draftRegions.push(region);
  calibrationStep();
});
el('calImage').addEventListener('pointercancel',()=>{state.draftDrag=null; calibrationStep();});
function useAreas(areas) {
  if(state.running || state.autoArmed) stop();
  state.layoutMode='manual';
  state.autoFocus=null;state.focusAspect=0;
  state.regions=areas.map(r=>({...r}));
  paintRegions(el('liveAreas'),state.regions);
  el('captureHint').textContent='В — вопрос. Цифрами обозначены варианты в выбранном порядке.';
  controls();status('Готов к чтению');notify();
}
el('apply').addEventListener('click',()=>{
  if(state.draftRegions.length!==manualAreaCount()) return;
  useAreas(state.draftRegions);
  remember('regions',{aspect:preview.videoWidth/preview.videoHeight,areas:state.regions});
  el('calibration').close();
});
el('reuse').addEventListener('click',()=> {
  const previous=settings().regions?.areas;
  if(previous && validFields(previous.length)) {
    const resume=state.running || state.autoArmed;
    useAreas(previous);
    if(resume) start();
  }
});
el('autoLayout').addEventListener('click',()=>{
  if(!state.stream) return;
  state.autoFocus=null;state.focusAspect=0;
  state.layoutMode='auto';
  start();
});
el('focusSearch').addEventListener('click',()=>{
  if(!state.stream || state.draftRegions.length!==1) return;
  const aspect=preview.videoWidth/preview.videoHeight;
  if(!Number.isFinite(aspect) || Math.abs(aspect/state.snapshotAspect-1)>.04) {
    state.draftRegions=[];takeSnapshot();calibrationStep();
    el('calStep').textContent='Размер кадра изменился. Обведи квиз на обновлённом кадре.';
    return;
  }
  state.autoFocus={...state.draftRegions[0]};
  state.focusAspect=aspect;state.layoutMode='auto';state.resumeCalibration=false;
  el('calibration').close();
  start();
});

const sampleCanvas=document.createElement('canvas');
const sampleWidth=352,sampleHeight=48;
sampleCanvas.width=sampleWidth;sampleCanvas.height=sampleHeight*5;
const sampleContext=sampleCanvas.getContext('2d',{willReadFrequently:true});
const ocrCanvas=document.createElement('canvas');
function pixels(rect) {
  const x=Math.floor(rect.x*preview.videoWidth),y=Math.floor(rect.y*preview.videoHeight);
  const w=Math.max(1,Math.min(preview.videoWidth-x,Math.ceil(rect.w*preview.videoWidth)));
  const h=Math.max(1,Math.min(preview.videoHeight-y,Math.ceil(rect.h*preview.videoHeight)));
  return [x,y,w,h];
}
function fingerprint() {
  const rows=state.layoutMode==='auto' ? 5 : state.regions.length;
  if(sampleCanvas.height!==sampleHeight*rows) sampleCanvas.height=sampleHeight*rows;
  if(state.layoutMode==='auto') {
    sampleContext.drawImage(preview,...searchPixels(),0,0,sampleCanvas.width,sampleCanvas.height);
  } else {
    state.regions.forEach((region,index)=>{
      sampleContext.drawImage(preview,...pixels(region),0,index*sampleHeight,sampleWidth,sampleHeight);
    });
  }
  return sampleContext.getImageData(0,0,sampleCanvas.width,sampleCanvas.height).data;
}
function differs(current,previous) {
  if(!previous || current.length!==previous.length) return true;
  // An animated horizontal rule should not accelerate polling. Small glyph changes
  // that occupy fewer rows are still picked up by the periodic OCR read.
  const stride=sampleWidth*sampleHeight*4;
  for(let start=0;start<current.length;start+=stride) {
    let count=0, rows=0;
    for(let row=0;row<sampleHeight;row++) {
      const from=start+row*sampleWidth*4;
      const before=count;
      for(let p=from;p<from+sampleWidth*4;p+=4) {
        const delta=Math.abs(current[p]-previous[p])+Math.abs(current[p+1]-previous[p+1])+Math.abs(current[p+2]-previous[p+2]);
        if(delta>78) count++;
      }
      if(count>before) rows++;
      if(rows>=5 && count>=12) return true;
    }
  }
  return false;
}
function ocrImage() {
  // Send only manually selected areas. The local OCR backend receives their new positions in the strip.
  const source=state.regions.map(pixels);
  const scale=Math.min(1,1600/Math.max(...source.map(r=>r[2])),1900/source.reduce((sum,r)=>sum+r[3],0));
  const sizes=source.map(r=>({w:Math.max(1,Math.round(r[2]*scale)),h:Math.max(1,Math.round(r[3]*scale))}));
  // A clear gap keeps adjacent choices separate when Vision reads the strip in one pass.
  const gap=20;
  ocrCanvas.width=Math.max(...sizes.map(r=>r.w));
  ocrCanvas.height=sizes.reduce((sum,r)=>sum+r.h,0)+gap*(sizes.length-1);
  const ctx=ocrCanvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,ocrCanvas.width,ocrCanvas.height);
  let top=0;
  const regions=source.map((r,i)=>{
    const {w,h}=sizes[i];
    ctx.drawImage(preview,...r,0,top,w,h);
    const area={x:0,y:top/ocrCanvas.height,w:w/ocrCanvas.width,h:h/ocrCanvas.height};
    top+=h+gap;return area;
  });
  // Lossless text keeps small glyphs intact, e.g. 75 % versus 715 %.
  return {image:encodeCanvas(ocrCanvas),regions};
}
function encodeCanvas(canvas) {
  const png=canvas.toDataURL('image/png');
  return png.length<=10_000_000 ? png : canvas.toDataURL('image/jpeg',.94);
}
function searchPixels() {
  return state.autoFocus ? pixels(state.autoFocus) : [0,0,preview.videoWidth,preview.videoHeight];
}
function wholeWindowImage(fullWindow = false) {
  const source=fullWindow ? [0, 0, preview.videoWidth, preview.videoHeight] : searchPixels();
  const scale=Math.min(1,1920/source[2],1280/source[3]);
  ocrCanvas.width=Math.max(1,Math.round(source[2]*scale));
  ocrCanvas.height=Math.max(1,Math.round(source[3]*scale));
  ocrCanvas.getContext('2d').drawImage(preview,...source,0,0,ocrCanvas.width,ocrCanvas.height);
  return encodeCanvas(ocrCanvas);
}
function clearWorkflow() {
  state.session++;
  state.previousReading=null;state.warningKey=null;state.readingReview=null;
  window.QuizChart?.show(null);
  window.clearInterval(state.timer);state.timer=null;
  state.ocrJob?.controller.abort();state.ocrJob=null;
  state.solveJob?.controller.abort();state.solveJob=null;
  state.currentKey=null;state.candidateKey=null;state.pendingSince=null;state.displayedKey=null;
  state.captureTrace=[];state.candidateSeenAt=null;state.firstSeenAt=null;state.confirmedTexts=null;state.captureIssue=null;
  state.retryOcrAt=0;state.retrySolveAt=0;state.signature=null;state.dirtyFrame=false;state.lastRead=-Infinity;
}
function stop(reason='') {
  state.running=false;state.autoArmed=false;clearWorkflow();controls();status(state.stream?'Пауза':'Ожидание');
  idleAnswer();showTexts(null);
  if(reason) notify(reason);
}
function start() {
  if (state.tabReading) {
    clearWorkflow();
    state.running=state.selected.size>0;
    controls();showTexts(null);
    answer({title:state.running ? 'Жду вопрос во вкладке' : 'Отметь документ'});
    status(state.running ? 'Читаю вкладку' : 'Отметь документ');
    if (state.running && state.tabFrame) window.quizLensTabCapture.read(state.tabFrame.texts, state.tabFrame.hint);
    return;
  }
  const auto=state.layoutMode==='auto';
  if(!state.stream || (!auto && (!validFields(state.regions.length) || !state.selected.size))) return;
  clearWorkflow();
  state.autoArmed=auto;
  state.running=Boolean(state.selected.size);
  if(auto) {
    state.regions=[];paintLiveAreas();
    el('captureHint').textContent=state.autoFocus ? 'Автопоиск внутри выбранной рамки.'
      : 'Рамки появятся автоматически. Ручная разметка доступна в параметрах чтения.';
  }
  if(!state.running) {
    controls();status('Отметь документ');showTexts(null);
    answer({title:'Отметь документ',subtitle:'Открой «Управлять источниками» и отметь файл. Чтение начнётся автоматически.'});
    return;
  }
  controls(); status('Читаю окно','active'); notify();
  showTexts(null);
  answer({title:auto?'Ожидаю вопрос и варианты':'Читаю вопрос…'});
  state.timer=window.setInterval(tick,260);tick();
}
// The extension calls this adapter with the visible question and displayed controls.
window.quizLensTabCapture = {
  start() {
    state.tabReading=false;state.tabFrame=null;
    release();
    state.tabReading=true;
    start();
  },
  read(texts, hint={}) {
    if (!state.tabReading || !Array.isArray(texts) || (texts.length && !validFields(texts.length))) return;
    if (!texts.every((line,i) => typeof line==='string' && line.length<=(i===0?6000:3000))) return;
    const mode={selectionMode:hint?.selectionMode, expectedCount:hint?.expectedCount};
    state.tabFrame={texts:texts.slice(),hint:mode};
    if (state.running) acceptTexts(texts,state.session,null,mode);
  },
  stop() {
    if (!state.tabReading) return;
    state.tabReading=false;state.tabFrame=null;
    stop();
  }
};
el('run').addEventListener('click',()=>{if(state.running || state.autoArmed) stop();else start();});
function selectionForFrame(hint,optionCount) {
  const override=el('selectionMode').value;
  if (override==='single') return {selectionMode:'single',expectedCount:1};
  const count=hint.selectionMode==='multiple' ? hint.expectedCount ?? null : null;
  if (override==='multiple' || hint.selectionMode==='multiple') {
    if (count===null || (Number.isInteger(count) && count>=1 && count<=optionCount)) {
      return {selectionMode:'multiple',expectedCount:count};
    }
    if (override==='multiple') return {selectionMode:'multiple',expectedCount:null};
    return {selectionMode:'unknown',expectedCount:null};
  }
  if (hint.selectionMode==='unknown') return {selectionMode:'unknown',expectedCount:null};
  return {selectionMode:'single',expectedCount:1};
}
function answerForResult(result, options, selection) {
  const journal = {journalId:result.journalId,journalWarning:result.journalWarning,tokenUsage:result.tokenUsage};
  if (selection.selectionMode==='multiple') {
    const choices=result.choices;
    if (Array.isArray(choices) && choices.length && Array.isArray(result.evidence) && new Set(choices).size===choices.length &&
        (!selection.expectedCount || choices.length===selection.expectedCount) &&
        choices.every(index=>Number.isInteger(index) && index>=1 && index<=options.length)) {
      const selections=[...choices].sort((a,b)=>a-b).map(index=>{
        const evidence=result.evidence.find(item=>item?.choice===index);
        return {choice:index,text:options[index-1],source:evidence?.source,excerpt:evidence?.excerpt};
      });
      if (selections.every(item=>item.source && item.excerpt)) {
        const numbers=new Intl.ListFormat('ru',{type:'conjunction'}).format(selections.map(item=>String(item.choice)));
        return {...journal,...answerProof(result,options),title:'Отметь '+numbers,selections};
      }
    }
  } else if (Number.isInteger(result.choice) && result.choice>=1 && result.choice<=options.length) {
    return {...journal,...answerProof(result,options),tag:'Вариант '+result.choice,title:options[result.choice-1],source:result.source,excerpt:result.excerpt};
  }
  return {...journal,title:result.kind==='error'?'Ответ недоступен':'Нет подтверждения',subtitle:result.detail || 'Сверь ответ с найденным фрагментом документа.',source:result.source,excerpt:result.excerpt,unclear:true};
}
function displayResult(view) {
  state.displayedKey=state.currentKey;
  answer(view);
  if(view.unclear) status('Проверь источник','problem');
  else if(view.tentative) status('Сверь расчёт','problem');
  else status('Ответ готов','active');
}
function cacheResult(key, result) {
  answerCache.set(key,result);
  if(answerCache.size>64) answerCache.delete(answerCache.keys().next().value);
}
async function solve(texts,key,session,selection) {
  if(state.displayedKey===key) return;
  const options=texts.slice(1);
  const cached=answerCache.get(key);
  if(cached) {
    displayResult(answerForResult({...cached, tokenUsage:{source:'cache'}},options,selection));
    return;
  }
  if(state.solveJob?.key===key || performance.now()<state.retrySolveAt) return;
  state.solveJob?.controller.abort();
  const job={key,controller:new AbortController()};state.solveJob=job;
  answer({title:'Ищу в документах…'});status('Ищу ответ','active');
  try {
    const params={question:texts[0],options,...selection,model:el('model').value,lectureIds:[...state.selected],
      record:el('record').checked,channel:'browser'};
    const requestAt=performance.now();
    if (state.firstSeenAt!==null) params.captureMs=Math.round(Math.max(0,requestAt-state.firstSeenAt));
    if (params.record && !state.tabReading) params.captureTrace=traceForRequest(requestAt);
    const result=await postJson('/api/solve',params,job.controller.signal);
    if(state.session!==session || job.controller.signal.aborted) return;
    const view=answerForResult(result,options,selection);
    if(!view.unclear && !view.tentative) cacheResult(key,result);
    if(state.currentKey===key && state.candidateKey===null) displayResult(view);
  } catch(error) {
    if(state.session!==session || job.controller.signal.aborted) return;
    if(state.currentKey===key && state.candidateKey===null) {
      answer({title:'Ответ недоступен',subtitle:error.message,unclear:true});status('Ошибка поиска','problem');
      state.retrySolveAt=performance.now()+3000;
    }
  } finally { if(state.solveJob===job) state.solveJob=null; }
}
function noteCapture(texts,status,timestamp) {
  if (state.tabReading || !el('record').checked) return;
  const question=(texts[0] || '').slice(0,400);
  const options=texts.slice(1,9).map(text=>text.slice(0,220));
  const key=JSON.stringify([status,question,options]);
  state.captureTrace=state.captureTrace.filter(row=>timestamp-row.timestamp<=4000);
  if (state.captureTrace.at(-1)?.key===key) return;
  state.captureTrace=state.captureTrace.filter(row=>row.key!==key);
  state.captureTrace.push({key,timestamp,question,options,status});
  if (state.captureTrace.length>4) state.captureTrace.shift();
}

function traceForRequest(timestamp) {
  return state.captureTrace.filter(row=>timestamp-row.timestamp<=4000).map(row=>({
    ageMs:Math.max(0,Math.round(timestamp-row.timestamp)),status:row.status,question:row.question,options:row.options
  }));
}

function warningsForFrame(raw, fieldCount) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0,fieldCount).flatMap(warning => {
    if (!Number.isInteger(warning?.field) || warning.field<0 || warning.field>=fieldCount ||
        !Array.isArray(warning.readings) || warning.readings.length!==2 ||
        !warning.readings.every(text => typeof text==='string' && text.trim())) return [];
    const readings=warning.readings.map(text=>text.trim().slice(0,160));
    if (readings[0]===readings[1]) return [];
    const image = typeof warning.image==='string' && warning.image.length<=400_000 &&
      warning.image.startsWith('data:image/png;base64,') ? warning.image : null;
    return [{field:warning.field, readings, image}];
  });
}

// A new number or a lost negation can change the key without changing the rest
// of the question. Invalidate the old answer as soon as such a change is seen.
function criticalDifference(previous, next) {
  if (!previous || previous.length!==next.length) return null;
  let field=-1;
  for (let i=0;i<next.length;i++) {
    if (previous[i]===next[i]) continue;
    if (field!==-1) return null;
    field=i;
  }
  if (field===-1) return null;
  const critical=/\d+(?:[.,]\d+)?|[+−–—%‰<>≤≥=×÷-]|(?<![\p{L}\p{N}_])(?:не|ни|нет|not|no|never)(?![\p{L}\p{N}_])/giu;
  const rest=text=>text.replace(critical,'').replace(/\s+/g,' ').trim();
  const a=previous[field], b=next[field];
  if (rest(a)!==rest(b) || (a.match(critical)||[]).join(' ')===(b.match(critical)||[]).join(' ')) return null;
  // Keep the common words around the edit so that a long question still
  // shows what changed, rather than two truncated identical prefixes.
  let start=0;
  while (start<a.length && start<b.length && a[start]===b[start]) start++;
  const clip=text=> (start>34 ? '…' : '') + text.slice(Math.max(0,start-34), start+48) +
    (text.length>start+48 ? '…' : '');
  return {field,readings:[clip(a),clip(b)]};
}

async function rowFromFrame(frame, field) {
  const rect=frame?.regions?.[field];
  if (!rect || typeof frame.image!=='string' || !frame.image.startsWith('data:image/')) return null;
  const image=new Image();
  image.src=frame.image;
  await image.decode();
  // Coordinates are relative to the pixel buffer that was actually submitted,
  // including when it contained stacked manual crops or a single focus area.
  const x=Math.max(0,Math.floor(rect.x*image.naturalWidth));
  const y=Math.max(0,Math.floor(rect.y*image.naturalHeight));
  const width=Math.min(image.naturalWidth-x,Math.ceil(rect.w*image.naturalWidth));
  const height=Math.min(image.naturalHeight-y,Math.ceil(rect.h*image.naturalHeight));
  if (!(width>0 && height>0)) return null;
  const canvas=document.createElement('canvas');
  const scale=Math.min(2,900/width,180/height);
  canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
  canvas.getContext('2d').drawImage(image,x,y,width,height,0,0,canvas.width,canvas.height);
  return canvas.toDataURL('image/png');
}

function reviewReading(texts, warnings, frame, review=null) {
  // Do not publish an answer based on (or cached before) the unresolved pixels.
  state.solveJob?.controller.abort();state.solveJob=null;
  state.currentKey=null;state.displayedKey=null;state.confirmedTexts=null;state.firstSeenAt=null;
  window.QuizChart?.show(null);
  const editable = review?.repeated && validFields(texts.length) && texts.every(Boolean);
  const key=JSON.stringify([texts,warnings.map(w=>[w.field,w.readings]), Boolean(editable), review?.identity]);
  if (key===state.warningKey) return;
  state.warningKey=key;
  showTexts(texts);
  const view={title:'Сверь распознанный текст', subtitle:'Чтения различаются. QuizLens перепроверяет кадр.',
    unclear:true, readingWarnings:warnings};
  if (editable) {
    view.subtitle = 'Сверь с изображением, при необходимости исправь текст и подтверди.';
    view.reviewEditor = {texts, confirm(edits) {
      if (state.readingReview !== review || state.response !== view || review.corrected) {
        throw new Error('Кадр уже изменился. Сверь текущий вопрос.');
      }
      review.corrected = texts.slice();
      for (const edit of edits) review.corrected[edit.field] = edit.text;
      // Only apply the correction after another observation of the exact same question and choices.
      state.ocrJob?.controller.abort();state.ocrJob=null;
      state.lastRead=-Infinity;
    }};
  }
  answer(view);status(editable ? 'Нужно подтверждение' : 'Проверяю символы','problem');
  const missing=warnings.find(w=>!w.image);
  if (!missing || !frame) return;
  rowFromFrame(frame,missing.field).then(image=>{
    if (image && state.warningKey===key && state.response===view) {
      missing.image=image;missing.fullRow=true;
      const selector='[data-reading-warning="'+missing.field+'"]';
      for (const container of [el('answer'),state.pip?.document?.getElementById('pipAnswer')]) {
        const figure=container?.querySelector(selector);
        if (figure) drawWarningImage(figure,missing);
      }
    }
  }).catch(()=>{}); // The alternatives remain visible if the image cannot be decoded.
}

function acceptTexts(raw,session,comparisonTexts,hint={},observedAt=performance.now(),frame=null) {
  if (session!==state.session) return;
  if(!Array.isArray(raw) || (raw.length!==0 && !validFields(raw.length))) throw new Error('OCR вернул некорректное число областей.');
  let texts=raw.map(s=>typeof s==='string'?s.normalize('NFC').replace(/\s+/g,' ').trim():'');
  const selection=selectionForFrame(hint,texts.length-1);
  const warnings=warningsForFrame(hint.readingWarnings,texts.length);
  const snapshot=JSON.stringify([texts,selection.selectionMode,selection.expectedCount]);
  const identity=JSON.stringify(warnings.map(item => [item.field,item.readings]));
  const prior=state.readingReview;
  const approved=prior?.corrected && prior.snapshot===snapshot && (!warnings.length || prior.identity===identity);
  if (approved) {
    texts=prior.corrected;
    comparisonTexts=null;
  } else if (warnings.length) {
    if (prior && prior.snapshot===snapshot && prior.identity===identity) {
      prior.repeated=true;
    } else {
      state.readingReview={snapshot,identity,repeated:false};
    }
    state.previousReading=null;state.pendingSince=null;state.candidateKey=null;state.candidateSeenAt=null;
    noteCapture(texts,'partial',observedAt);
    reviewReading(texts,warnings,frame,state.readingReview);
    return;
  } else {
    state.readingReview=null;
    if (prior?.corrected) {
      state.solveJob?.controller.abort();state.solveJob=null;
      state.previousReading=null;state.currentKey=null;state.displayedKey=null;
      state.warningKey=null;state.confirmedTexts=null;
    }
  }
  const compared=Array.isArray(comparisonTexts) && comparisonTexts.length===raw.length && comparisonTexts.every(s=>typeof s==='string')
    ? comparisonTexts : texts;
  // Percent spacing often changes across OCR passes. The server still gets
  // what was read, and options that differ only in typography stay distinct.
  const compact=compared.map((value,i)=>i ? value.replace(/^([+−-]?\d+(?:[,.]\d+)?)\s+([%‰])$/, '$1$2') : value);
  const fields=new Set(compact.slice(1)).size===texts.length-1 ? compact : compared;
  const complete=validFields(texts.length) && texts.every(Boolean) && fields.every(Boolean) && new Set(fields.slice(1)).size===texts.length-1;
  const change=complete && !state.tabReading ? criticalDifference(state.previousReading,texts) : null;
  state.previousReading=complete ? texts : null;
  // Keep option order, negation, case and mathematical symbols in the identity.
  if (complete || texts.some(Boolean) || state.captureIssue || state.currentKey) {
    noteCapture(texts,complete?'complete':'partial',observedAt);
  }
  const key=complete ? JSON.stringify([el('model').value,[...state.selected].sort(),fields,selection.selectionMode,selection.expectedCount]) : '';

  if (change && key!==state.currentKey) {
    state.candidateKey=key;state.candidateSeenAt=observedAt;state.pendingSince=null;
    reviewReading(texts,[change],frame);
    return;
  }
  if(!state.tabReading && !approved && key!==state.currentKey && key!==state.candidateKey) {
    window.QuizChart?.show(null);
    if(state.pendingSince===null) state.pendingSince=performance.now();
    state.candidateKey=key;state.candidateSeenAt=observedAt;
    if((complete || state.currentKey===null) && !state.warningKey) {
      state.displayedKey=null;
      showTexts(texts);
      answer({title:'Проверяю текст…'});status('Распознаю','active');
    }
    return;
  }
  state.warningKey=null;
  const seenAt=state.candidateSeenAt ?? observedAt;
  state.candidateKey=null;state.pendingSince=null;state.candidateSeenAt=null;
  if(key!==state.currentKey) {
    // Two consecutive OCR reads now agree on the replacement (or on an incomplete frame).
    state.solveJob?.controller.abort();state.solveJob=null;
    state.currentKey=key;state.displayedKey=null;state.retrySolveAt=0;
    state.firstSeenAt=complete ? seenAt : null;state.confirmedTexts=complete ? texts : null;
    showTexts(texts);
    if(!complete) {
      if(state.layoutMode==='auto') {
        state.regions=[];paintLiveAreas();controls();showTexts(null);
        answer({title:state.captureIssue || 'Ожидаю вопрос и варианты'});
      } else {
        answer({title:'Жду все '+(texts.length-1)+(texts.length<=5?' варианта':' вариантов'),subtitle:'Проверь разметку, если вопрос уже полностью виден.'});
      }
      status('Ожидаю варианты','active');
    } else if (selection.selectionMode==='unknown') {
      el('captureSettings').open = true;
      answer({title:'Уточни формат вопроса',subtitle:'В поле «Сколько вариантов можно отметить?» выбери «Один вариант» или «Несколько вариантов».',unclear:true});
      status('Нужен формат','problem');
    }
  }
  if (complete && selection.selectionMode!=='unknown') {
    const chart=window.QuizChart?.show(texts[0],selection.selectionMode);
    if (chart) {
      if (state.displayedKey!==key) {
        answer({title:'Нужны данные с изображения',subtitle:'Открой разбор, чтобы сверить значения и подписи.',unclear:true});
        status('Нужен рисунок','active');state.displayedKey=key;
      }
      return;
    }
    solve(texts,key,session,selection);
  } else {
    window.QuizChart?.show(null);
  }
}

function acceptLayout(result,session,observedAt,frame) {
  const {regions,texts}=result;
  if(!Array.isArray(regions) || !Array.isArray(texts)) throw new Error('Не удалось прочитать авторазметку.');
  state.captureIssue=result.captureStatus==='partial' ? result.hint || 'Прокрути до конца списка ответов.' : null;
  if(!regions.length && !texts.length) {
    // A waiting room is a normal first frame. Avoid a pointless second full scan.
    if(!state.currentKey && state.candidateKey===null && !state.regions.length && !state.warningKey) {
      if(state.captureIssue) noteCapture([],'partial',observedAt);
      state.currentKey='';status('Ожидаю вопрос','active');
      if(state.response?.title!==(state.captureIssue || 'Ожидаю вопрос и варианты')) {
        showTexts(null);answer({title:state.captureIssue || 'Ожидаю вопрос и варианты'});
      }
    } else {
      acceptTexts([],session,undefined,{},observedAt);
    }
    return;
  }
  const valid=validFields(regions.length) && regions.every(r=>r &&
    [r.x,r.y,r.w,r.h].every(Number.isFinite) && r.x>=0 && r.y>=0 && r.w>0 && r.h>0 &&
    r.x+r.w<=1.00001 && r.y+r.h<=1.00001);
  if(!valid || texts.length!==regions.length || !texts.every(t=>typeof t==='string' && t.trim())) {
    throw new Error('Не удалось определить области вопроса и ответов.');
  }
  let areas=regions;
  if(state.autoFocus) {
    const [left,top,width,height]=searchPixels();
    areas=regions.map(r=>({
      x:(left+width*r.x)/preview.videoWidth, y:(top+height*r.y)/preview.videoHeight,
      w:width*r.w/preview.videoWidth, h:height*r.h/preview.videoHeight
    }));
  }
  const moved=state.regions.length!==areas.length || areas.some((box,i)=>
    ['x','y','w','h'].some(k=>Math.abs(box[k]-state.regions[i][k])>.003));
  if(moved) {
    state.regions=areas;
    paintLiveAreas();
    controls();
  }
  acceptTexts(texts,session,result.comparisonTexts,result,observedAt,frame);
}

async function recognize(session) {
  const job={controller:new AbortController(),frameAt:performance.now()};state.ocrJob=job;
  state.lastRead=job.frameAt;state.dirtyFrame=false;
  try {
    const auto=state.layoutMode==='auto';
    const payload=auto ? {image:wholeWindowImage()} : ocrImage();
    const chartFrame=auto && !state.autoFocus ? payload.image : window.QuizChart?.needsFrame(session) ? wholeWindowImage(true) : null;
    payload.lectureIds=[...state.selected];
    const result=await postJson(auto?'/api/detect-layout':'/api/ocr',payload,job.controller.signal);
    if(state.session!==session || job.controller.signal.aborted) return;
    const frame={image:payload.image,regions:auto ? result.regions : payload.regions};
    if(auto) acceptLayout(result,session,job.frameAt,frame);
    else acceptTexts(result.texts,session,result.comparisonTexts,result,job.frameAt,frame);
    window.QuizChart?.remember(result.texts,chartFrame,session);
  } catch(error) {
    if(state.session!==session || job.controller.signal.aborted) return;
    state.currentKey=null;state.candidateKey=null;state.pendingSince=null;state.displayedKey=null;
    state.candidateSeenAt=null;state.firstSeenAt=null;state.confirmedTexts=null;
    state.warningKey=null;state.previousReading=null;state.readingReview=null;window.QuizChart?.show(null);
    state.solveJob?.controller.abort();state.solveJob=null;
    state.retryOcrAt=performance.now()+1900;
    showTexts(null);
    answer({title:'Не удалось прочитать',subtitle:error.message,unclear:true});
    status('Ошибка OCR','problem');
  } finally { if(state.ocrJob===job) state.ocrJob=null; }
}
function tick() {
  if(!state.running || !state.stream || preview.readyState<2 || !preview.videoWidth) return;
  const now=performance.now();
  try {
    const next=fingerprint();
    if(differs(next,state.signature)) {
      state.signature=next;
      state.dirtyFrame=true;
    }
    // During a prolonged transition an old answer must not remain on screen.
    if(state.currentKey && state.pendingSince!==null && now-state.pendingSince>=1400) {
      state.currentKey=null;state.displayedKey=null;state.confirmedTexts=null;
      state.solveJob?.controller.abort();state.solveJob=null;
      showTexts(null);answer({title:'Проверяю новый кадр…'});status('Распознаю','active');
    }
    // Pixels only schedule a read. Motion cannot invalidate a captured OCR result,
    // reset a text candidate, or cancel a lookup for a confirmed question.
    const quickRead=state.currentKey===null || state.candidateKey!==null;
    let wait=quickRead ? 240 : state.dirtyFrame || state.currentKey==='' ? 550 : 1800;
    if(state.layoutMode==='auto') {
      // The first read/shifts are inexpensive to detect by pixels. On an unchanged
      // page, periodically recheck the entire layout for tiny edits or movement.
      wait=quickRead ? 300 : state.dirtyFrame ? 700 : 3000;
    }
    if(state.warningKey && state.candidateKey===null) wait=state.readingReview?.repeated ? (state.dirtyFrame ? 400 : 1600) : 550;
    if(!state.ocrJob && now>=state.retryOcrAt && now-state.lastRead>=wait) recognize(state.session);
  } catch(error) { stop('Не удалось прочитать кадр. '+error.message); }
}

preview.addEventListener('resize',()=>{
  if(!state.stream) return;
  fitVideo();
  if(state.layoutMode==='auto') {
    if(state.autoFocus && preview.videoHeight && Math.abs((preview.videoWidth/preview.videoHeight)/state.focusAspect-1)>.04) {
      state.autoFocus=null;state.focusAspect=0;
      state.regions=[];paintLiveAreas();controls();
    }
    if(state.running) start();
    return;
  }
  if(validFields(state.regions.length) && preview.videoWidth && Math.abs((preview.videoWidth/preview.videoHeight)/(settings().regions?.aspect||1)-1)>.04) {
    stop();state.regions=[];paintRegions(el('liveAreas'),[]);controls();
    notify('Форма окна изменилась. Проверь кадр и заново разметь области.');
  }
});
window.addEventListener('storage', event => {
  if(event.key===storageKey) {
    try {
      const before = JSON.parse(event.oldValue || '{}') || {};
      const after = JSON.parse(event.newValue || '{}') || {};
      if (before.model !== after.model) {
        modelPreferenceChanged = true;
        loadModels();
      }
      if (JSON.stringify(before.selected) !== JSON.stringify(after.selected)) {
        loadLectures().catch(error => { el('fileInfo').textContent = error.message; });
      }
    } catch { /* Ignore unrelated or malformed settings. */ }
  }
  if (event.key === 'quizlens.sources.revision') {
    loadLectures().catch(error => { el('fileInfo').textContent = error.message; });
  }
  if(event.key==='quizlens.corrections.rev') {
    answerCache.clear();
    if(state.running) start();
  }
});
window.addEventListener('pagehide',()=>{
  if(state.stream) state.stream.getTracks().forEach(t=>t.stop());
  clearWorkflow();
});
idleAnswer(); controls();
api('/api/health').then(health => {
  el('nativeLink').hidden = health.nativeCapture !== true;
}).catch(() => {});
loadLectures().catch(error=>{el('fileInfo').textContent=error.message;});
loadModels();
