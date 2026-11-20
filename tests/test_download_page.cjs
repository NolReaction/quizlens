'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {detectDevice, parseRelease, enablePage} = require('../site/download.js');

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
  assert.equal(get('hero-action').href,url+'/releases/download/v2.0.0/QuizLens-Mac.zip');
  assert.equal(get('windows-download').href,url+'/releases/download/v2.0.0/QuizLens-Windows.zip');
  assert.match(get('sign-info').textContent,/прошёл нотарификацию/);
  assert.equal(get('extension-download').href, url+'/releases/download/v2.0.0/QuizLens-Extension.zip');
  assert.equal(get('setup-extension-download').href, get('extension-download').href);
  await get('share').events.click();
  assert.equal(copied,'https://download.example.org/quiz/');
  const linux = surface(response,'Linux');
  await linux.done;
  assert.equal(linux.get('hero-action').href,'#download');
  assert.equal(linux.get('mac-download').hidden,false);
});
test('404 disables absent assets, network errors preserve the HTML release-list fallback',async () => {
  const empty=surface({status:404});
  await empty.done;
  assert.equal(empty.get('mac-download').hidden,true);
  assert.equal(empty.get('windows-download').hidden,true);
  assert.equal(empty.get('extension-download').href,'./QuizLens-Extension.zip');
  assert.match(empty.get('release-status').textContent,/пока не опубликован/);
  const throttled=surface({ok:false,status:403});
  await throttled.done;
  assert.equal(throttled.get('mac-download').hidden,undefined);
  assert.match(throttled.get('release-status').textContent,/списке выпусков/);
});

test('Mac hero opens signing information before an unsigned or unconfirmed download', async () => {
  for (const body of ['', '<!-- quizlens-macos: unsigned -->']) {
    const {done,get} = surface({ok:true,status:200,json:async()=>payload(body)}, 'MacIntel');
    await done;
    assert.equal(get('hero-action').href,'#mac-card');
    assert.equal(get('mac-download').href,url+'/releases/download/v2.0.0/QuizLens-Mac.zip');
    assert.match(get('sign-info').textContent, body ? /без нотарификации/ : /не указан/);
  }
});

test('older releases keep the single extension download from the static site', async () => {
  const prior = surface({ok:true, status:200, json:async()=>payload('', ['QuizLens-Windows.zip'])});
  await prior.done;
  assert.equal(prior.get('setup-extension-download').href, './QuizLens-Extension.zip');
  assert.equal(prior.get('extension-download').href, './QuizLens-Extension.zip');
});
