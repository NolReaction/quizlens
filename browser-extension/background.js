'use strict';

const session = chrome.storage.session;
const popup = chrome.runtime.getURL('popup.html');
const version = chrome.runtime.getManifest().version;
const reconnectWindow = 30000;
const empty = () => ({receiver:null, source:null});
const save = pair => session.set({pair});
const localPage = url => {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname) &&
      ['/', '/index.html'].includes(u.pathname);
  } catch { return false; }
};
const sameCaptureOrigin = (receiver, url) => localPage(url) && new URL(url).origin === receiver.origin;
function readablePage(url) {
  try {
    const u = new URL(url);
    return ['http:', 'https:'].includes(u.protocol) && u.hostname !== 'chromewebstore.google.com' &&
      !(u.hostname === 'chrome.google.com' && u.pathname.startsWith('/webstore')) &&
      !(u.hostname === 'microsoftedge.microsoft.com' && u.pathname.startsWith('/addons'));
  } catch { return false; }
}
const sameDocument = (entry, sender) => entry && entry.tabId === sender.tab?.id &&
  entry.documentId === sender.documentId && sender.frameId === 0;
const matches = (entry, sender, key) => sameDocument(entry, sender) && entry.key === key;
async function send(side, to, type, extra = {}) {
  const ok = await chrome.tabs.sendMessage(side.tabId,
    {to, type, key:side.key, version, ...extra}, {documentId:side.documentId});
  if (!ok?.ok) throw Error('Расширение потеряло связь с выбранной страницей.');
}

const commands = {bind:'Подключить QuizLens', start:'Читать вопросы', stop:'Остановить чтение'};
const buttonMessages = {
  'can-bind':'Подключи эту вкладку QuizLens, чтобы принимать вопросы.',
  'need-connection':'Открой страницу «Захват» в QuizLens и нажми значок расширения.',
  bound:'QuizLens подключён. В квизе нажми на значок расширения и выбери «Читать вопросы».',
  reconnecting:'Восстанавливаю связь со страницей захвата QuizLens.',
  'can-start':'Вопрос и видимые варианты из этой вкладки появятся в QuizLens.',
  unsupported:'Для чтения открой обычную страницу с вопросами или выбери окно в QuizLens.',
  waiting:'Чтение включено. Жду вопрос с вариантами.',
  question:'Вопрос и варианты переданы в QuizLens.',
  partial:'Покажи весь вопрос и все варианты ответа.',
  multiple:'На странице видно несколько групп ответов. Оставь в кадре одно задание.'
};
function popupState(tab, pair, available, isCapture) {
  const receiverHere = pair.receiver?.tabId === tab.id;
  const sourceHere = pair.source?.tabId === tab.id;
  const receiverRecovering = Boolean(pair.receiver?.reconnectingUntil);
  const reason = pair.source ? pair.source.reason || 'waiting' : null;
  let status;
  let command = null;
  if (receiverRecovering && (receiverHere || !isCapture)) {
    status = 'reconnecting';
    command = pair.source ? 'stop' : null;
  } else if (pair.source && (receiverHere || sourceHere)) { status = reason; command = 'stop'; }
  else if (receiverHere) status = 'bound';
  else if (isCapture) { status = 'can-bind'; command = 'bind'; }
  else if (!readablePage(tab.url) || localPage(tab.url)) status = 'unsupported';
  else if (!pair.receiver) status = 'need-connection';
  else { status = 'can-start'; command = 'start'; }
  return {ok:true, command, label:commands[command], message:buttonMessages[status], status,
    here:receiverHere ? 'receiver' : sourceHere ? 'source' : isCapture ? 'capture' : 'other',
    receiverConnected:Boolean(pair.receiver) && !receiverRecovering, receiverRecovering, reading:Boolean(pair.source), reason,
    sourceTitle:pair.source?.title || '', sourceTabId:pair.source?.tabId ?? null,
    receiverTabId:pair.receiver?.tabId ?? null, availableCaptureTabId:available?.tabId ?? null};
}

const actionTitle = 'QuizLens · чтение вкладки';
function badge(side, mark, title = actionTitle) {
  if (!side) return;
  const tabId = side.tabId;
  // Tab removal can race badge updates; the tab's own badge disappears on close.
  chrome.action.setBadgeText({tabId, text:mark}).catch(() => {});
  chrome.action.setTitle({tabId, title}).catch(() => {});
  if (mark) chrome.action.setBadgeBackgroundColor({tabId, color:'#17674e'}).catch(() => {});
}
function showReading(pair) {
  const reason = pair.source.reason || 'waiting';
  const mark = {question:'✓', waiting:'…', partial:'!', multiple:'!'}[reason];
  for (const side of [pair.receiver, pair.source]) badge(side, mark, 'QuizLens · ' + buttonMessages[reason]);
}
async function stop(pair) {
  const source = pair.source;
  pair.source = null;
  await save(pair);
  if (source) {
    badge(source, '');
    await send(source, 'reader', 'stop').catch(() => {});
  }
  if (pair.receiver && !pair.receiver.reconnectingUntil) {
    badge(pair.receiver, '•', 'QuizLens · расширение подключено');
    await send(pair.receiver, 'receiver', 'stopped').catch(() => {});
  }
}
async function unbind(pair) {
  await stop(pair);
  if (pair.receiver) {
    badge(pair.receiver, '');
    await send(pair.receiver, 'receiver', 'unbound').catch(() => {});
  }
  pair.receiver = null;
  await save(pair);
}
async function markReconnecting(pair) {
  if (pair.receiver.reconnectingUntil) return;
  pair.receiver.reconnectingUntil = Date.now() + reconnectWindow;
  await save(pair);
  for (const side of [pair.receiver, pair.source]) badge(side, '…', 'QuizLens · ' + buttonMessages.reconnecting);
}
async function validatePair(pair) {
  if (pair.receiver) {
    try { await send(pair.receiver, 'receiver', 'ping'); }
    catch { await markReconnecting(pair); }
  }
  if (pair.source) {
    try { await send(pair.source, 'reader', 'ping'); }
    catch { await stop(pair); }
  }
}
async function currentReceiver(sender, instance) {
  if (typeof instance !== 'string' || !instance) return false;
  try {
    const current = await chrome.tabs.sendMessage(sender.tab.id, {to:'receiver', type:'identify'}, {frameId:0});
    return current?.instance === instance;
  } catch { return false; }
}
async function restoreReceiver(pair, sender) {
  const old = pair.receiver;
  pair.receiver = {tabId:old.tabId, origin:old.origin, documentId:sender.documentId, key:crypto.randomUUID()};
  try { await send(pair.receiver, 'receiver', 'bound', {restored:true}); }
  catch { pair.receiver = old; await markReconnecting(pair); return; }
  await save(pair);
  badge(pair.receiver, '•', 'QuizLens · расширение подключено');
  if (!pair.source) return;
  pair.source.reason = 'waiting';
  try {
    await send(pair.receiver, 'receiver', 'started', {sourceTitle:pair.source.title, restored:true});
    // Ask the old source document for a fresh frame, even if the question never changed.
    await send(pair.source, 'reader', 'start');
    await save(pair);
    showReading(pair);
  } catch { await stop(pair); }
}
async function captureDocument(tabId) {
  try {
    const [result] = await chrome.scripting.executeScript({target:{tabId}, files:['receiver.js']});
    return result?.result && result.documentId;
  } catch { return null; }
}
async function focus(side) {
  if (!side) throw Error('Вкладка закрыта. Открой QuizLens и подключи его снова.');
  const tab = await chrome.tabs.update(side.tabId, {active:true});
  await chrome.windows.update(tab.windowId, {focused:true});
}
async function forPopup(command, tabId, pair, available) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab.active) throw Error('Сначала открой нужную вкладку.');
  await validatePair(pair);
  if (available && !pair.receiver) {
    try { await send(available, 'receiver', 'available'); }
    catch { available = null; await session.remove('available'); }
  }
  if (command === 'focus-capture') await focus(pair.receiver || available);
  if (command === 'focus-source') await focus(pair.source);
  if (['inspect','focus-capture','focus-source'].includes(command)) {
    const isCapture = localPage(tab.url) && (tabId === pair.receiver?.tabId || tabId === available?.tabId || Boolean(await captureDocument(tabId)));
    return popupState(tab, pair, available, isCapture);
  }
  if (command === 'bind') {
    if (!localPage(tab.url)) throw Error('Открой локальную страницу «Захват» QuizLens.');
    const documentId = await captureDocument(tabId);
    if (!documentId) throw Error('В этой вкладке нет доступной страницы захвата QuizLens.');
    await unbind(pair);
    pair.receiver = {tabId, documentId, origin:new URL(tab.url).origin, key:crypto.randomUUID()};
    await save(pair);
    try { await send(pair.receiver, 'receiver', 'bound'); }
    catch (error) { await unbind(pair); throw error; }
    badge(pair.receiver, '•', 'QuizLens · расширение подключено');
  } else if (command === 'stop') {
    await stop(pair);
  } else if (command === 'start') {
    if (!pair.receiver || pair.receiver.reconnectingUntil || pair.receiver.tabId === tabId || !readablePage(tab.url) || localPage(tab.url)) {
      throw Error('Подключи QuizLens и выбери отдельную вкладку с квизом.');
    }
    let injection;
    try { [injection] = await chrome.scripting.executeScript({target:{tabId}, files:['reader.js']}); } catch { /* The browser may restrict this page. */ }
    if (!injection?.documentId) throw Error('Браузер закрыл доступ к этой вкладке. Для неё используй «Выбрать окно» в QuizLens.');
    if (pair.source) await stop(pair);
    const title = (tab.title || '').trim().slice(0,160);
    pair.source = {tabId, documentId:injection.documentId, key:crypto.randomUUID(), title, reason:'waiting'};
    await save(pair);
    try {
      await send(pair.receiver, 'receiver', 'started', {sourceTitle:title});
      await send(pair.source, 'reader', 'start');
    } catch {
      await stop(pair);
      throw Error('Связь прервалась. Проверь подключение на странице захвата.');
    }
    showReading(pair);
  } else throw Error('Вкладка уже изменилась. Открой расширение снова.');
  return popupState(tab, pair, available, false);
}

function validateFrame(frame) {
  if (!frame || !Array.isArray(frame.texts)) return false;
  if (!frame.texts.length) return ['waiting', 'multiple', 'partial'].includes(frame.reason);
  return frame.texts.length >= 3 && frame.texts.length <= 9 &&
    ['single','multiple'].includes(frame.selectionMode) &&
    frame.texts.every((v,i) => typeof v === 'string' && v.trim() && v.length <= (i ? 3000 : 6000));
}
async function handle(message, sender) {
  if (sender.id !== chrome.runtime.id || !message) return;
  const stored = await session.get(['pair','available']);
  const pair = stored.pair || empty();
  if (pair.receiver?.reconnectingUntil && Date.now() >= pair.receiver.reconnectingUntil) await unbind(pair);
  if (sender.url === popup) return forPopup(message.command, message.tabId, pair, stored.available);
  if (message.command === 'presence' && sender.frameId === 0 && sender.tab?.id != null && sender.documentId && localPage(sender.url)) {
    if (!stored.available || (message.first && !sameDocument(stored.available, sender))) {
      await session.set({available:{tabId:sender.tab.id, documentId:sender.documentId}});
    }
    const receiver = pair.receiver;
    let paired = Boolean(matches(receiver, sender, message.key));
    if (receiver?.tabId === sender.tab.id && sameCaptureOrigin(receiver, sender.url) &&
        (!sameDocument(receiver, sender) || (paired && receiver.reconnectingUntil)) &&
        await currentReceiver(sender, message.instance)) {
      await restoreReceiver(pair, sender);
      paired = !pair.receiver.reconnectingUntil;
    }
    const source = paired ? pair.source : null;
    return {ok:true, type:'presence', version,
      paired, reading:Boolean(source), sourceTitle:source?.title || '', reason:source ? source.reason || 'waiting' : null};
  }
  if (message.command === 'frame' && matches(pair.source, sender, message.key) && validateFrame(message.frame)) {
    if (pair.receiver.reconnectingUntil) return {ok:true};
    try { await send(pair.receiver, 'receiver', 'frame', {frame:message.frame}); }
    catch { await markReconnecting(pair); return {ok:true}; }
    const reason = message.frame.texts.length ? 'question' : message.frame.reason;
    if (reason !== pair.source.reason) {
      pair.source.reason = reason;
      await save(pair);
      showReading(pair);
    }
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
function resetTab(tabId, removed, change = {}) {
  enqueue(async () => {
    const stored = await session.get(['pair','available']);
    if (stored.available?.tabId === tabId && (removed || (change.url && !localPage(change.url)))) await session.remove('available');
    const pair = stored.pair || empty();
    if (pair.receiver?.tabId === tabId) {
      if (removed || (change.url && !sameCaptureOrigin(pair.receiver, change.url))) await unbind(pair);
    } else if (pair.source?.tabId === tabId && (removed || change.status === 'loading')) await stop(pair);
  });
}
chrome.tabs.onRemoved.addListener(tabId => resetTab(tabId, true));
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === 'loading' || change.url) resetTab(tabId, false, change);
});
