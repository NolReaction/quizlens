'use strict';

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../ui/app-update.js'), 'utf8');

function app(initial) {
  const port = {status:{currentVersion:'1.8.1', latestVersion:'1.8.2', automatic:true, supported:true,
    ...initial}, elements:new Map(), timers:new Map(), requests:[], reloads:0, busy:false};
  let serial = 0;
  function element(id) {
    if (!port.elements.has(id)) port.elements.set(id, {addEventListener(_, callback) { this.click = callback; }});
    return port.elements.get(id);
  }
  const activity = {busy:() => port.busy, whenIdle:async callback => {
    if (port.busy) return false;
    await callback();
    return true;
  }};
  vm.runInNewContext(source, {
    window:{quizLensUpdateActivity:activity}, document:{visibilityState:'visible',
      getElementById:element, addEventListener() {}}, location:{reload() { port.reloads++; }},
    navigator:{locks:{}}, AbortController,
    setTimeout(callback) { const id = ++serial; port.timers.set(id, callback); return id; },
    clearTimeout(id) { port.timers.delete(id); },
    fetch:async (path, {body}) => {
      if (port.offline) throw Error('offline');
      if (path.endsWith('/health')) return {ok:true, json:async () => ({app:'QuizLens', version:port.status.currentVersion})};
      if (body) {
        const {action} = JSON.parse(body);
        port.requests.push(action);
        if (action === 'activate' && !port.backendBusy) port.status.state = 'restarting';
      }
      return {ok:true, json:async () => ({...port.status})};
    }
  });
  port.id = element;
  port.tick = async () => {
    const scheduled = [...port.timers.entries()].at(-1);
    assert.ok(scheduled, 'next status check is scheduled');
    port.timers.delete(scheduled[0]);
    await scheduled[1]();
  };
  return port;
}

test('update recovers after a disconnected restart and displays a rollback without reactivating', async () => {
  const p = app({state:'ready'});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(p.requests, ['activate']);
  p.offline = true;
  await p.tick();
  assert.match(p.id('updateMessage').textContent, /Восстанавливаю/);
  p.offline = false; p.status.state = 'error'; p.status.message = 'Восстановлена предыдущая версия.';
  await p.tick();
  assert.equal(p.reloads, 0);
  assert.deepEqual(p.requests, ['activate']);
  assert.equal(p.id('updateMessage').textContent, p.status.message);
  assert.equal(p.id('updateAction').disabled, false);
});

test('an existing tab reloads the new version only after its capture finishes', async () => {
  const p = app({state:'current'});
  await new Promise(resolve => setImmediate(resolve));
  p.busy = true; p.status.currentVersion = '1.8.2';
  await p.tick();
  assert.equal(p.reloads, 0);
  assert.match(p.id('updateMessage').textContent, /после завершения захвата/);
  p.busy = false;
  await p.tick();
  assert.equal(p.reloads, 1);
});

test('a checkout without the release launcher exposes a usable download link', async () => {
  const p = app({state:'unsupported', supported:false, message:'Нужна официальная сборка.'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.id('manualRelease').hidden, false);
  assert.equal(p.id('automaticLabel').hidden, true);
  assert.equal(p.id('updateAction').hidden, true);
  assert.deepEqual(p.requests, []);
});

test('a staged update retries after the server finishes an operation', async () => {
  const p = app({state:'ready', automatic:false});
  await new Promise(resolve => setImmediate(resolve));
  p.backendBusy = true;
  p.id('updateAction').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.status.state, 'ready');
  assert.equal(p.id('updateAction').disabled, false);
  p.backendBusy = false;
  await p.tick();
  assert.equal(p.status.state, 'restarting');
  assert.deepEqual(p.requests, ['activate','activate']);
});
