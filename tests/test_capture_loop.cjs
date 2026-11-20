'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../ui/app.js'), 'utf8');
const initial = ['Какая формула верна?', 'x²', 'x2', '10 %', '1 %'];
const blank = ['', '', '', '', ''];
const paint = () => new Uint8ClampedArray(352 * 48 * 5 * 4);

function fillRect(frame, x, y, w, h) {
  for (let row = y; row < y + h; row++) {
    for (let col = x; col < x + w; col++) {
      const p = (row * 352 + col) * 4;
      frame.fill(230, p, p + 3);
    }
  }
}

// Exercise the shipped tick/confirm/solve functions with a controlled camera,
// clock and local endpoints. Keep aborted requests deliverable to expose races.
async function makeSession() {
  const port = { now: 0, frame: paint(), texts: initial, ocr: [], detections: [], lookups: [], answers: [] };
  function request(path, payload) {
    return new Promise(resolve => {
      if (path === '/api/ocr') port.ocr.push({ texts: payload.texts, lectureIds: payload.lectureIds, resolve });
      else if (path === '/api/detect-layout') port.detections.push({payload,resolve});
      else if (path === '/api/solve') port.lookups.push({ ...payload, resolve });
      else throw new Error(path);
    });
  }
  const fakeElement = {
    addEventListener(name, fn) { this.listeners ??= {}; this.listeners[name] = fn; },
    getContext() {}, querySelector() { return this; }, play: async () => {}, close() { this.open=false; },
    replaceChildren() { this.children = []; },
    setAttribute(name,value) { (this.attributes ??= {})[name]=value; },
    append(...items) { (this.children ??= []).push(...items); }, add() {}, classList: { toggle() {} }, value: 'qwen',
  };
  const elements = new Map();
  const document = {
    getElementById(id) {
      if(!elements.has(id)) elements.set(id, Object.assign(Object.create(fakeElement), {style:{}}));
      return elements.get(id);
    },
    createElement: tagName => Object.assign(Object.create(fakeElement), {style:{},tagName}),
  };
  fakeElement.ownerDocument = document;
  // The capture loop needs only resolved video dimensions and its regular helpers.
  const context = vm.createContext({
    document, port, request, AbortController, performance: { now: () => port.now },
    localStorage: { getItem() { return null; } }, ResizeObserver: class { observe() {} },
    Option: class {}, window: { addEventListener() {}, clearInterval() {},setInterval() { return 1; } },
    navigator: {mediaDevices: {getDisplayMedia:async () => {
      const track={addEventListener(){},stop(){}};
      return {getTracks:()=>[track],getVideoTracks:()=>[track]};
    }}},
    fetch: async path => ({ ok: true, json: async () => path === '/api/models' ? { models: [] } : { lectures: [] } }),
  });
  vm.runInContext(readFileSync(join(__dirname, '../ui/token-usage.js'), 'utf8'), context);
  vm.runInContext(readFileSync(join(__dirname, '../ui/answer-proof.js'), 'utf8'), context);
  vm.runInContext(readFileSync(join(__dirname, '../ui/chart-assist.js'), 'utf8'), context);
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext(`
    preview.readyState=4;preview.videoWidth=800;preview.videoHeight=600;
    state.stream={getTracks:()=>[]};state.layoutMode='manual';state.regions=Array(5).fill({});state.selected=new Set(['lecture']);
    state.running=true;
    fingerprint=()=>port.frame;
    ocrImage=()=>({texts:[...port.texts]});
    wholeWindowImage=()=>'full-frame';
    postJson=request;
    answer=info=>{state.response=info;port.answers.push(info);};
    showTexts=()=>{};status=()=>{};
    globalThis.testApi={tick,differs,acceptTexts,clearWorkflow,start,stop,applySettingsChange,searchPixels,state,drawAnswer,tabCapture:window.quizLensTabCapture};
  `, context);
  const api = context.testApi;
  const flush = () => new Promise(resolve => setImmediate(resolve));
  async function step(ms, { hold = false } = {}) {
    port.now += ms;
    api.tick();
    if (!hold && port.ocr.length) {
      const job = port.ocr.shift();
      job.resolve({ texts: job.texts, ...port.hint });
      await flush();
    }
  }
  function read(texts, comparisonTexts, hint) { api.acceptTexts(texts, api.state.session, comparisonTexts, hint); }
  async function finish(job, choice) {
    job.resolve({ choice, source: 'Лекция', excerpt: 'Выдержка' });
    await flush();
  }
  return { port, api, step, read, finish, flush, node:id=>elements.get(id) };
}

test('a moving progress bar and timer cannot invalidate an OCR or lookup in flight', async () => {
  const { port, api, step, finish } = await makeSession();
  function frame(i, timer) {
    const next = paint();
    fillRect(next, 0, 2, 320 - i * 6, 3);
    if (timer) fillRect(next, 280 + i % 3 * 9, 10, 7, 11);
    return next;
  }
  assert.equal(api.differs(frame(1, false), frame(5, false)), false);
  assert.equal(api.differs(frame(1, true), frame(2, true)), true);
  for (let i = 0; i < 20; i++) {
    port.frame = frame(i, true);
    await step(260, { hold: i < 5 });
    if (i === 12) {
      assert.equal(port.lookups.length, 1);
      await finish(port.lookups[0], 2);
    }
  }
  assert.equal(port.lookups.length, 1);
  assert.equal(port.answers.at(-1).title, 'x2');
  const answered = port.answers.findIndex(item => item.tag);
  assert.deepEqual(port.answers.slice(answered).map(item => item.title), ['x2']);
});

test('a single incomplete OCR preserves the answer; two blanks remove it', async () => {
  const { port, read, finish } = await makeSession();
  read(initial); read(initial);
  await finish(port.lookups[0], 3);
  read(blank); read(initial);
  assert.equal(port.answers.at(-1).title, '10 %');
  assert.equal(port.lookups.length, 1);
  read(blank); read(blank);
  assert.equal(port.answers.at(-1).title, 'Жду все 4 варианта');
  read(initial); read(initial);
  assert.equal(port.answers.at(-1).title, '10 %');
  assert.equal(port.lookups.length, 1);
});

test('old response is withheld during a transition and stale deliveries cannot overwrite a new result', async () => {
  const { port, api, read, finish } = await makeSession();
  const changed = ['Какая формула не верна?', ...initial.slice(1).reverse()];
  read(initial); read(initial);
  read(changed);
  await finish(port.lookups[0], 1);
  assert.equal(port.answers.some(item => item.tag), false);
  read(changed);
  assert.equal(port.lookups[1].question, changed[0]);
  const changedAgain = [changed[0], '1 %', '11 %', 'x2', 'x²'];
  read(changedAgain); read(changedAgain);
  await finish(port.lookups[2], 2);
  await finish(port.lookups[1], 4);
  assert.equal(port.answers.at(-1).title, '11 %');
  api.clearWorkflow();
  assert.equal(api.state.currentKey, null);
});

test('no visual signal is required for small numeric or mathematical changes and option reordering', async () => {
  const { port, step, finish } = await makeSession();
  await step(260); await step(260);
  await finish(port.lookups[0], 1);
  for (const texts of [
    [initial[0], initial[1], initial[2], '11 %', '1 %'],
    [initial[0], initial[2], initial[1], '11 %', '1 %'],
    [initial[0], 'X2', initial[1], '11 %', '1 %'],
    ['Какая формула не верна?', 'X2', initial[1], '11 %', '1 %'],
  ]) {
    const prevCount = port.lookups.length;
    port.texts = texts;
    for (let i = 0; i < 9; i++) await step(260);
    assert.equal(port.lookups.length, prevCount + 1);
    await finish(port.lookups.at(-1), 1);
    assert.equal(port.answers.at(-1).title, texts[1]);
  }
});

test('corrected OCR percentages are solved separately; an unconfirmed previous reading is not reused', async () => {
  const { port, read, finish, flush } = await makeSession();
  const corrected = ['Какова конверсия при 8 покупках на 200 посетителей?', '2%', '4%', '8%', '16%'];
  const misread = [...corrected];
  misread[2] = '24%';
  read(misread); read(misread);
  port.lookups[0].resolve({ choice: null, choices: [], kind: 'retrieval', excerpt: '8 / 200 × 100 % = 4 %' });
  await flush();
  assert.equal(port.answers.at(-1).title, 'Нет подтверждения');
  read(misread); read(misread);
  assert.equal(port.lookups.length, 1, 'an unchanged image must not call the model repeatedly');
  read(corrected); read(corrected);
  await finish(port.lookups[1], 2);
  assert.equal(port.answers.at(-1).title, '4%');
  read(misread); read(misread);
  assert.equal(port.lookups.length, 3, 'the earlier uncertain result must be checked when it returns');
  read(corrected); read(corrected);
  assert.equal(port.lookups.length, 3);
  assert.equal(port.answers.at(-1).title, '4%');
  assert.equal(port.answers.at(-1).tokenUsage.source, 'cache');
});

test('continually changing OCR text eventually hides a stale answer', async () => {
  const { port, read, step, finish } = await makeSession();
  read(initial); read(initial);
  await finish(port.lookups[0], 1);
  for (let i = 0; i < 8; i++) {
    port.texts = ['Смена кадра ' + i, ...initial.slice(1)];
    read(port.texts);
    await step(260, { hold: true });
  }
  assert.equal(port.answers.at(-1).tag, undefined);
  assert.equal(port.lookups.length, 1);
});

test('a model percentage stays tentative until calculation verifies the result; only the proof is reused', async () => {
  const { port, read, flush, api, node } = await makeSession();
  const texts = ['Из 1000 клиентов ушли 10. Чему равна Accuracy при прогнозе «останется»?', '0%', '1%', '50%', '99%'];
  const equation = '990 / 1000 × 100 = 99%';
  const evidence = {source:'Лекция', excerpt:equation};
  const see = fields => { read(fields); read(fields); };
  see(texts);
  port.lookups[0].resolve({...evidence, kind:'model', choice:2});
  await flush();
  const tentative = port.answers.at(-1);
  assert.equal(tentative.tentative, true);
  api.drawAnswer(node('answer'), tentative);
  assert.ok(node('answer').children.some(child => child.textContent === 'Вариант модели · расчёт не проверен'));
  see(texts);
  assert.equal(port.lookups.length, 1);
  see(blank); see(texts);
  assert.equal(port.lookups.length, 2);
  port.lookups[1].resolve({...evidence, kind:'calculation', choice:4, verification:{kind:'calculation', equation}});
  await flush();
  see(blank); see(texts);
  assert.equal(port.lookups.length, 2);
  const replay = port.answers.at(-1);
  assert.equal(replay.title, '99%');
  assert.equal(replay.tokenUsage.source, 'cache');
  api.drawAnswer(node('answer'), replay);
  assert.ok(node('answer').children.some(child => child.textContent === 'Проверено расчётом · ' + equation));
});

test('an OCR card identifies its original wording without claiming an arithmetic check', async () => {
  const { port, read, flush } = await makeSession();
  const question = 'Какова конверсия при 8 покупках на 200 посетителей?';
  const fields = [question.replace('конверсия', 'конверсня'), '4%', '8%'];
  read(fields); read(fields);
  port.lookups[0].resolve({kind:'ocr_card', choice:1, matchedQuestion:question, source:'Карточка', excerpt:'4%'});
  await flush();
  const view = port.answers.at(-1);
  assert.equal(view.title, '4%');
  assert.equal(view.note, 'Вопрос в карточке · «' + question + '»');
});

test('lecture-supported OCR aliases share one key, while the chosen raw text is preserved', async () => {
  const { port, read, finish, step } = await makeSession();
  const latin = ['Для чего используют API?', 'a', 'b', 'c', 'd'];
  const cyrillic = ['Для чего используют АРІ?', ...latin.slice(1)];
  read(latin, latin);
  read(cyrillic, latin);
  assert.equal(port.lookups[0].question, cyrillic[0]);
  await finish(port.lookups[0], 2);
  read(latin, latin);
  assert.equal(port.lookups.length, 1);
  assert.equal(port.answers.at(-1).title, 'b');
  await step(2000, { hold: true });
  assert.deepEqual([...port.ocr[0].lectureIds], ['lecture']);
});

const grid = [
  {x:.08,y:.10,w:.84,h:.10},
  {x:.08,y:.25,w:.40,h:.15}, {x:.52,y:.25,w:.40,h:.15},
  {x:.08,y:.50,w:.40,h:.15}, {x:.52,y:.50,w:.40,h:.15},
];
const list = [grid[0], ...[.25,.39,.53,.67].map(y=>({x:.08,y,w:.84,h:.12}))];

function multiAnswer(choices, options) {
  return {choice:null,choices,evidence:choices.map(choice=>({
    choice,source:'Лекция, раздел '+choice,excerpt:options[choice-1]+' подтверждено',
  }))};
}

test('auto capture confirms mode changes even on identical OCR; choices follow their current positions', async()=>{
  const {port,node,step,finish,flush,api}=await makeSession();
  await node('share').listeners.click();
  async function detect(texts,selectionMode,expectedCount) {
    if(!port.detections.length) await step(3200);
    port.detections.shift().resolve({regions:grid,texts,selectionMode,expectedCount});await flush();
  }
  await detect(initial,'single',1);await detect(initial,'single',1);
  assert.equal(port.lookups[0].selectionMode,'single');
  await detect(initial,'multiple',2);
  await finish(port.lookups[0],2);
  assert.equal(port.answers.some(item=>item.tag),false);
  await detect(initial,'multiple',2);
  const job=port.lookups[1];
  assert.equal(job.selectionMode,'multiple');assert.equal(job.expectedCount,2);
  job.resolve(multiAnswer([3,1],job.options));await flush();
  const first=port.answers.at(-1);
  assert.equal(first.title,'Отметь 1 и 3');
  assert.deepEqual(Array.from(first.selections,s=>[s.choice,s.text,s.excerpt]),[
    [1,'x²','x² подтверждено'],[3,'10 %','10 % подтверждено'],
  ]);
  // Main view and always-on-top view both use drawAnswer with their own document.
  api.drawAnswer(node('answer'),first);
  const contents=node('answer').children;
  assert.deepEqual(contents.slice(0,3).map(item=>item.textContent),['Отметь 1 и 3','1. x²','3. 10 %']);
  assert.deepEqual(contents.at(-1).children.map(item=>item.textContent),
    ['Цитаты из документов','Вариант 1 · Лекция, раздел 1','x² подтверждено','Вариант 3 · Лекция, раздел 3','10 % подтверждено']);

  const reordered=[initial[0],...initial.slice(1).reverse()];
  await detect(reordered,'multiple',2);await detect(reordered,'multiple',2);
  const next=port.lookups[2];next.resolve(multiAnswer([2,4],next.options));await flush();
  assert.equal(port.answers.at(-1).title,'Отметь 2 и 4');
  await detect(initial,'single',1);await detect(initial,'single',1);
  assert.equal(port.answers.at(-1).title,'x2');
  assert.equal(port.lookups.length,3);
});

test('manual OCR passes the current count, rejects partial sets and lets the user resolve contradictory cues', async()=>{
  const {port,step,flush,read,node}=await makeSession();
  port.hint={selectionMode:'multiple',expectedCount:2};
  await step(300);await step(300);
  assert.equal(port.lookups.length,1);
  assert.equal(port.lookups[0].selectionMode,'multiple');assert.equal(port.lookups[0].expectedCount,2);
  port.lookups[0].resolve(multiAnswer([1],initial.slice(1)));await flush();
  assert.equal(port.answers.at(-1).title,'Нет подтверждения');
  const three={selectionMode:'multiple',expectedCount:3};
  read(initial,null,three);read(initial,null,three);
  assert.equal(port.lookups.length,2);assert.equal(port.lookups[1].expectedCount,3);
  const conflict={selectionMode:'unknown',expectedCount:null};
  read(initial,null,conflict);read(initial,null,conflict);
  assert.equal(port.answers.at(-1).title,'Уточни формат вопроса');assert.equal(port.lookups.length,2);
  port.lookups[1].resolve(multiAnswer([1,2,3],initial.slice(1)));await flush();
  assert.equal(port.answers.at(-1).title,'Уточни формат вопроса');

  port.hint=conflict;
  const control=node('selectionMode');control.value='multiple';control.listeners.change();
  await step(300);await step(300);
  assert.equal(port.lookups.length,3);
  assert.equal(port.lookups[2].selectionMode,'multiple');assert.equal(port.lookups[2].expectedCount,null);
  port.hint=three;control.value='single';control.listeners.change();
  await step(300);await step(300);
  assert.equal(port.lookups[3].selectionMode,'single');assert.equal(port.lookups[3].expectedCount,1);
  await node('share').listeners.click();
  assert.equal(control.value,'auto');
});

test('selecting a waiting tab automatically acquires a grid and follows a changed list', async () => {
  const { port,api,node,flush,step,finish }=await makeSession();
  await node('share').listeners.click();
  assert.equal(api.state.layoutMode,'auto');
  assert.equal(api.state.running,true);
  async function detection(regions,texts) {
    assert.equal(port.detections.length,1);
    const job=port.detections.shift();
    assert.equal(job.payload.image,'full-frame');
    assert.deepEqual([...job.payload.lectureIds],['lecture']);
    job.resolve({regions,texts});await flush();
  }
  await detection([],[]);
  for(let i=0;i<4;i++)await step(260);
  assert.equal(port.detections.length,0, 'idle lobby should not run OCR continuously');
  fillRect(port.frame=paint(),15,25,45,15);
  await step(260);
  await detection(grid,initial);
  await step(520);
  await detection(grid,initial);
  assert.equal(port.lookups.length,1);
  await finish(port.lookups[0],2);
  assert.equal(port.answers.at(-1).title,'x2');
  const next=['Какой ответ новый?', ...initial.slice(1).reverse()];
  port.frame=paint();
  await step(780);
  await detection(list,next);
  await step(520);
  await detection(list,next);
  assert.equal(port.lookups.length,2);
  assert.equal(api.state.regions[3].w,.84);
  await finish(port.lookups[1],4);
  assert.equal(port.answers.at(-1).title,next[4]);
  assert.equal(port.ocr.length,0);
  // A stopped stream must suppress a late detection and stay paused on a resize.
  await step(3200);
  node('run').listeners.click();
  await detection(grid,initial);
  node('preview').listeners.resize();
  await step(3200);
  assert.equal(port.detections.length,0);
  assert.equal(api.state.running,false);
  assert.equal(port.answers.at(-1).tag,undefined);
});

test('auto capture armed before lecture selection can be stopped while waiting', async()=>{
  const {api,node,port}=await makeSession();
  api.state.selected.clear();
  await node('share').listeners.click();
  assert.equal(api.state.autoArmed,true);
  assert.equal(api.state.running,false);
  node('run').listeners.click();
  api.state.selected.add('lecture');
  api.applySettingsChange();
  assert.equal(port.detections.length,0);
  assert.equal(api.state.running,false);
  node('run').listeners.click();
  assert.equal(api.state.running,true);
  assert.equal(port.detections.length,1);
});

test('auto starts when a lecture arrives after the window is shared', async()=>{
  const {api,node,port}=await makeSession();
  api.state.selected.clear();
  await node('share').listeners.click();
  api.state.selected.add('lecture');
  api.applySettingsChange();
  assert.equal(port.detections.length,1);
  assert.equal(api.state.running,true);
});

test('one focus box crops the search, maps detector coordinates back, and resets on aspect change',async()=>{
  const {api,node,port,flush}=await makeSession();
  await node('share').listeners.click();
  const older=port.detections.shift();
  api.stop();
  api.state.snapshotAspect=800/600;
  api.state.draftRegions=[{x:.1,y:.25,w:.5,h:.5}];
  node('focusSearch').listeners.click();
  assert.equal(api.state.layoutMode,'auto');
  assert.deepEqual([...api.searchPixels()],[80,150,400,300]);
  const first=port.detections.shift();
  first.resolve({regions:grid,texts:initial});await flush();
  assert.equal(api.state.regions.length,5);
  assert.equal(api.state.regions[1].x,.1+grid[1].x*.5);
  assert.equal(api.state.regions[1].h,grid[1].h*.5);
  // A late whole-window reply cannot replace the transformed regions.
  older.resolve({regions:list,texts:initial});await flush();
  assert.equal(api.state.regions[1].w,grid[1].w*.5);
  assert.equal(node('autoLayout').hidden,false);
  node('preview').videoWidth=1200;node('preview').listeners.resize();
  assert.equal(api.state.autoFocus,null);
  assert.deepEqual([...api.searchPixels()],[0,0,1200,600]);
  assert.equal(api.state.regions.length,0);
  assert.equal(api.state.running,true);
});

test('a new chosen source clears a one-box focus and manual areas',async()=>{
  const {api,node}=await makeSession();
  api.state.autoFocus={x:.1,y:.1,w:.6,h:.6};
  await node('share').listeners.click();
  assert.equal(api.state.autoFocus,null);
  assert.equal(api.state.layoutMode,'auto');
  assert.equal(api.state.regions.length,0);
  assert.equal(api.state.running,true);
});

test('two-, five- and eight-answer frames keep order, sources and record the same sighting once', async () => {
  const {port,node,api,step,flush}=await makeSession();
  await node('share').listeners.click();
  const question='Какие варианты подходят?';
  async function twice(total, answers) {
    const texts=[question,...Array.from({length:total},(_,i)=>'Текст '+(i+1))];
    const regions=texts.map((_,i)=>({x:.08,y:.03+i*.1,w:.84,h:.08}));
    for(let i=0;i<2;i++) {
      if(!port.detections.length) await step(3200);
      port.detections.shift().resolve({regions,texts,selectionMode:answers.length===1?'single':'multiple',expectedCount:answers.length});
      await flush();
    }
    const job=port.lookups.at(-1);
    assert.equal(job.channel,'browser');
    assert.equal(job.record,true);
    assert.ok(job.captureMs>0);
    assert.equal(job.options.length,total);
    assert.equal(api.state.regions.length,total+1);
    const result=multiAnswer(answers,job.options);
    if(answers.length===1) Object.assign(result,{choice:answers[0],source:'Лекция',excerpt:'Текст'});
    job.resolve({...result,journalId:'entry'+total});await flush();
    assert.equal(port.answers.at(-1).journalId,'entry'+total);
    return texts;
  }
  await twice(2,[2]);
  await twice(5,[1,5]);
  const eight=await twice(8,[6,7,8]);
  assert.deepEqual(Array.from(port.answers.at(-1).selections,item=>item.text),eight.slice(6));
  assert.equal(port.lookups.length,3);
  api.drawAnswer(node('answer'),port.answers.at(-1));
  assert.equal(node('answer').children.at(-1).href,'/journal#entry8');
});

test('partial auto frame reports the actual scroll cue and does not queue a guess', async () => {
  const {port,node,step,flush}=await makeSession();
  await node('share').listeners.click();
  port.detections.shift().resolve({regions:[],texts:[],captureStatus:'partial',hint:'Прокрути список ответов.'});
  await flush();
  assert.equal(port.answers.at(-1).title,'Прокрути список ответов.');
  await step(3200);
  port.detections.shift().resolve({regions:[],texts:[]});
  await flush();
  assert.equal(port.answers.at(-1).title,'Ожидаю вопрос и варианты');
  assert.equal(port.lookups.length,0);
});

test('journal trace preserves distinct recent recognized texts, and respects disabling recording', async () => {
  const {port,read,node,flush}=await makeSession();
  const expected=['Какая метрика верна?', 'Recall','Precision'];
  const partial=[expected[0],expected[1],''];
  read(partial);port.now=300;read(expected);port.now=610;read(expected);
  assert.equal(port.lookups.length,1);
  const job=port.lookups[0];
  assert.equal(job.captureMs,310);
  assert.deepEqual(Array.from(job.captureTrace,row=>[row.status,row.ageMs,row.question,...row.options]),[
    ['partial',610,...partial], ['complete',310,...expected]
  ]);
  job.resolve({choice:1,source:'Лекция',excerpt:'Recall'});await flush();
  node('record').checked=false;
  port.now=1000;
  read(['Другой вопрос?',...expected.slice(1)]);port.now=1200;read(['Другой вопрос?',...expected.slice(1)]);
  assert.equal(port.lookups[1].record,false);
  assert.equal('captureTrace' in port.lookups[1],false);
});

test('uncertain OCR never uses a previous lookup or cached answer until two fresh clean observations', async()=>{
  const {port,api,node,read,finish}=await makeSession();
  const texts=['Как изменилась доля?', '0,5%', '5%', '10%'];
  read(texts);read(texts);
  const previous=port.lookups[0];
  const image='data:image/png;base64,QQ==';
  const questionable={readingWarnings:[{field:1,readings:['0,5%','5%'],image}]};
  read(texts,null,questionable);
  assert.equal(port.answers.at(-1).title,'Сверь распознанный текст');
  await finish(previous,1);
  assert.equal(port.answers.at(-1).tag,undefined);
  api.drawAnswer(node('answer'),port.answers.at(-1));
  const warning=node('answer').children.find(c=>c.attributes?.['data-reading-warning']==='1');
  assert.match(warning.children[0].textContent,/Вариант 1.*0,5%.*5%/);
  assert.equal(warning.children[1].src,image);
  read(texts,null,questionable);read(texts);
  assert.equal(port.lookups.length,1);
  read(texts);
  assert.equal(port.lookups.length,2);
  await finish(port.lookups[1],1);
  read(texts,null,questionable);read(texts,null,questionable);
  assert.equal(port.answers.at(-1).title,'Сверь распознанный текст','ambiguity cannot reuse a cached answer');
  read(texts);read(texts);
  assert.equal(port.answers.at(-1).title,'0,5%');
  assert.equal(port.answers.at(-1).tokenUsage.source,'cache');
});

test('a changing comma or negation withdraws the previous answer immediately and never combines rows', async()=>{
  const {port,read,finish}=await makeSession();
  const first=['Какое утверждение верно?', '0,5%','7%'];
  const second=[first[0], '5%',first[2]];
  const third=['Какое утверждение не верно?', ...second.slice(1)];
  read(first);read(first);await finish(port.lookups[0],1);
  read(second);
  assert.equal(port.answers.at(-1).readingWarnings[0].field,1);
  assert.equal(port.answers.at(-1).tag,undefined);
  read(first);read(second);
  assert.equal(port.lookups.length,1);
  read(second);assert.equal(port.lookups.length,2);
  read(third);
  assert.equal(port.answers.at(-1).readingWarnings[0].field,0);
  await finish(port.lookups[1],1);
  assert.equal(port.answers.at(-1).tag,undefined);
  read(third);
  assert.deepEqual([port.lookups[2].question,...port.lookups[2].options],third);
});

test('direct tab snapshots solve immediately and later OCR or tab jobs cannot replace them', async()=>{
  const {port,api,read,finish}=await makeSession();
  read(initial);read(initial);const prior=port.lookups[0];
  const tab=api.tabCapture;
  tab.start();
  assert.equal(api.state.stream,null);
  tab.read(['Какой формат?','JSON','TXT'],{selectionMode:'single'});
  assert.equal(port.lookups.length,2);
  await finish(prior,1);
  assert.equal(port.answers.at(-1).tag,undefined);
  await finish(port.lookups[1],1);
  assert.equal(port.answers.at(-1).title,'JSON');
  tab.read(['Какой формат?','JSON','TXT'],{selectionMode:'single'});
  assert.equal(port.lookups.length,2);
  tab.read(['Какой формат?','TXT','JSON'],{selectionMode:'single'});
  assert.equal(port.lookups.length,3);
  tab.read([]);await finish(port.lookups[2],2);
  assert.equal(port.answers.at(-1).tag,undefined);
  tab.read(['Что выбрать?','А','Б'],{selectionMode:'multiple'});
  assert.equal(port.lookups.at(-1).selectionMode,'multiple');
  tab.stop();await finish(port.lookups.at(-1),2);
  assert.equal(api.state.running,false);
  assert.equal(port.answers.at(-1).tag,undefined);
  assert.equal(api.state.tabFrame,null);
});

test('spacing around percent keeps one stable reading while the current display text is preserved',async()=>{
  const {port,read,finish}=await makeSession();
  const texts=['Какова доля?', '0,5%', '5%', '10%'];
  const spaced=[texts[0],'0,5 %','5 %','10 %'];
  read(texts);read(spaced);
  assert.equal(port.lookups.length,1);
  assert.equal(port.lookups[0].options[0],'0,5 %');
  await finish(port.lookups[0],1);
  read(texts);read(spaced);
  assert.equal(port.lookups.length,1);
  assert.equal(port.answers.at(-1).tag,'Вариант 1');
});


test('a chart-dependent question offers its image; a subsequent text question resumes ordinary lookup', async () => {
  const {port, read, node} = await makeSession();
  const chart = ['Какое значение показано на графике?', '5', '15', '25'];
  read(chart); read(chart);
  assert.equal(port.lookups.length, 0);
  assert.equal(node('visualPrompt').hidden, false);
  assert.match(node('visual').textContent, /рисунок/);
  read(initial); read(initial);
  assert.equal(port.lookups.length, 1);
  assert.equal(node('visualPrompt').hidden, true);
});
