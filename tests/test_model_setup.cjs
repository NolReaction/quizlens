'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const directory = path.join(__dirname,'../ui');
const html = readFileSync(path.join(directory,'setup.html'),'utf8');
const js = readFileSync(path.join(directory,'setup.js'),'utf8');
const usageJs = readFileSync(path.join(directory,'token-usage.js'),'utf8');
const wait = () => new Promise(resolve=>setImmediate(resolve));
const card = {
  id:'qwen3-4b-instruct-2507',name:'Qwen3-4B-Instruct-2507',fileSize:'2,50 ГБ',quant:'Q4_K_M',installedId:'qwen4',
  search:'publisher/model-GGUF',downloadUrl:'https://huggingface.co/publisher/model/resolve/main/Qwen3-Q4_K_M.gguf?download=true',reason:'Выбор по памяти.'
};
const auto = {profile:{platform:'macos',accelerator:'apple',cpuName:'Apple M4 Pro',ramGiB:24},primary:card,alternative:null,
    needsHardware:false,notes:[],models:['qwen4','existing'],installedMatch:'qwen4',serverAvailable:true,caseCount:3};
const values = () => new Map([['quizlens.settings.v1',JSON.stringify({model:'existing',record:false,selected:['abc']})]]);
const sample = (index,model) => ({caseIndex:index,title:'Тест '+index,firstCall:!index,status:'ok',passed:true,
  withinBudget:true,withinLiveTimeout:model==='qwen4',elapsedMs:model==='qwen4'?[3900,1000,3000][index]:12000});

class Field {
  constructor(tag) {this.tag=tag;this.children=[];this.value='';this.textContent='';this.hidden=false;}
  replaceChildren(...nodes){this.children=nodes;}
  append(...nodes){this.children.push(...nodes);}
  prepend(...nodes){this.children.unshift(...nodes);}
  querySelector(){return this.appleOption??={};}
  focus(){this.focused=true;}
  setAttribute(key,val){this[key]=val;}
  reportValidity(){return this.valid!==false;}
}
class Option extends Field {constructor(label,val){super('option');this.textContent=label;this.value=val;}}

async function browser({info=auto,store=values(),benchmark=sample}={}) {
  const nodes = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Field(match[1])]));
  const calls=[];
  const fetch=async (url, options) => {
    const input=options.body ? JSON.parse(options.body) : null;
    calls.push([url,input]);
    const data = url.endsWith('benchmark') ? await benchmark(input.caseIndex,input.model)
      : !input ? info : {profile:input,primary:{...card,installedId:null},notes:[],models:[],serverAvailable:null};
    return {ok:true,json:async()=>data};
  };
  const context=vm.createContext({document:{getElementById:id=>nodes[id],createElement:tag=>new Field(tag)}, fetch, Option, URL,
    navigator:{clipboard:{writeText:async()=>{}}},localStorage:{getItem:key=>store.get(key),setItem:(key,val)=>store.set(key,val)}});
  vm.runInContext(usageJs,context);
  vm.runInContext(js,context);
  await wait();
  return {nodes,calls,store};
}

test('prefill preserves explicit model choice and lecture preferences; recommended local model may be chosen explicitly',async()=>{
  const {nodes,store,calls}=await browser();
  assert.equal(nodes['hardware'].textContent,'macOS · Apple M4 Pro · 24 ГБ общей памяти');
  assert.equal(nodes['installed-model'].value,'existing');
  assert.equal(nodes['apply-model'].disabled,true);
  const actions=nodes['primary-model'].children.find(x=>x.className==='actions');
  assert.match(actions.children[1].href,/Q4_K_M\.gguf/);
  actions.children[0].onclick();
  assert.equal(nodes['installed-model'].value,'qwen4');
  assert.equal(JSON.parse(store.get('quizlens.settings.v1')).model,'existing');
  nodes['apply-model'].onclick();
  assert.deepEqual(JSON.parse(store.get('quizlens.settings.v1')),{model:'qwen4',record:false,selected:['abc']});
  assert.deepEqual(calls,[['/api/advisor',null]]);
});

test('offline recommendation can be downloaded; a Windows manual preview neither checks this machine nor changes preferences',async()=>{
  const {nodes,store,calls}=await browser({info:{...auto,serverAvailable:false,models:[],primary:{...card,installedId:null}}});
  assert.equal(nodes['recommendation'].hidden,false);
  assert.equal(nodes['available'].hidden,true);
  assert.equal(nodes['install'].hidden,false);
  assert.doesNotMatch(html, /<details[^>]+id="install"/);
  nodes['device-switch'].onclick();
  assert.equal(nodes['local-panel'].hidden,true);
  nodes.platform.value='windows';
  nodes.platform.onchange();
  assert.equal(nodes.accelerator.value,'unknown');
  nodes.accelerator.value='discrete';
  nodes.accelerator.onchange();
  assert.equal(nodes['vram-field'].hidden,false);
  nodes.ram.value='32';nodes.vram.value='10';
  await nodes['profile-form'].onsubmit({preventDefault(){}});
  assert.deepEqual(calls.at(-1),['/api/advisor',{platform:'windows',accelerator:'discrete',ramGiB:32,vramGiB:10}]);
  assert.equal(nodes.recommendation.hidden,false);
  nodes['profile-form'].oninput();
  assert.equal(nodes.recommendation.hidden,true);
  await nodes.benchmark.onclick();
  assert.equal(calls.length,2);
  assert.equal(JSON.parse(store.get('quizlens.settings.v1')).model,'existing');
});

test('benchmark measures once per question, distinguishes live timeout and persists comparable completed runs',async()=>{
  const {nodes,calls,store} = await browser();
  await nodes.benchmark.onclick();
  assert.match(nodes['bench-summary'].textContent,/3 из 3/);
  assert.match(nodes['bench-summary'].textContent,/больше 10 с/);
  assert.equal(nodes.comparison.hidden,true);
  nodes['installed-model'].value='qwen4';
  nodes['installed-model'].onchange();
  await nodes.benchmark.onclick();
  assert.equal(nodes.comparison.hidden,false);
  assert.deepEqual(calls.filter(call=>call[0].endsWith('benchmark')).map(call=>call[1].caseIndex),[0,1,2,0,1,2]);
  assert.equal(nodes['history-rows'].children[0].children[2].textContent,'2 с');
  const restored=await browser({store});
  assert.equal(restored.nodes.comparison.hidden,false);
  assert.equal(restored.nodes['history-rows'].children.length,2);
});

test('unavailable model stops probes and does not record an invented zero time or a complete comparison',async()=>{
  const {nodes,calls,store} = await browser({benchmark:(index)=>({...sample(index,'qwen4'),status:'error',detail:'Сервер отключён.',elapsedMs:null})});
  await nodes.benchmark.onclick();
  assert.equal(calls.length,2);
  assert.match(nodes['bench-summary'].textContent,/Сервер отключён/);
  assert.ok(!nodes['bench-cases'].children[0].textContent.includes('0 с'));
  assert.equal(store.has('quizlens.advisor.bench.v1'),false);
});

test('a result arriving after manual comparison opens cannot appear or alter local history',async()=>{
  let finish;
  const delayed = new Promise(resolve=>{finish=resolve;});
  const {nodes,calls,store}=await browser({benchmark:()=>delayed});
  const pending=nodes.benchmark.onclick();
  nodes['device-switch'].onclick();
  finish(sample(0,'qwen4'));
  await pending;
  assert.equal(nodes['bench-result'].hidden,true);
  assert.equal(nodes['bench-cases'].children.length,0);
  assert.equal(calls.length,2);
  assert.equal(store.has('quizlens.advisor.bench.v1'),false);
});

test('a slow first call with fast subsequent answers suggests warming the model',async()=>{
  const {nodes} = await browser({benchmark:(index)=>({...sample(index,'qwen4'),status:index?'ok':'late',correct:true,
    passed:Boolean(index),withinLiveTimeout:Boolean(index),withinBudget:Boolean(index),elapsedMs:index?1100:17000})});
  await nodes.benchmark.onclick();
  assert.match(nodes['bench-summary'].textContent,/2 из 3/);
  assert.match(nodes['bench-summary'].textContent,/оставь модель загруженной/);
});

test('alternative and additional models show distinct names, limits and token example without opening details', async () => {
  const primary = {...card, parametersB:4, contextTokens:32768, maxOutputTokens:8192};
  const alternative = {...primary, id:'second', name:'Second model', maxOutputTokens:null};
  const other = {...primary, id:'third', name:'Third model', fit:'limited', fitLabel:'Мало памяти'};
  const example = {inputTokens:631, outputTokens:7, outputLimitSingle:80, outputLimitMultipleMin:160, outputLimitMultipleMax:224};
  const {nodes} = await browser({info:{...auto, primary, alternative, catalog:[primary, alternative, other], quizTokenExample:example}});
  assert.doesNotMatch(html, /<details[^>]+id="alternative"/);
  assert.equal(nodes.alternative.hidden, false);
  const limits = nodes['other-model'].children.find(node => node.tag==='dl').children;
  assert.ok(limits.some(row => row.children[0].textContent === 'Параметры' && row.children[1].textContent === '4 млрд'));
  assert.ok(limits.some(row => row.children[0].textContent === 'Макс. ответ' && row.children[1].textContent === 'Не указан автором'));
  assert.equal(nodes['model-catalog'].hidden, false);
  const [entry] = nodes['catalog-grid'].children;
  assert.equal(nodes['catalog-grid'].children.length, 1);
  assert.ok(entry.children.some(child => child.textContent === other.name));
  assert.equal(nodes['quiz-budget'].hidden, false);
  assert.match(nodes['example-input'].textContent, /631/);
  assert.match(nodes['budget-limits'].textContent, /80.+160.+224/);
});

test('catalog remains visible on unsupported or unknown hardware even without a recommended model', async () => {
  const {nodes} = await browser({info:{...auto, needsHardware:true, primary:null, alternative:null, catalog:[card]}});
  assert.equal(nodes['recommendation'].hidden, false);
  assert.equal(nodes['alternative'].hidden, true);
  assert.equal(nodes['catalog-grid'].children.length, 1);
  assert.equal(nodes['model-catalog'].hidden, false);
});
