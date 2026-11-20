'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {detectDevice, parseRelease, enablePage, enableNavigation} = require('../site/download.js');

const repo = 'demo/QuizLens';
const url = 'https://github.com/'+repo;
const names = ['QuizLens-Mac.zip','QuizLens-Windows.zip','QuizLens-Guide.md','SHA256SUMS.txt', 'QuizLens-Extension.zip'];
function payload(body, assets = names) {
  return {tag_name:'v2.0.0', body, assets:assets.map(name => ({name, state:'uploaded', size:123}))};
}
test('classifies desktop, ambiguous Windows, Android, and iPad in desktop mode', () => {
  assert.equal(detectDevice({platform:'MacIntel', maxTouchPoints:1}),'mac');
  assert.equal(detectDevice({platform:'MacIntel', maxTouchPoints:5}),'mobile');
  assert.equal(detectDevice({platform:'Linux armv8', userAgent:'Android 12'}),'mobile');
  assert.equal(detectDevice({platform:'Win32', userAgent:'Windows NT 10.0; Win64; x64'}),'windows');
  assert.equal(detectDevice({platform:'Win32',userAgent:'Windows NT 10.0; Win64; x64'}, {architecture:'arm',bitness:'64'}),'windowsOther');
  assert.equal(detectDevice({platform:'Win32'}, {architecture:'x86',bitness:'32',wow64:true}),'windows');
  assert.equal(detectDevice({platform:'Win32'}, {architecture:'x86',bitness:'32'}),'windowsOther');
  assert.equal(detectDevice({platform:'Win32'}),'windowsUnknown');
  assert.equal(detectDevice({platform:'Linux x86_64'}),'linux');
  assert.equal(detectDevice({platform:'Linux', userAgent:'CrOS x86_64'}),'unknown');
});
test('uses published assets for exact tag and never promotes absent, unknown or conflicting signing claims', () => {
  const marked = '<!-- quizlens-macos: notarized -->';
  const draft = payload(marked); draft.draft = true;
  assert.throws(()=>parseRelease(draft,url));
  const onlyMac = payload(marked,[names[0]]);
  onlyMac.assets.push({name:names[1], state:'new', size:123});
  onlyMac.assets[0].browser_download_url='https://example.org/';
  const result = parseRelease(onlyMac,url);
  assert.equal(result.mac,url+'/releases/download/v2.0.0/QuizLens-Mac.zip');
  assert.equal(result.windows,null);
  assert.equal(result.macStatus,'notarized');
  assert.equal(parseRelease(payload(''),url).macStatus,'unknown');
  assert.equal(parseRelease(payload(marked+' <!-- quizlens-macos: unsigned -->'),url).macStatus,'unknown');
  assert.equal(parseRelease(payload('<!-- quizlens-macos: unsigned -->'),url).macStatus,'unsigned');
});

class Element {
  constructor() {
    this.textContent = ''; this.children = []; this.events = {}; this.classList = {toggle:()=>{}};
  }
  querySelector() { return this.child ||= new Element(); }
  addEventListener(name, handler) { this.events[name] = handler; }
  append(...children) { this.children.push(...children); }
}
function surface(result, platform = 'Win32', clipboard) {
  const nodes = new Map();
  const doc = {body:{dataset:{repository:repo}}, getElementById(id) {
    if (!nodes.has(id)) nodes.set(id,new Element());
    return nodes.get(id);
  },createElement:()=>new Element()};
  const location = new URL('https://download.example.org/quiz/index.html?x=1#mac-install');
  const request = async endpoint => {
    assert.equal(endpoint,'https://api.github.com/repos/demo/QuizLens/releases/latest');
    if (result instanceof Error) throw result;
    return result;
  };
  const done = enablePage(doc,{platform, clipboard},location,request);
  return {get:id=>doc.getElementById(id),done};
}
test('completed releases yield real manual downloads and preferred action only on a compatible platform', async () => {
  const response = {ok:true,status:200,json:async()=>payload('<!-- quizlens-macos: notarized -->')};
  let copied;
  const {done,get} = surface(response, 'MacIntel', {writeText:async v=>{copied=v;}});
  await done;
  assert.equal(get('mac-download').href,url+'/releases/download/v2.0.0/QuizLens-Mac.zip');
  assert.equal(get('mac-card').querySelector('.match').hidden, false);
  assert.equal(get('windows-card').querySelector('.match').hidden, true);
  assert.equal(get('windows-download').href,url+'/releases/download/v2.0.0/QuizLens-Windows.zip');
  assert.match(get('sign-info').textContent,/прошёл нотарификацию/);
  await get('share').events.click();
  assert.equal(copied,'https://download.example.org/quiz/');
  const linux = surface(response,'Linux');
  await linux.done;
  assert.equal(linux.get('mac-card').querySelector('.match').hidden, true);
  assert.equal(linux.get('windows-card').querySelector('.match').hidden, true);
  assert.equal(linux.get('mac-download').hidden,false);
});
test('404 disables absent assets, network errors preserve the HTML release-list fallback',async () => {
  const empty=surface({status:404});
  await empty.done;
  assert.equal(empty.get('mac-download').hidden,true);
  assert.equal(empty.get('windows-download').hidden,true);
  assert.match(empty.get('release-status').textContent,/пока не опубликован/);
  const throttled=surface({ok:false,status:403});
  await throttled.done;
  assert.equal(throttled.get('mac-download').hidden,undefined);
  assert.match(throttled.get('release-status').textContent,/списке выпусков/);
});

test('Mac card describes unsigned and unconfirmed builds beside its download', async () => {
  for (const body of ['', '<!-- quizlens-macos: unsigned -->']) {
    const {done,get} = surface({ok:true,status:200,json:async()=>payload(body)}, 'MacIntel');
    await done;
    assert.equal(get('mac-download').href,url+'/releases/download/v2.0.0/QuizLens-Mac.zip');
    assert.match(get('sign-info').textContent, body ? /без нотарификации/ : /не указан/);
  }
});

test('older single-platform releases show only the available desktop download', async () => {
  const prior = surface({ok:true, status:200, json:async()=>payload('', ['QuizLens-Windows.zip'])});
  await prior.done;
  assert.equal(prior.get('mac-download').hidden, true);
  assert.equal(prior.get('windows-download').hidden, false);
});

test('compact navigation supports keyboard dismissal, focus transfer, and desktop resizing', () => {
  class Control extends EventTarget {
    constructor() { super(); this.attributes = {}; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    contains(target) { return this === target; }
    focus() { focused = this; }
  }
  let focused;
  const compact = Object.assign(new EventTarget(), {matches:true});
  const doc = new Control();
  const view = Object.assign(new EventTarget(), {
    scrollY:0, innerHeight:800, matchMedia:()=>compact,
    requestAnimationFrame:callback=>callback()
  });
  const button = new Control();
  const nav = new Control();
  const ids = ['download','start','extension','components'];
  const links = ids.map(id => Object.assign(new Control(), {hash:'#'+id}));
  const targets = ids.map((id,index) => Object.assign(new Control(), {
    getBoundingClientRect:()=>({top:80 + index * 500 - view.scrollY})
  }));
  const elements = new Map(ids.map((id,index)=>[id,targets[index]]));
  elements.set('site-nav', nav);
  elements.set('nav-toggle', button);
  nav.querySelectorAll = ()=>links;
  doc.getElementById = id=>elements.get(id);
  doc.querySelector = ()=>({offsetHeight:70});
  doc.documentElement = {scrollHeight:2400};
  enableNavigation(doc, view);
  assert.equal(nav.hidden, true);
  button.dispatchEvent(new Event('click'));
  assert.equal(button.attributes['aria-expanded'], 'true');
  assert.equal(nav.hidden, false);
  doc.dispatchEvent(Object.assign(new Event('keydown'), {key:'Escape'}));
  assert.equal(nav.hidden, true);
  assert.equal(focused, button);
  button.dispatchEvent(new Event('click'));
  links[2].dispatchEvent(new Event('click'));
  assert.equal(nav.hidden, true);
  assert.equal(focused, targets[2]);
  view.scrollY = 1020;
  view.dispatchEvent(new Event('scroll'));
  assert.equal(links[2].attributes['aria-current'], 'location');
  assert.equal(links[0].attributes['aria-current'], undefined);
  compact.matches = false;
  compact.dispatchEvent(new Event('change'));
  assert.equal(nav.hidden, false);
  assert.equal(button.hidden, true);
});
