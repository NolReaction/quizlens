'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const root = path.join(__dirname,'..');
const source = name => readFileSync(path.join(root,name),'utf8');

function background() {
  const popup = 'chrome-extension://quizlens/popup.html';
  const store = {};
  const disconnected = new Set(), activations = [];
  const documents = new Map();
  const listeners = {}, delivered = [], pages = new Map([
    [1, {id:1, active:true, url:'http://127.0.0.1:8765/'}],
    [2, {id:2, active:true, title:'Моя лекция', url:'https://learning.example/quiz'}],
    [3, {id:3, active:true, url:'https://other.example/form'}]
  ]);
  const documentId = id => documents.get(id) || 'doc'+id;
  const hook = name => ({addListener:fn => { listeners[name] = fn; }});
  const chrome = {
    runtime:{id:'quizlens',getURL:() => popup,getManifest:() => ({version:'test'}),onMessage:hook('message')},
    scripting:{executeScript:async ({target,files}) => [{
      result:files[0]==='receiver.js' ? target.tabId===1 : undefined,
      documentId:documentId(target.tabId)
    }]},
    storage:{session:{get:async () => structuredClone(store),
      set:async value => Object.assign(store,structuredClone(value)),remove:async key => { delete store[key]; }}},
    action:{setBadgeText:async()=>{},setTitle:async()=>{},setBadgeBackgroundColor:async()=>{}},
    windows:{update:async()=>{}},
    tabs:{get:async id=>pages.get(id),update:async id=>{ activations.push(id); return {windowId:5}; }, onUpdated:hook('update'),onRemoved:hook('remove'),
      sendMessage:async (id,msg,target) => {
        if (disconnected.has(id)) throw Error('Document closed');
        if (msg.type === 'identify') {
          assert.deepEqual({...target}, {frameId:0});
          return {instance:'instance-'+documentId(id)};
        }
        if (target.documentId !== documentId(id)) throw Error('Previous document closed');
        delivered.push({id,...msg}); return {ok:true};
      }}
  };
  vm.runInNewContext(source('browser-extension/background.js'),{chrome,URL,crypto:webcrypto});
  const post=(payload,sender) => new Promise(done => listeners.message(payload,sender,done));
  return {delivered,listeners,pages,documents,disconnected,activations,get pair(){return store.pair;},
    ui:(command,tabId) => post({command,tabId},{id:'quizlens',url:popup}),
    from:(command,tabId,key,extra={},senderDocument=documentId(tabId)) => post(
      {command,key,instance:'instance-'+senderDocument,...extra},
      {id:'quizlens',url:pages.get(tabId).url,tab:{id:tabId},frameId:0,documentId:senderDocument})};
}

test('extension can only bind a selected loopback QuizLens then track one chosen document',async()=>{
  const app=background();
  assert.equal((await app.ui('inspect',2)).command,null);
  assert.equal((await app.ui('bind',3)).ok,false);
  assert.equal((await app.ui('bind',1)).ok,true);
  // The receiving page cannot impersonate the extension popup to start capture elsewhere.
  await app.from('start',1,app.pair.receiver.key,{tabId:2});
  assert.equal(app.pair.source,null);
  assert.equal((await app.ui('start',2)).ok,true);
  const {key}=app.pair.source;
  const frame={texts:['Accuracy?','99%','1%'],selectionMode:'single'};
  await app.from('frame',3,key,{frame});
  await app.from('frame',2,'stale',{frame});
  await app.from('frame',2,key,{frame:{texts:['q','invalid'],selectionMode:'single'}});
  assert.equal(app.delivered.filter(x=>x.type==='frame').length,0);
  await app.from('frame',2,key,{frame});
  assert.deepEqual(app.delivered.filter(x=>x.type==='frame').map(x=>x.id),[1]);
  await app.from('receiver-stop',1,app.pair.receiver.key);
  await app.from('frame',2,key,{frame});
  assert.equal(app.pair.source,null);
  assert.equal(app.delivered.filter(x=>x.type==='frame').length,1);
  assert.equal((await app.ui('start',3)).ok,true);
  app.listeners.update(3,{status:'loading'});
  await app.ui('inspect',1);
  assert.equal(app.pair.source,null);
  assert.equal(app.pair.receiver.tabId,1);
  app.listeners.remove(1);
  await app.ui('inspect',3);
  assert.equal(app.pair.receiver,null);
});

test('capture reload restores the same live source and requests a fresh frame',async()=>{
  const app=background();
  await app.ui('bind',1);
  const oldReceiverKey=app.pair.receiver.key;
  app.listeners.update(1,{status:'loading'});
  app.documents.set(1,'idle-reload');
  const idle=await app.from('presence',1,undefined,{first:true});
  assert.equal(idle.paired,true);
  assert.equal(idle.reading,false);
  assert.notEqual(app.pair.receiver.key,oldReceiverKey);

  await app.ui('start',2);
  const {key}=app.pair.source;
  const frame={texts:['Accuracy?','99%','1%'],selectionMode:'single'};
  await app.from('frame',2,key,{frame});
  app.listeners.update(1,{status:'loading'});
  app.documents.set(1,'active-reload');
  app.disconnected.add(1);
  assert.equal((await app.from('frame',2,key,{frame})).ok,true);
  const recovering=await app.ui('inspect',2);
  assert.deepEqual([recovering.status,recovering.receiverConnected,recovering.receiverRecovering,recovering.reading],
    ['reconnecting',false,true,true]);
  assert.equal((await app.ui('start',3)).ok,false);
  app.disconnected.delete(1);

  const active=await app.from('presence',1,undefined,{first:true});
  assert.deepEqual([active.paired,active.reading,active.reason],[true,true,'waiting']);
  assert.equal(app.pair.source.key,key);
  assert.equal(app.pair.receiver.documentId,'active-reload');
  assert.equal(app.delivered.filter(x=>x.to==='reader' && x.type==='start' && x.id===2).length,2);
  assert.deepEqual(app.delivered.filter(x=>x.to==='receiver' && x.restored && x.id===1).map(x=>[x.type,x.version]),
    [['bound','test'],['bound','test'],['started','test']]);

  await app.from('frame',2,key,{frame});
  assert.equal((await app.ui('inspect',2)).status,'question');
  assert.equal((await app.from('presence',1,oldReceiverKey,{},'doc1')).paired,false);
  assert.equal(app.pair.receiver.documentId,'active-reload');
});

test('another origin or tab cannot restore a bound receiver',async()=>{
  const app=background();
  await app.ui('bind',1);
  app.pages.get(3).url='http://127.0.0.1:8765/';
  assert.equal((await app.from('presence',3,undefined,{first:true})).paired,false);
  assert.equal(app.pair.receiver.tabId,1);
  app.pages.get(1).url='http://127.0.0.1:9876/';
  app.documents.set(1,'different-local-server');
  assert.equal((await app.from('presence',1,undefined,{first:true})).paired,false);
  app.listeners.update(1,{status:'loading',url:app.pages.get(1).url});
  await app.ui('inspect',2);
  assert.equal(app.pair.receiver,null);
});

function capturePage(userAgent = 'Chrome') {
  const events = {}, elements = {
    '#tabReaderSetup':{dataset:{readingSwitchVersion:'1.11.1'}}
  }, timers = new Map(), intervals = new Map(), calls = [];
  let timerId = 0;
  const window = {
    addEventListener: (name, fn) => { events[name] = fn; },
    dispatchEvent: event => events[event.type]?.(event),
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { intervals.set(++timerId, fn); return timerId; },
    clearInterval(id) { intervals.delete(id); }
  };
  window.quizLensTabCapture = {
    start: () => calls.push('start'), read: (texts, mode) => calls.push([...texts, mode.selectionMode]),
    stop: () => calls.push('stop')
  };
  const document = {
    hidden: false,
    addEventListener: window.addEventListener,
    querySelector(selector) {
      return elements[selector] ||= {
        dataset: {}, addEventListener(name, fn) { this[name] = fn; }, focus() { this.focused = true; }
      };
    }
  };
  vm.runInNewContext(source('ui/tab-reader.js'), {window, document, location:{hostname:'127.0.0.1'}, navigator:{userAgent}, Event});
  const emit = data => events['quizlens-extension']({detail:JSON.stringify(data)});
  return {calls, elements, document, events, emit,
    presence(details = {}) { emit({type:'presence', version:'1.11.1', paired:false, reading:false, ...details}); },
    expire() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } },
    cycle() { for (const fn of intervals.values()) fn(); },
    get state() { return elements['#tabReaderStatus'].dataset.state; }
  };
}

test('legacy receiver keeps a live binding without probes and ignores frames after stop', () => {
  const app = capturePage();
  const frame = {texts:['Какой?', 'A', 'B'], selectionMode:'single'};
  app.emit({type:'frame', frame});
  app.emit({type:'bound'}); app.emit({type:'started'});
  app.cycle(); app.expire();
  app.emit({type:'frame', frame});
  app.emit({type:'frame', frame:{texts:[], reason:'waiting'}});
  app.elements['#stopTabReader'].click();
  app.emit({type:'frame', frame});
  app.emit({type:'stopped'});
  assert.equal(app.state, 'stopped');
  assert.deepEqual(app.calls, ['start',['Какой?', 'A', 'B', 'single'],[undefined],'stop']);
});

test('old extension versions expose an update hint without blocking the existing buttons', () => {
  const app = capturePage();
  app.presence({version:'1.11.0', paired:true});
  assert.equal(app.state, 'paired');
  assert.match(app.elements['#tabReaderHint'].textContent, /обнови расширение/);
  assert.match(app.elements['#tabReaderVersion'].textContent, /1\.11\.0/);
  assert.equal(app.elements['#tabReaderInstall'].hidden, false);
  app.presence({version:'1.11.1', paired:true});
  assert.match(app.elements['#tabReaderHint'].textContent, /включи «Чтение вкладки»/);
  assert.equal(app.elements['#tabReaderInstall'].hidden, true);
});

test('reloaded capture shows a restoring state until it receives a new frame', () => {
  const app = capturePage();
  app.emit({type:'bound', version:'1.11.1', restored:true});
  app.emit({type:'started', version:'1.11.1', restored:true, sourceTitle:'Квиз'});
  assert.equal(app.state, 'restoring');
  app.emit({type:'frame', frame:{texts:['Какой?', 'A', 'B'], selectionMode:'single'}});
  assert.equal(app.state, 'question');
  assert.deepEqual(app.calls, ['start',['Какой?', 'A', 'B', 'single']]);
  // The restored binding can arrive before the first presence response has been published.
  app.cycle();
  app.expire();
  assert.equal(app.state, 'disconnected');
  assert.equal(app.calls.at(-1), 'stop');
});

test('capture reports measured presence, detects disable, then recovers without starting OCR', () => {
  const app = capturePage();
  app.expire();
  assert.equal(app.state, 'undetected');
  app.presence();
  assert.equal(app.state, 'available');
  assert.equal(app.elements['#tabReaderInstall'].hidden, true);
  app.presence({paired:true});
  assert.equal(app.state, 'paired');
  app.emit({type:'started'});
  app.presence({paired:true, reading:true, reason:'partial', sourceTitle:'Квиз • CSV'});
  assert.equal(app.state, 'partial');
  assert.match(app.elements['#tabReaderHint'].textContent, /Квиз • CSV/);
  app.emit({type:'frame', frame:{texts:['Какой?', 'A', 'B'], selectionMode:'single'}});
  assert.equal(app.state, 'question');
  app.cycle(); app.expire();
  assert.equal(app.state, 'disconnected');
  assert.equal(app.elements['#stopTabReader'].hidden, true);
  app.emit({type:'frame', frame:{texts:['Лишний?', 'A', 'B'], selectionMode:'single'}});
  app.presence();
  assert.equal(app.state, 'available');
  assert.deepEqual(app.calls, ['start',['Какой?', 'A', 'B', 'single'],'stop']);
});

test('loss of the extension in a background capture stops a stale floating answer', () => {
  const app = capturePage();
  app.presence({paired:true});
  app.document.hidden = true;
  app.events.visibilitychange();
  app.emit({type:'started'});
  app.emit({type:'frame', frame:{texts:['Какой?', 'A', 'B'], selectionMode:'single'}});
  app.cycle(); app.expire();
  assert.equal(app.state, 'disconnected');
  assert.equal(app.calls.at(-1), 'stop');
});

test('a hidden capture tab rechecks on return and a single live snapshot never runs inference', () => {
  const app = capturePage('Mozilla/5.0 Version/18.0 Safari/605.1.15');
  app.expire();
  assert.equal(app.state, 'unsupported');
  app.presence({paired:true, reading:true, reason:'waiting'});
  assert.equal(app.state, 'waiting');
  assert.deepEqual(app.calls, []);
  app.document.hidden = true;
  app.events.visibilitychange();
  app.cycle(); app.expire();
  assert.equal(app.state, 'waiting');
  app.document.hidden = false;
  app.events.visibilitychange();
  app.expire();
  assert.equal(app.state, 'disconnected');
  assert.deepEqual(app.calls, []);
});

test('popup and verified capture report the delivered frame without persisting its texts',async()=>{
  const app = background();
  assert.equal((await app.ui('inspect',1)).status,'can-bind');
  assert.equal((await app.ui('inspect',2)).status,'need-connection');
  const idle = await app.from('presence',1,undefined,{first:true});
  assert.equal(idle.paired,false);
  assert.equal(idle.reading,false);
  assert.equal((await app.ui('inspect',2)).availableCaptureTabId,1);
  await app.ui('focus-capture',2);
  assert.deepEqual(app.activations,[1]);
  await app.ui('bind',1);
  assert.equal((await app.ui('inspect',1)).status,'bound');
  assert.equal((await app.ui('inspect',2)).status,'can-start');
  assert.equal((await app.ui('start',2)).status,'waiting');
  const {key} = app.pair.source;
  await app.from('frame',2,key,{frame:{texts:[],reason:'partial'}});
  assert.equal((await app.ui('inspect',1)).status,'partial');
  await app.from('frame',2,key,{frame:{texts:['private question','A','B'],selectionMode:'single'}});
  const read = await app.from('presence',1,app.pair.receiver.key);
  assert.equal(read.paired,true);
  assert.equal(read.reason,'question');
  assert.equal(read.sourceTitle,'Моя лекция');
  assert.equal((await app.ui('inspect',2)).status,'question');
  assert.equal((await app.ui('inspect',3)).status,'can-start');
  assert.ok(!JSON.stringify(app.pair).includes('private question'));
  assert.equal((await app.from('presence',1,'old-connection')).sourceTitle,'');
  assert.equal((await app.from('presence',3,undefined)).type,undefined);
  await app.ui('focus-source',1);
  assert.deepEqual(app.activations,[1,2]);
  app.disconnected.add(2);
  assert.equal((await app.ui('inspect',1)).status,'bound');
  app.disconnected.add(1);
  const lost = await app.ui('inspect',2);
  assert.equal(lost.status,'reconnecting');
  assert.equal(lost.receiverConnected,false);
  app.listeners.remove(1);
  const closed = await app.ui('inspect',2);
  assert.equal(closed.status,'need-connection');
  assert.equal(closed.availableCaptureTabId,null);
  assert.equal(app.pair.receiver,null);
});

test('unsupported internal pages cannot start a reading session',async()=>{
  const app = background();
  await app.ui('bind',1);
  app.pages.get(2).url='chrome://extensions';
  assert.equal((await app.ui('inspect',2)).status,'unsupported');
  assert.equal((await app.ui('start',2)).ok,false);
  assert.equal(app.pair.source,null);
});

test('tab extension requires a user gesture and requests no site-wide or network access',()=>{
  const manifest=JSON.parse(source('browser-extension/manifest.json'));
  assert.deepEqual(manifest.permissions.slice().sort(),['activeTab','scripting','storage']);
  assert.equal(manifest.host_permissions,undefined);
  assert.equal(manifest.content_scripts.length,1);
  const [discovery] = manifest.content_scripts;
  assert.deepEqual(discovery.js,['receiver.js']);
  assert.ok(!discovery.all_frames);
  for (const match of discovery.matches) {
    const url = new URL(match);
    assert.ok(['127.0.0.1','localhost','[::1]'].includes(url.hostname));
    assert.equal(url.protocol,'http:');
    assert.ok(['/', '/index.html'].includes(url.pathname));
  }
  assert.equal(manifest.externally_connectable,undefined);
  for (const name of ['reader.js','background.js','receiver.js']) {
    assert.doesNotMatch(source('browser-extension/'+name), /\bfetch\s*\(|XMLHttpRequest|WebSocket/);
  }
});
