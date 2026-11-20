'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../browser-extension/popup.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function popup(send) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      dataset:{}, textContent:'', disabled:false, hidden:false,
      toggleAttribute() {}, setAttribute() {}, removeAttribute() {},
      addEventListener(name, callback) { this[name] = callback; },
      querySelectorAll:() => ['action','focusCapture','focusSource'].map(element),
    });
    return elements.get(id);
  }
  let storageEvent;
  const document = {getElementById:element, body:element('body')};
  const chrome = {
    tabs:{query: async () => [{id:12, title:'Моя вкладка с квизом'}]},
    runtime:{sendMessage: ({command,tabId}) => {
      assert.equal(tabId,12);
      return send(command);
    }},
    storage:{onChanged:{addListener: listener => { storageEvent = listener; }}},
  };
  vm.runInNewContext(script, {document, chrome, navigator:{platform:"Win32"}, window:{close(){}}});
  return {element, changed:(key = 'pair', area = 'session') => storageEvent({[key]:{}}, area)};
}

const ready = {ok:true, status:'can-start', command:'start', label:'Читать вопросы',
  here:'other', receiverConnected:true, reading:false};
const waiting = {...ready, status:'waiting', command:'stop', label:'Остановить чтение',
  here:'source', sourceTitle:'Метрики', reading:true};

test('a late inspection cannot revert a successful start or the delivery notification', async () => {
  let completeInspection;
  let inspectCount = 0;
  const ui = popup(command => {
    if (command === 'start') return waiting;
    if (++inspectCount === 1) return ready;
    if (inspectCount === 2) return new Promise(resolve => { completeInspection = resolve; });
    return {...waiting, status:'question'};
  });
  const {element} = ui;
  await settle();
  assert.equal(element('action').dataset.command,'start');
  ui.changed();
  element('action').click();
  await settle();
  assert.equal(element('body').dataset.state,'waiting');
  assert.equal(element('focusCapture').hidden,false);
  completeInspection(ready);
  await settle();
  assert.equal(element('body').dataset.state,'waiting');
  ui.changed();
  await settle();
  assert.equal(element('body').dataset.state,'question');
  assert.equal(element('sourceTitle').textContent,'Метрики');
  assert.equal(element('action').dataset.command,'stop');
});

test('a failed connection offers a retry and clears stale routing controls', async () => {
  let available = true;
  const ui = popup(command => {
    if (command === 'bind') return {ok:false, error:'Вкладка захвата закрыта.'};
    if (available) return {...ready, status:'can-bind', here:'capture', receiverConnected:false, command:'bind', label:'Подключить QuizLens'};
    return {...ready, status:'need-connection', here:'other', command:null, receiverConnected:false};
  });
  const {element} = ui;
  await settle();
  element('action').click();
  await settle();
  assert.equal(element('body').dataset.state,'error');
  assert.equal(element('description').textContent,'Вкладка захвата закрыта.');
  assert.equal(element('connection').hidden,true);
  assert.equal(element('action').dataset.command,'inspect');
  available = false;
  element('action').click();
  await settle();
  assert.equal(element('body').dataset.state,'need-connection');
  assert.equal(element('focusCapture').hidden,true);
  assert.equal(element('action').hidden,true);
});

test('opening or closing an unpaired capture tab updates the popup while it stays open', async () => {
  let availableCaptureTabId = null;
  let inspections = 0;
  const ui = popup(() => {
    inspections++;
    return {ok:true, command:null, status:'need-connection', here:'other', reading:false, receiverConnected:false, availableCaptureTabId};
  });
  const button = ui.element('focusCapture');
  await settle();
  assert.match(ui.element('description').textContent, /Start\.cmd/);
  assert.equal(button.hidden,true);
  availableCaptureTabId = 19;
  ui.changed('available', 'local');
  await settle();
  assert.equal(inspections,1);
  ui.changed('available');
  await settle();
  assert.equal(button.hidden,false);
  assert.equal(button.dataset.command,'focus-capture');
  availableCaptureTabId = null;
  ui.changed('available');
  await settle();
  assert.equal(button.hidden,true);
});
