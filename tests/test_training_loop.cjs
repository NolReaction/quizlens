'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../ui/train.js'), 'utf8');

class Element {
  constructor(tag = 'div', text = '', value = '') {
    this.tag = tag; this.textContent = text; this.value = value; this.children = []; this.checked = false;
  }
  append(...els) { this.children.push(...els); }
  add(el) { this.append(el); if (this.children.length === 1) this.value = el.value; }
  replaceChildren(...els) { this.children = [...els]; if (els[0]?.tag === 'option') this.value = els[0].value; }
  addEventListener(event, cb) { this['on' + event] = cb; }
  setAttribute(name, value) { this[name] = value; }
  focus() { this.focused = true; }
  querySelectorAll(selector) {
    return this.children.flatMap(el => typeof el === 'string' ? [] :
      [...(el.tag === 'input' && (selector !== 'input:checked' || el.checked) ? [el] : []),
       ...el.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  text() { return this.textContent + ' ' + this.children.map(el => typeof el === 'string' ? el : el.text()).join(' '); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const card = {topic:'Метрики', options:['50 %','75 %'], mode:'single', source:'Пример · 1', excerpt:'Правило вычисления точности.',
  variants:[{value:'2', question:'Один правильный прогноз из 2. Какова точность?', correct:[1], reason:'1/2 = 50 %.'},
    {value:'4', question:'Три правильных прогноза из 4. Какова точность?', correct:[2], reason:'3/4 = 75 %.'}]};

async function harness({cards=[card], response, noModel=false, saved={}, hash='', modelWait}={}) {
  const elements = {}, storage = new Map(Object.entries(saved)), calls = [], jobs = new Map();
  let clock=0, last=0;
  const get = id => elements[id] ||= new Element();
  get('material').add(new Element('option','Метрики','builtin'));
  const schedule = (fn, duration) => {jobs.set(++last,{fn,duration});return last;};
  const api = async (url, init) => {
    const payload = init?.body && JSON.parse(init.body);
    calls.push([url,payload]);
    const known = {'/api/train':{lectureName:'lecture.txt',cards},
      '/api/lectures':{lectures:[{name:'lecture.txt',id:'one'},{name:'two.md',id:'two'}]},
      '/api/models':{models:noModel ? [] : ['local-Qwen']},'/api/train/generate':{cards}};
    let body = known[url];
    if (url === '/api/models' && modelWait) await modelWait;
    if (url === '/api/solve') body = await response(payload, ms => clock += ms);
    return {ok:true,json:async()=>body};
  };
  const ctx = vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag)},
    Option:class extends Element {constructor(text,value){super('option',text,value);}},fetch:api,
    location:{hash},
    performance:{now:()=>clock},setInterval:schedule,setTimeout:schedule,
    clearTimeout:n=>jobs.delete(n),clearInterval:n=>jobs.delete(n),AbortController,
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}});
  vm.runInContext(script,ctx);
  await tick();
  return {get,ctx,calls,storage,setTime:v=>clock=v,fire:delay=>Array.from(jobs.values()).find(j=>j.duration===delay)?.fn()};
}

test('manual practice works without model; contrast uses same order, shows both explanations and scores once', async () => {
  const h=await harness({noModel:true});
  assert.equal(h.get('practice').disabled,false);
  assert.equal(h.get('bench').disabled,true);
  h.get('practice').onclick();
  const choices = () => h.get('answers').querySelectorAll('input');
  const expected = () => Number(vm.runInContext('expectedPositions(session)[0]',h.ctx));
  choices()[expected()-1].checked=true;
  h.get('submit').onclick();
  h.get('tryChange').onclick();
  choices()[expected()-1].checked=true;
  h.get('submit').onclick();
  assert.match(h.get('feedback').text(),/До изменения/);
  assert.match(h.get('feedback').text(),/После изменения/);
  assert.match(h.get('feedback').text(),/1\/2 = 50 %/);
  assert.match(h.get('feedback').text(),/3\/4 = 75 %/);
  h.get('next').onclick();
  assert.match(h.get('score').text(),/1 из 1/);
  assert.equal(h.calls.filter(([url])=>url==='/api/solve').length,0);
});

test('model benchmark sends no scoring keys, counts full answer before deadline, and retains late results', async () => {
  let n=0;
  const h=await harness({cards:[card,card],response:(req,advance)=>{
    n++;
    assert.deepEqual(Object.keys(req).sort(),['channel','expectedCount','lectureIds','model','options','question','record','selectionMode']);
    assert.deepEqual(req.lectureIds,['one']);assert.equal(req.channel,'training');
    const variant=card.variants.find(v=>v.question===req.question);
    advance(n===1?15001:14999);
    return {choices:[req.options.indexOf(card.options[variant.correct[0]-1])+1]};
  }});
  h.get('bench').onclick();await tick();h.fire(650);await tick();h.fire(650);
  assert.match(h.get('score').text(),/1 из 2/);
  assert.match(h.get('summary').text(),/после таймера/);
  assert.equal(n,2);
});

test('timer rejects manual mark on late click; previous async answer dropped after stop', async () => {
  let complete;
  const h=await harness({response:()=>new Promise(resolve=>{complete=resolve;})});
  h.get('practice').onclick();
  const i=vm.runInContext('expectedPositions(session)[0]',h.ctx);
  h.get('answers').querySelectorAll('input')[i-1].checked=true;
  h.setTime(15100);h.get('submit').onclick();h.get('next').onclick();
  assert.match(h.get('score').text(),/0 из 1/);
  h.get('restart').onclick();h.get('bench').onclick();
  h.get('stop').onclick();complete({choices:[1]});await tick();
  assert.match(h.get('score').text(),/до первого ответа/);
  assert.equal(h.get('round').hidden,true);
});

test('generated drafts stay outside benchmark until user checks the keys; persist per lecture', async () => {
  const h=await harness();
  h.get('material').value='two';h.get('material').onchange();
  assert.equal(h.get('bench').disabled,true);
  await h.get('generate').onclick();
  const review=h.get('review');
  const save=review.children.at(-1);
  save.onclick();assert.equal(h.get('bench').disabled,true);
  review.querySelectorAll('input').find(i=>i.type==='checkbox').checked=true;
  save.onclick();assert.equal(h.get('bench').disabled,false);
  const store=JSON.parse(h.storage.get('quizlens.practice.decks.v1'));
  assert.deepEqual(store.map(row=>row.id),['two']);
  assert.equal(store[0].cards.length,1);
  h.get('material').value='builtin';h.get('material').onchange();
  assert.equal(h.get('generation').hidden,true);
});

test('study opens from the cards link before model lookup ends; flipping keeps papers stable and shows all correct answers', async () => {
  const multi = {...card, mode:'multiple', variants:[{
    question:'Какие два ответа подходят?', correct:[1,2], reason:'Оба ответа даны в лекции.'
  }]};
  let finishLookup;
  const modelWait = new Promise(resolve => { finishLookup = resolve; });
  const h = await harness({cards:[card,multi], noModel:true, hash:'#cards', modelWait});
  assert.equal(h.get('flashcards').hidden,false);
  assert.equal(h.get('previousCard').disabled,true);
  assert.equal(h.get('refresh').disabled,true);
  assert.equal(h.get('cards').disabled,false);
  const first = h.get('cardQuestion').textContent;
  h.get('flipCard').onclick();
  assert.equal(h.get('cardAnswer').hidden,false);
  assert.equal(h.get('flipCard')['aria-expanded'],'true');
  assert.match(h.get('cardSource').text(),/Правило вычисления/);
  h.get('followingCard').onclick();
  assert.equal(h.get('cardAnswer').hidden,true);
  assert.equal(h.get('cardPosition').textContent,'Карточка 2 из 2');
  h.get('flipCard').onclick();
  assert.deepEqual(h.get('correctOptions').children.map(el => el.textContent), multi.options);
  assert.equal(h.get('cardReason').textContent,multi.variants[0].reason);
  h.get('previousCard').onclick();
  assert.equal(h.get('cardQuestion').textContent,first);
  assert.equal(h.get('cardAnswer').hidden,true);
  h.get('followingCard').onclick();
  h.get('followingCard').onclick();
  assert.equal(h.get('setup').hidden,false);
  assert.equal(h.get('flashcards').hidden,true);
  assert.equal(h.get('cards').focused,true);
  finishLookup(); await tick();
  assert.ok(h.calls.every(([url]) => url !== '/api/solve' && url !== '/api/train/generate'));
});

test('study uses verified cards for the selected lecture and never falls back to an unrelated deck', async () => {
  const own = {...card, variants:[{question:'Вопрос из своей лекции', correct:[1],reason:'Объяснение'}]};
  const saved = {'quizlens.practice.decks.v1':JSON.stringify([{id:'two',cards:[own]}])};
  const h = await harness({noModel:true,saved});
  h.get('material').value='two';h.get('material').onchange();h.get('cards').onclick();
  assert.equal(h.get('cardQuestion').textContent,own.variants[0].question);
  h.get('closeCards').onclick();
  h.get('material').value='one';h.get('material').onchange();
  assert.equal(h.get('cards').disabled,true);
});
