'use strict';

const byId = id => document.getElementById(id);
const messages = {
  'need-connection': ['idle', 'Ожидает подключения', 'Открой QuizLens и нажми значок расширения на странице «Захват».'],
  'can-bind': ['ready', 'Экран захвата найден', 'Подключи это окно, чтобы передавать в него вопросы из вкладки с квизом.'],
  bound: ['ready', 'QuizLens подключён', 'Перейди во вкладку с квизом, нажми значок расширения и выбери «Читать вопросы».'],
  'can-start': ['ready', 'Вкладка готова к чтению', 'Нажми «Читать вопросы». Видимый вопрос и варианты появятся в QuizLens.'],
  waiting: ['live', 'Жду вопрос', 'Чтение включено. Когда появятся вопрос и варианты, расширение передаст их в QuizLens.'],
  partial: ['attention', 'Вопрос виден не целиком', 'Прокрути вкладку так, чтобы формулировка и варианты были видны вместе. Чтение продолжится автоматически.'],
  multiple: ['attention', 'Видно несколько вопросов', 'Оставь в поле зрения один вопрос с вариантами, чтобы передать его в QuizLens.'],
  question: ['live', 'Вопрос передан', 'Вопрос и варианты доставлены в QuizLens. Результат поиска появится на экране захвата.'],
  unsupported: ['attention', 'Эту страницу прочитать нельзя', 'Открой обычную веб-страницу с квизом или используй «Выбрать окно» в QuizLens.'],
};

let activeTab;
let revision = 0;
let working = false;
let refreshPending = false;

function write(id, text) {
  const element = byId(id);
  if (element.textContent !== text) element.textContent = text;
}

function status(phase, tone, heading, detail) {
  document.body.dataset.state = phase;
  document.body.dataset.tone = tone;
  write('message', heading);
  write('description', detail);
}

function button(id, command, label, primary = false) {
  const element = byId(id);
  element.hidden = !command;
  element.disabled = !command;
  element.dataset.command = command || '';
  element.toggleAttribute('data-primary', Boolean(primary));
  if (command) write(id, label);
}

function show(reply) {
  const copy = messages[reply.status];
  if (!copy) throw Error('Обнови расширение на странице расширений браузера и открой его ещё раз.');
  let [tone, heading, detail] = copy;
  const switchSource = reply.status === 'can-start' && reply.reading;
  const replaceCapture = reply.status === 'can-bind' && reply.receiverConnected;
  const captureKnown = reply.receiverConnected || Number.isInteger(reply.availableCaptureTabId);
  if (switchSource) {
    heading = 'Читается другая вкладка';
    detail = 'Можно переключить чтение на текущую вкладку. Предыдущая остановится.';
  } else if (replaceCapture) {
    heading = 'Открыт другой экран захвата';
    detail = reply.reading ? 'Подключение переключится на это окно и остановит текущее чтение.' : 'Можно переключить подключение QuizLens на это окно.';
  } else if (reply.status === 'need-connection') {
    const platform = navigator.userAgentData?.platform || navigator.platform;
    if (captureKnown) detail = 'Перейди в открытый QuizLens и нажми значок расширения на странице «Захват».';
    else if (/win/i.test(platform)) detail = 'Запусти Start.cmd из папки QuizLens. На странице «Захват» нажми значок расширения.';
    else if (/mac/i.test(platform)) detail = 'Открой QuizLens.app. На странице «Захват» нажми значок расширения.';
  }
  status(reply.status, tone, heading, detail);

  byId('connection').hidden = false;
  write('receiverState', reply.receiverConnected ? 'Подключено' :
    (reply.here === 'capture' || captureKnown ? 'Ожидает подключения' : 'Связи нет'));
  const candidate = reply.status === 'can-start' && !reply.reading;
  const title = reply.reading ? reply.sourceTitle : (candidate ? activeTab?.title : '');
  const source = title || (reply.reading ? 'Выбранная вкладка' : (candidate ? 'Текущая вкладка' : 'Ещё не выбрана'));
  write('sourceLabel', candidate ? 'Эта вкладка' : 'Вкладка квиза');
  write('sourceTitle', source);
  byId('sourceTitle').title = title || '';
  byId('sourceRow').hidden = !reply.receiverConnected;

  const canFocusCapture = captureKnown && reply.here !== 'receiver' && reply.here !== 'capture';
  const canFocusSource = reply.reading && reply.here !== 'source';
  const focusFirst = ['stop', null, undefined].includes(reply.command);
  let actionLabel = reply.label;
  if (switchSource) actionLabel = 'Читать эту вкладку';
  if (replaceCapture) actionLabel = 'Подключить это окно';
  button('action', reply.command, actionLabel, !focusFirst);
  button('focusCapture', canFocusCapture && 'focus-capture', 'Перейти в QuizLens', focusFirst);
  button('focusSource', canFocusSource && 'focus-source', 'Перейти к квизу', focusFirst && !canFocusCapture);
}

function showError(error, command) {
  const title = {
    bind:'Не удалось подключиться',
    start:'Чтение не началось',
    stop:'Не удалось остановить чтение',
    'focus-capture':'Вкладка QuizLens недоступна',
    'focus-source':'Вкладка квиза недоступна',
  }[command] || 'Не удалось проверить связь';
  const text = error?.message || '';
  status('error', 'attention', title, /[а-яё]/i.test(text) ? text : 'Открой нужную вкладку и повтори проверку.');
  byId('connection').hidden = true;
  button('focusCapture', null);
  button('focusSource', null);
  button('action', 'inspect', 'Проверить снова', true);
}

async function update(command = 'inspect') {
  if (working) {
    if (command === 'inspect') refreshPending = true;
    return;
  }
  const attempt = ++revision;
  const deliberate = command !== 'inspect';
  if (deliberate) {
    working = true;
    byId('actions').setAttribute('aria-busy', 'true');
    for (const element of byId('actions').querySelectorAll('button')) element.disabled = true;
  }
  try {
    if (!activeTab) [activeTab] = await chrome.tabs.query({active:true, currentWindow:true});
    if (activeTab?.id == null) throw Error('Открой вкладку с QuizLens или квизом и повтори проверку.');
    const reply = await chrome.runtime.sendMessage({command, tabId:activeTab.id});
    if (attempt !== revision) return;
    if (!reply?.ok) throw Error(reply?.error || 'Расширение не получило ответ. Попробуй ещё раз.');
    if (command === 'focus-capture' || command === 'focus-source') {
      window.close();
      return;
    }
    show(reply);
  } catch (error) {
    if (attempt === revision) {
      refreshPending = false;
      showError(error, command);
    }
  } finally {
    if (deliberate) {
      working = false;
      byId('actions').removeAttribute('aria-busy');
      if (refreshPending) {
        refreshPending = false;
        update();
      }
    }
  }
}

for (const element of byId('actions').querySelectorAll('button')) {
  element.addEventListener('click', () => update(element.dataset.command));
}
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && ('pair' in changes || 'available' in changes)) update();
});
update();
