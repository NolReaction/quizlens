'use strict';
const el = id => document.getElementById(id);
async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Локальный сервер вернул ошибку.');
  return data;
}
let running = false;
let loadingModels = false;

let statusTimer = null;
let statusLoading = false;

async function refreshOcrStatus() {
  clearTimeout(statusTimer);
  if (document.hidden || statusLoading) return;
  statusLoading = true;
  const node = el('ocrStatus');
  try {
    const data = await request('/api/ocr-status');
    const labels = {
      preparing: 'OCR готовится в фоне. Проверку можно начать во время подготовки.',
      ready: 'OCR готов к распознаванию.',
      failed: 'Фоновая подготовка OCR завершилась ошибкой. Запусти проверку, чтобы повторить попытку.'
    };
    const label = data.displayDetail && data.detail ? data.detail : (labels[data.state] || '');
    node.hidden = !label;
    node.className = 'ocr-status ' + (data.state === 'ready' ? 'ok' : ['failed', 'unavailable'].includes(data.state) ? 'warn' : '');
    if (node.textContent !== label) node.textContent = label;
    if (!document.hidden && data.state === 'preparing') statusTimer = setTimeout(refreshOcrStatus, 1200);
  } catch {
    node.hidden = true;
  } finally { statusLoading = false; }
}

document.addEventListener('visibilitychange', refreshOcrStatus);

function updateControls() {
  const busy = running || loadingModels;
  el('test').disabled = busy;
  const metrics = el('questionSet').value === 'metrics';
  const checkAnswers = Boolean(el('chatModel').value) || metrics;
  el('testModel').disabled = busy || !checkAnswers;
  el('testModel').textContent = metrics ? 'Проверить расчёты по тексту' : 'Проверить только модель';
  el('refreshModels').disabled = busy;
  el('chatModel').disabled = busy;
  el('questionSet').disabled = running;
  el('test').textContent = checkAnswers ? 'Проверить OCR и ответ' : 'Проверить OCR';
}

async function loadModels() {
  loadingModels = true;
  updateControls();
  const select = el('chatModel');
  const selected = select.value;
  let models = [];
  try {
    const data = await request('/api/models');
    models = data.models;
    el('modelMessage').textContent = models.length ? '' : 'Расчёты в наборе «Проценты и метрики» можно проверить без модели. Для остальных наборов загрузи модель в LM Studio и нажми «Обновить».';
  } catch (error) {
    el('modelMessage').textContent = error.message + ' Проверка OCR доступна отдельно.';
  } finally {
    select.replaceChildren(new Option('Без локальной модели', ''));
    models.forEach(name => select.add(new Option(name, name)));
    select.value = models.includes(selected) ? selected : (models[0] || '');
    loadingModels = false;
    updateControls();
  }
  try {
    const info = await request('/api/embedding-models');
    el('embeddings').replaceChildren(...info.models.map(name => new Option(name, name)));
  } catch { /* Manual model IDs can still be entered. */ }
}
function renderQuestion(card) {
  const lines = [card.question, ...card.options];
  const canvas = document.createElement('canvas');
  canvas.width = 1600; canvas.height = 75 * lines.length;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle = '#141f2a';ctx.textBaseline = 'middle';ctx.font='34px sans-serif';
  const regions = lines.map((line, i) => {
    ctx.fillText(line,20,75*i+38,canvas.width-40);
    return {x:0,y:i/lines.length,w:1,h:1/lines.length};
  });
  return {image:canvas.toDataURL('image/png'),regions};
}
const canon = text => text.toLocaleLowerCase('ru').replace(/ё/g, 'е').replace(/\s+/g, ' ').trim();
const seconds = ms => (ms / 1000).toLocaleString('ru-RU', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' с';

// Restrict compatibility folding to textual glyphs. NFKC also folds exponents
// and other mathematical characters that should stay distinct in a quiz.
function typographyKey(text) {
  const presentationForms = /[\uFF21-\uFF3A\uFF41-\uFF5A\uFB00-\uFB06]/gu;
  const letters = text.normalize('NFC').replace(presentationForms, c => c.normalize('NFKC'));
  // Hyphens and dashes can replace each other in words (e.g. веб-сервис).
  // Preserve them near numbers, single-letter variables and arithmetic minus signs.
  return canon(letters).replace(/(?<=\p{L}{2})([-\u2010\u2011\u2012\u2013\u2014])(?=\p{L}{2})/gu, '-')
    .replace(/(?<=\p{L}{2} )[-\u2010\u2011\u2012\u2013\u2014](?= \p{L}{2})/gu, '—');
}

function mappedCharacters(text) {
  const chars = [];
  let offset = 0;
  for (const glyph of text) {
    const start = offset;
    offset += glyph.length;
    if (/\s/u.test(glyph)) {
      if (!chars.length) continue;
      if (chars.at(-1).value === ' ') chars.at(-1).end = offset;
      else chars.push({value:' ', start, end:offset});
    } else {
      for (const value of glyph.toLocaleLowerCase('ru').replace(/ё/g, 'е')) {
        chars.push({value, start, end:offset});
      }
    }
  }
  if (chars.at(-1)?.value === ' ') chars.pop();
  return chars;
}

function changedRanges(expected, read) {
  const a = mappedCharacters(expected);
  const b = mappedCharacters(read);
  const shortest = Math.min(a.length, b.length);
  let prefix = 0, suffix = 0;
  while (prefix < shortest && a[prefix].value === b[prefix].value) prefix++;
  while (prefix + suffix < shortest && a[a.length-1-suffix].value === b[b.length-1-suffix].value) suffix++;
  function range(chars, text) {
    const start = chars[prefix]?.start ?? text.length;
    const end = prefix + suffix === chars.length ? start : chars[chars.length-1-suffix].end;
    return {start, end};
  }
  return [range(a, expected), range(b, read)];
}

function showChangedField(row, index, expected, read) {
  const group = document.createElement('div');
  group.className = 'ocr-difference';
  row.append(group);
  const label = index ? 'Вариант ' + index : 'Формулировка вопроса';
  detail(group, label, 'diff-label');
  const [expectedRange, readRange] = changedRanges(expected, read);
  function line(title, value, range) {
    const node = document.createElement('small');
    node.className = 'diff-line';
    const mark = document.createElement('mark');
    mark.textContent = value.slice(range.start,range.end);
    node.append(title + ' — «' + value.slice(0,range.start), mark, value.slice(range.end) + '»');
    group.append(node);
  }
  line('Ожидалось', expected, expectedRange);
  line('Прочитано', read, readRange);
  const expectedChars = Array.from(expected.slice(expectedRange.start, expectedRange.end));
  const readChars = Array.from(read.slice(readRange.start, readRange.end));
  const length = Math.max(expectedChars.length, readChars.length);
  if (length > 0 && length <= 4) {
    const codepoints = chars => chars.length
      ? chars.map(c => 'U+' + c.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')).join(' ')
      : 'нет символа';
    detail(group, codepoints(expectedChars) + ' → ' + codepoints(readChars), 'diff-unicode');
  }
}

async function timeStage(label, operation) {
  const start = performance.now();
  const render = () => { el('progress').textContent = label + ' · ' + seconds(performance.now() - start); };
  render();
  const timer = setInterval(render, 400);
  try {
    const value = await operation();
    return {value, elapsed: performance.now() - start};
  } catch (error) {
    return {error: error.message, elapsed: performance.now() - start};
  } finally {
    clearInterval(timer);
    el('progress').textContent = '';
  }
}

function rowFor(card) {
  const row = document.createElement('li');
  row.textContent = card.question;
  el('results').append(row);
  return row;
}

function detail(row, text, className = '') {
  const line = document.createElement('small');
  line.textContent = text;
  line.className = className;
  row.append(line);
}

function shuffleChoices(card) {
  const order = card.options.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const other = Math.floor(Math.random() * (i + 1));
    [order[i], order[other]] = [order[other], order[i]];
  }
  const expected = card.choices || [card.choice];
  const choices = order.flatMap((oldIndex, position) => expected.includes(oldIndex + 1) ? [position + 1] : []);
  return {...card, options: order.map(i => card.options[i]), choices, choice: choices.length === 1 ? choices[0] : null};
}

async function runCheck(withOCR) {
  if (running) return;
  const model = el('chatModel').value;
  const selection = el('questionSet').value;
  const checkAnswers = Boolean(model) || selection === 'metrics';
  if (!withOCR && !checkAnswers) {
    el('message').textContent = 'Выбери модель для ответа.';
    return;
  }

  running = true;
  updateControls();
  el('results').replaceChildren();
  el('message').textContent = 'Готовлю вопросы…';
  try {
    const [bank, library] = await Promise.all([
      request('/check-questions.json'),
      checkAnswers ? request('/api/lectures') : Promise.resolve({lectures: []})
    ]);
    const items = selection === 'basic' ? bank.items : bank[selection]?.map(shuffleChoices);
    if (!Array.isArray(items) || !items.length) throw Error('В проверочном наборе нет вопросов.');
    const notebook = library.lectures.find(item => selection === 'metrics'
      ? item.builtIn && item.name === 'analytics-demo.txt' : item.id === bank.lectureId);
    if (checkAnswers && !notebook) throw Error('Не удалось найти встроенную демолекцию. Распакуй полный архив приложения и запусти его снова.');

    let ocrCompleted = 0, exactCount = 0, typographyCount = 0, disputed = 0, solved = 0, correct = 0;
    const ocrTimes = [];
    let ocrFailure = '', modelFailure = '';
    if (model) el('message').textContent = 'Модель для проверки — ' + model;
    else el('message').textContent = checkAnswers ? 'Проверяю расчёты по демолекции.' : 'Проверяю распознавание текста.';
    for (const [index, card] of items.entries()) {
      const row = rowFor(card);
      const prefix = 'Вопрос ' + (index + 1) + ' из ' + items.length + ' · ';
      const original = [card.question, ...card.options];
      let texts = original;
      let ocrMs = 0;
      if (withOCR) {
        const name = index === 0 ? 'OCR · первый вызов, возможна подготовка компонентов' : 'OCR';
        const frame = renderQuestion(card);
        if (notebook) frame.lectureIds = [notebook.id];
        const read = await timeStage(prefix + name, () => request('/api/ocr', frame));
        ocrMs = read.elapsed;
        texts = read.value?.texts;
        if (read.error || !Array.isArray(texts) || texts.length !== original.length || texts.some(t => typeof t !== 'string')) {
          ocrFailure = read.error || 'Некорректный ответ службы OCR.';
          detail(row, 'Ошибка OCR за ' + seconds(ocrMs) + '. ' + ocrFailure, 'warn');
          if (checkAnswers) detail(row, 'Поиск и ответ для этого вопроса пропущены.');
          break;
        }
        ocrCompleted++;
        ocrTimes.push(ocrMs);
        const differences = original.map((expected, i) => ({expected, read:texts[i], index:i}))
          .filter(field => canon(field.expected) !== canon(field.read));
        const exact = differences.length === 0;
        const typographyOnly = !exact && differences.every(field => typographyKey(field.expected) === typographyKey(field.read));
        const comparison = read.value.comparisonTexts;
        const matchedLectureTerms = !exact && !typographyOnly && Array.isArray(comparison) &&
          comparison.length === original.length && differences.every(field =>
            typeof comparison[field.index] === 'string' && typographyKey(field.expected) === typographyKey(comparison[field.index]));
        if (exact) exactCount++;
        if (typographyOnly) typographyCount++;
        const stageLabel = index === 0 ? 'OCR, первый вызов' : 'OCR';
        let verdict = 'текст отличается';
        if (exact) verdict = 'текст совпал';
        else if (typographyOnly) verdict = 'отличается только типографика';
        else if (matchedLectureTerms) verdict = 'похожие буквы в термине · написание сверено с лекцией';
        const warning = !exact && !typographyOnly && !matchedLectureTerms;
        detail(row, stageLabel + ' · ' + seconds(ocrMs) + ' · ' + verdict, exact ? 'ok' : (warning ? 'warn' : ''));
        differences.forEach(field => showChangedField(row, field.index, field.expected, field.read));
        if (read.value.readingWarnings?.length) {
          disputed++;
          for (const warning of read.value.readingWarnings) {
            const name = warning.field === 0 ? 'Вопрос' : 'Вариант ' + warning.field;
            detail(row, name + ' · чтения расходятся: ' + warning.readings.join(' / '), 'warn');
          }
          if (checkAnswers) detail(row, 'Ответ отложен до проверки текста.');
          continue;
        }
        if (texts.some(text => !text.trim())) {
          if (checkAnswers) detail(row, 'Поиск и ответ пропущены. Не все области распознаны.', 'warn');
          continue;
        }
      }

      if (!checkAnswers) continue;
      if (modelFailure) {
        detail(row, 'Поиск и ответ пропущены после ошибки запроса.');
        continue;
      }
      const result = await timeStage(prefix + 'поиск по лекции и проверка ответа', () =>
        request('/api/solve', {question: texts[0], options: texts.slice(1), model, lectureIds: [notebook.id], channel: 'check'}));
      if (result.error) {
        modelFailure = result.error;
        detail(row, 'Ошибка поиска или модели за ' + seconds(result.elapsed) + '. ' + modelFailure, 'warn');
        if (withOCR) continue;
        break;
      }
      solved++;
      const expected = card.choices || [card.choice];
      const choices = result.value.choices ?? (result.value.choice ? [result.value.choice] : []);
      const ok = choices.length === expected.length && expected.every(n => choices.includes(n));
      if (ok) correct++;
      const selected = choices.length > 1 ? 'варианты ' + choices.join(', ') : choices.length ? 'вариант ' + choices[0] : 'нет подтверждённого варианта';
      const verdict = selected + ' / ожидается ' + expected.join(', ');
      detail(row, 'Поиск и ответ · ' + seconds(result.elapsed) + ' · ' + verdict, ok ? 'ok' : 'warn');
      const verification = result.value.verification;
      if (verification?.kind === 'calculation') detail(row, verification.equation);
      if (selection === 'multiple' && choices.length && !ok) {
        const missed = expected.filter(n => !choices.includes(n));
        const extra = choices.filter(n => !expected.includes(n));
        if (missed.length) detail(row, 'Пропущены номера · ' + missed.join(', '));
        if (extra.length) detail(row, 'Лишние номера · ' + extra.join(', '));
      }
      const evidence = result.value.evidence ?? (choices.length === 1 && result.value.source && result.value.excerpt ? [result.value] : []);
      for (const item of evidence) {
        const proof = document.createElement('details');
        proof.className = 'evidence';
        const title = document.createElement('summary');
        title.textContent = selection === 'multiple' ? 'Вариант ' + item.choice + ' · цитата из лекции' : 'Цитата из лекции';
        proof.append(title);
        detail(proof, '«' + item.excerpt + '»');
        detail(proof, item.source);
        row.append(proof);
      }
      if (withOCR) detail(row, 'OCR, поиск и ответ вместе · ' + seconds(ocrMs + result.elapsed));
      if (!ok) {
        if (result.value.detail) detail(row, result.value.detail);
        detail(row, notebook.name + (card.cell ? ' · ячейка ' + card.cell : ''));
      }
    }
    const summary = [];
    if (withOCR) {
      summary.push('OCR завершён для ' + ocrCompleted + ' из ' + items.length + ' вопросов.');
      if (ocrCompleted) summary.push('Текст совпал в ' + exactCount + ' из ' + ocrCompleted + ' распознанных кадров.');
      if (disputed) summary.push('Кадров для сверки — ' + disputed + '.');
      if (typographyCount) summary.push('Кадров только с типографическими отличиями — ' + typographyCount + '.');
      const laterMax = Math.max(...ocrTimes.slice(1));
      if (ocrTimes.length > 1 && ocrTimes[0] > laterMax * 1.5 && ocrTimes[0] - laterMax > 400) {
        summary.push('Первый вызов OCR занял больше времени. Перед занятием запусти проверку заранее и оставь QuizLens открытым.');
      }
    } else summary.push('Проверка по готовому тексту.');
    if (checkAnswers) {
      summary.push('Поиск и ответ завершены для ' + solved + ' из ' + items.length + ' вопросов.');
      if (solved) summary.push((selection === 'multiple' ? 'Полностью совпавших наборов — ' : 'Верных вариантов — ') + correct + ' из ' + solved + '.');
    }
    if (ocrFailure) summary.push(checkAnswers ? 'После ошибки OCR доступна отдельная проверка по тексту.' : 'После ошибки OCR дальнейшее распознавание остановлено.');
    if (modelFailure) summary.push('После ошибки повторные запросы модели остановлены.');
    el('message').textContent = summary.join(' ');
  } catch (error) {
    el('message').textContent = error.message;
  } finally {
    running = false;
    updateControls();
    if (withOCR) refreshOcrStatus();
  }
}
el('test').onclick = () => runCheck(true);
el('testModel').onclick = () => runCheck(false);
el('chatModel').onchange = updateControls;
el('questionSet').onchange = updateControls;
async function configure(enabled) {
  const model=enabled?el('embedding').value.trim():'';
  if(enabled && !model){el('embeddingMessage').textContent='Введи идентификатор модели.';return;}
  el('prepare').disabled=el('disable').disabled=true;
  el('embeddingMessage').textContent=enabled?'Готовлю локальный индекс…':'Меняю настройку…';
  try {
    const status=await request('/api/semantic',{model});
    el('embeddingMessage').textContent=enabled?'Индекс готов. Фрагментов: '+status.prepared+'.':'Включён поиск по словам.';
    el('embedding').value=model;
  }catch(e){el('embeddingMessage').textContent=e.message;}
  finally{el('prepare').disabled=el('disable').disabled=false;}
}
el('refreshModels').onclick=loadModels;
el('prepare').onclick=()=>configure(true);el('disable').onclick=()=>configure(false);
request('/api/semantic').then(data=>{el('embedding').value=data.model||'';}).catch(()=>{});
loadModels();
refreshOcrStatus();
