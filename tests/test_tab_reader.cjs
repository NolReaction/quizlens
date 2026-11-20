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
  let pair;
  const listeners = {}, delivered = [], pages = new Map([
    [1, {id:1, active:true, url:'http://127.0.0.1:8765/'}],
    [2, {id:2, active:true, url:'https://learning.example/quiz'}],
    [3, {id:3, active:true, url:'https://other.example/form'}]
  ]);
  const hook = name => ({addListener:fn => { listeners[name] = fn; }});
  const chrome = {
    runtime:{id:'quizlens',getURL:() => popup,onMessage:hook('message')},
    scripting:{executeScript:async ({target,files}) => [{
      result:files[0]==='receiver.js' ? target.tabId===1 : undefined,
      documentId:'doc'+target.tabId
    }]},
    storage:{session:{get:async () => ({pair:structuredClone(pair)}),
      set:async value => { pair=structuredClone(value.pair); }}},
    tabs:{get:async id=>pages.get(id), onUpdated:hook('update'),onRemoved:hook('remove'),
      sendMessage:async (id,msg,target) => {
        assert.equal(target.documentId,'doc'+id);
        delivered.push({id,...msg}); return {ok:true};
      }}
  };
  vm.runInNewContext(source('browser-extension/background.js'),{chrome,URL,crypto:webcrypto});
  const post=(payload,sender) => new Promise(done => listeners.message(payload,sender,done));
  return {delivered,listeners,get pair(){return pair;},
    ui:(command,tabId) => post({command,tabId},{id:'quizlens',url:popup}),
    from:(command,tabId,key,extra={}) => post({command,key,...extra},{id:'quizlens',url:pages.get(tabId).url,
      tab:{id:tabId},frameId:0,documentId:'doc'+tabId})};
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

test('paired receiver clears pending tab work and shows only valid active frames',()=>{
  const listeners={}, statuses={};
  const window={addEventListener:(name,fn)=>listeners[name]=fn,
    dispatchEvent:event=>listeners[event.type]?.(event)};
  const calls=[];
  window.quizLensTabCapture={start:()=>calls.push('start'),read:(texts,mode)=>calls.push([...texts,mode.selectionMode]),stop:()=>calls.push('stop')};
  const document={querySelector:selector=>statuses[selector] ||= {addEventListener(name,fn){this[name]=fn;}}};
  vm.runInNewContext(source('ui/tab-reader.js'),{window,document,location:{hostname:'127.0.0.1'},Event});
  const emit=(type,frame) => listeners['quizlens-extension']({detail:JSON.stringify({type,frame})});
  emit('frame',{texts:['Какой?','A','B'],selectionMode:'single'});
  emit('started'); emit('frame',{texts:['Какой?','A','B'],selectionMode:'single'});
  emit('frame',{texts:[],reason:'waiting'}); statuses['#stopTabReader'].click();
  emit('frame',{texts:['Чужой?','A','B'],selectionMode:'single'}); emit('stopped');
  assert.deepEqual(calls,['start',['Какой?','A','B','single'],[undefined],'stop']);
});

test('tab extension requires a user gesture and requests no site-wide or network access',()=>{
  const manifest=JSON.parse(source('browser-extension/manifest.json'));
  assert.deepEqual(manifest.permissions.slice().sort(),['activeTab','scripting','storage']);
  assert.equal(manifest.host_permissions,undefined);
  assert.equal(manifest.content_scripts,undefined);
  assert.equal(manifest.externally_connectable,undefined);
  for (const name of ['reader.js','background.js','receiver.js']) {
    assert.doesNotMatch(source('browser-extension/'+name), /\bfetch\s*\(|XMLHttpRequest|WebSocket/);
  }
});
