'use strict';

const session = chrome.storage.session;
const popup = chrome.runtime.getURL('popup.html');
const empty = () => ({receiver:null, source:null});
const getPair = async () => (await session.get('pair')).pair || empty();
const save = pair => session.set({pair});
const localPage = url => {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname) &&
      ['/', '/index.html'].includes(u.pathname);
  } catch { return false; }
};
const matches = (entry, sender, key) => entry && entry.key === key && entry.tabId === sender.tab?.id &&
  entry.documentId === sender.documentId && sender.frameId === 0;
async function send(side, to, type, extra = {}) {
  const ok = await chrome.tabs.sendMessage(side.tabId,
    {to, type, key:side.key, ...extra}, {documentId:side.documentId});
  if (!ok?.ok) throw Error('Расширение потеряло связь с выбранной страницей.');
}
const states = {
  bind: ['Подключить QuizLens', 'Подключи этот экран захвата. Затем открой вкладку с квизом и снова нажми значок расширения.'],
  start: ['Читать вопросы', 'Вопрос и видимые варианты из этой вкладки появятся в подключённом QuizLens.'],
  stop: ['Остановить чтение', 'Текст из вкладки квиза передаётся в QuizLens.']
};
function display(command, message) {
  return {ok:true, command, label:states[command]?.[0], message:message || states[command]?.[1]};
}

async function stop(pair) {
  const source = pair.source;
  pair.source = null;
  await save(pair);
  if (source) await send(source, 'reader', 'stop').catch(() => {});
  if (pair.receiver) await send(pair.receiver, 'receiver', 'stopped').catch(() => {});
}
async function unbind(pair) {
  await stop(pair);
  if (pair.receiver) await send(pair.receiver, 'receiver', 'unbound').catch(() => {});
  pair.receiver = null;
  await save(pair);
}

async function forPopup(command, tabId, pair) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.active) throw Error('Сначала открой нужную вкладку.');
  if (command === 'inspect') {
    if (pair.source && [pair.receiver?.tabId, pair.source.tabId].includes(tabId)) return display('stop');
    if (pair.receiver?.tabId === tabId) return display(null, 'QuizLens подключён. Открой вкладку с квизом и нажми значок расширения.');
    if (localPage(tab.url)) return display('bind');
    if (!pair.receiver) return display(null, 'Открой страницу «Захват» в локальном QuizLens и нажми значок расширения для подключения.');
    if (!/^https?:\/\//.test(tab.url || '')) return display(null, 'Перейди на страницу квиза с адресом http или https.');
    return display('start');
  }
  if (command === 'bind') {
    if (!localPage(tab.url)) throw Error('Открой локальную страницу «Захват» QuizLens.');
    const [injection] = await chrome.scripting.executeScript({target:{tabId}, files:['receiver.js']});
    if (!injection.result || !injection.documentId) throw Error('В этой вкладке не найдена страница захвата QuizLens с поддержкой расширения.');
    await unbind(pair);
    pair.receiver = {tabId, documentId:injection.documentId, key:crypto.randomUUID()};
    await save(pair);
    await send(pair.receiver, 'receiver', 'bound');
    return display(null, 'QuizLens подключён. Перейди в квиз, нажми значок QuizLens и выбери «Читать вопросы».');
  }
  if (command === 'stop' && [pair.receiver?.tabId, pair.source?.tabId].includes(tabId)) {
    await stop(pair);
    return display(null, 'Чтение остановлено. Можно выбрать другую вкладку.');
  }
  if (command === 'start') {
    if (!pair.receiver || pair.receiver.tabId === tabId || !/^https?:\/\//.test(tab.url || '')) {
      throw Error('Подключи QuizLens и выбери отдельную вкладку с квизом.');
    }
    const [injection] = await chrome.scripting.executeScript({target:{tabId}, files:['reader.js']});
    if (!injection.documentId) throw Error('Браузер не дал доступ к странице. Используй «Выбрать окно» в QuizLens.');
    if (pair.source) await stop(pair);
    pair.source = {tabId, documentId:injection.documentId, key:crypto.randomUUID()};
    await save(pair);
    try {
      await send(pair.receiver, 'receiver', 'started');
      await send(pair.source, 'reader', 'start');
    } catch {
      await unbind(pair);
      throw Error('Связь прервалась. Открой расширение на странице захвата и подключи QuizLens заново.');
    }
    return display('stop');
  }
  throw Error('Вкладка уже изменилась. Открой расширение снова.');
}

function validateFrame(frame) {
  if (!frame || !Array.isArray(frame.texts)) return false;
  if (!frame.texts.length) return ['waiting', 'multiple', 'partial'].includes(frame.reason);
  return frame.texts.length >= 3 && frame.texts.length <= 9 &&
    ['single','multiple'].includes(frame.selectionMode) &&
    frame.texts.every((v,i) => typeof v === 'string' && v.trim() && v.length <= (i ? 3000 : 6000));
}
async function handle(message, sender) {
  const pair = await getPair();
  if (sender.id !== chrome.runtime.id || !message) return;
  if (sender.url === popup) return forPopup(message.command, message.tabId, pair);
  if (message.command === 'frame' && matches(pair.source, sender, message.key) && validateFrame(message.frame)) {
    try { await send(pair.receiver, 'receiver', 'frame', {frame:message.frame}); }
    catch { await unbind(pair); }
  } else if (message.command === 'receiver-stop' && matches(pair.receiver, sender, message.key)) {
    await stop(pair);
  }
  return {ok:true};
}

// Serialize pair changes and incoming frames; a former document can never publish into its replacement.
let pending = Promise.resolve();
function enqueue(work) {
  const next = pending.then(work);
  pending = next.catch(() => {});
  return next;
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  enqueue(() => handle(message,sender)).then(reply, error => reply({ok:false,error:error.message}));
  return true;
});
function resetTab(tabId) {
  enqueue(async () => {
    const pair = await getPair();
    if (pair.receiver?.tabId === tabId) await unbind(pair);
    else if (pair.source?.tabId === tabId) await stop(pair);
  });
}
chrome.tabs.onRemoved.addListener(resetTab);
chrome.tabs.onUpdated.addListener((tabId, change) => { if (change.status === 'loading') resetTab(tabId); });
