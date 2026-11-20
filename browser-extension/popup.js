'use strict';

const byId = id => document.getElementById(id);
const messages = {
  'need-connection': ['idle', 'Подключи приложение', 'Открой QuizLens и нажми значок расширения на странице «Захват».'],
  'can-bind': ['ready', 'Экран захвата найден', 'Подключи это окно, чтобы передавать в него вопросы из вкладки с квизом.'],
  bound: ['ready', 'Можно перейти к квизу', 'На вкладке с квизом открой расширение и включи «Чтение вкладки».'],
  'can-start': ['ready', 'Вкладка готова к чтению', 'Включи тумблер. Видимый вопрос и варианты появятся в QuizLens.'],
  waiting: ['live', 'Жду вопрос', 'Если вопрос уже виден, а статус не меняется, перейди в QuizLens и нажми «Выбрать окно» для чтения с экрана.'],
  partial: ['attention', 'Вопрос виден не целиком', 'Прокрути вкладку так, чтобы формулировка и варианты были видны вместе. Чтение продолжится автоматически.'],
  multiple: ['attention', 'Видно несколько вопросов', 'Оставь в поле зрения один вопрос с вариантами, чтобы передать его в QuizLens.'],
  question: ['live', 'Вопрос передан', 'Распознанный текст и состояние ответа видны на экране захвата QuizLens.'],
  reconnecting: ['attention', 'Восстанавливаю связь', 'Страница QuizLens обновляется. Подключение восстановится, когда она загрузится.'],
  unsupported: ['attention', 'Эту страницу прочитать нельзя', 'Открой обычную веб-страницу с квизом или используй «Выбрать окно» в QuizLens.'],
};
const commandButtons = ['readingSwitch','action','focusCapture','focusSource'].map(byId);
const pendingMessages = {
  inspect:'Проверяю связь…', bind:'Подключаю QuizLens…', start:'Включаю чтение…', stop:'Выключаю чтение…',
  'focus-capture':'Открываю QuizLens…', 'focus-source':'Открываю квиз…',
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

function connection(state, label) {
  byId('receiverState').dataset.connection = state;
  write('receiverState', label);
}

function readingToggle(command, scope, unknown = false) {
  const element = byId('readingSwitch');
  const on = command === 'stop';
  element.dataset.command = command || '';
  element.disabled = !command;
  element.setAttribute('aria-checked', String(on));
  write('readingValue', unknown ? '—' : on ? 'Включено' : 'Выключено');
  write('readingScope', scope);
}

function show(reply) {
  const copy = messages[reply.status];
  if (!copy) throw Error('Обнови расширение на странице расширений браузера и открой его ещё раз.');
  let [tone, heading, detail] = copy;
  const switchSource = reply.status === 'can-start' && reply.reading;
  const replaceCapture = reply.status === 'can-bind' && reply.receiverConnected;
  const captureKnown = reply.receiverConnected || reply.receiverRecovering || Number.isInteger(reply.availableCaptureTabId);
  if (switchSource) {
    heading = 'Читается другая вкладка';
    detail = 'Включи тумблер, чтобы читать эту вкладку. Чтение предыдущей остановится.';
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

  if (reply.receiverRecovering) connection('recovering', 'Переподключение…');
  else if (reply.receiverConnected) connection('connected', 'Подключено');
  else if (reply.here === 'capture' || captureKnown) connection('available', 'Не подключено');
  else connection('offline', 'Не подключено');

  const scopes = {
    bound:'Включается на вкладке квиза',
    unsupported: reply.reading ? 'Читается другая вкладка' : 'Здесь недоступно',
    reconnecting: reply.reading ? 'Ждёт QuizLens' : 'Ждёт подключения',
  };
  const readingCommand = ['start','stop'].includes(reply.command) ? reply.command : null;
  const scope = scopes[reply.status] || (readingCommand === 'start' ? 'Для этой вкладки'
    : readingCommand === 'stop' ? 'Активная вкладка квиза'
    : replaceCapture ? 'Другое окно захвата' : 'Сначала подключи QuizLens');
  readingToggle(readingCommand, scope);

  const candidate = reply.status === 'can-start' && !reply.reading;
  const title = reply.reading ? reply.sourceTitle : (candidate ? activeTab?.title : '');
  const source = title || (reply.reading ? 'Выбранная вкладка' : 'Текущая вкладка');
  write('sourceLabel', candidate ? 'Эта вкладка' : 'Читаемая вкладка');
  write('sourceTitle', source);
  byId('sourceTitle').title = title || '';
  byId('sourceRow').hidden = !reply.reading && !candidate;

  const canFocusCapture = captureKnown && !['receiver','capture'].includes(reply.here) && reply.receiverTabId !== activeTab?.id;
  const canFocusSource = reply.reading && reply.here !== 'source';
  const canBind = reply.command === 'bind';
  button('action', canBind && 'bind', replaceCapture ? 'Подключить это окно' : 'Подключить QuizLens', true);
  button('focusCapture', canFocusCapture && 'focus-capture', 'Перейти в QuizLens', !canBind && readingCommand !== 'start');
  button('focusSource', canFocusSource && 'focus-source', 'Перейти к квизу', !canFocusCapture);
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
  connection('error', 'Нет ответа');
  readingToggle(null, 'Состояние неизвестно', true);
  byId('sourceRow').hidden = true;
  button('focusCapture', null);
  button('focusSource', null);
  button('action', 'inspect', 'Проверить снова', true);
}

async function update(command = 'inspect', trigger = null) {
  if (working) {
    if (command === 'inspect') refreshPending = true;
    return;
  }
  const attempt = ++revision;
  const deliberate = Boolean(trigger);
  const restoreFocus = trigger && document.activeElement === trigger;
  if (deliberate) {
    working = true;
    byId('actions').setAttribute('aria-busy', 'true');
    for (const element of commandButtons) {
      if (!element.disabled) element.setAttribute('aria-disabled', 'true');
    }
    write('message', pendingMessages[command]);
    write('description', '');
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
      for (const element of commandButtons) element.removeAttribute('aria-disabled');
      if (restoreFocus && (trigger.hidden || trigger.disabled)) byId('message').focus();
      if (refreshPending) {
        refreshPending = false;
        update();
      }
    }
  }
}

for (const element of commandButtons) {
  element.addEventListener('click', () => {
    if (element.dataset.command && !working) update(element.dataset.command, element);
  });
}
const version = chrome.runtime.getManifest().version;
write('extensionVersion', 'v' + version);
byId('extensionVersion').hidden = false;
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && ('pair' in changes || 'available' in changes)) update();
});
update();
