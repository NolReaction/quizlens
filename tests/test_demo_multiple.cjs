"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const vm = require("node:vm");

const read = name => readFileSync(join(__dirname, "..", name), "utf8");
const cards = JSON.parse(read("examples/analytics-demo.quiz.json")).cards;
const markup = read("ui/demo.html");

function element(id = "") {
  const attributes = new Map();
  const classes = new Set();
  const listeners = {};
  const label = {textContent: ""};
  return {
    id, style: {}, options: [], listeners, disabled: false, value: "",
    addEventListener: (type, fn) => { listeners[type] = fn; },
    querySelector: () => label, cloneNode: () => element(),
    getAttribute: key => attributes.get(key),
    setAttribute: (key, value) => attributes.set(key, value),
    removeAttribute: key => attributes.delete(key),
    classList: {
      toggle: (key, enabled) => enabled ? classes.add(key) : classes.delete(key),
      contains: key => classes.has(key)
    },
    set className(value) { classes.clear(); classes.add(value); }
  };
}

// Run the same handlers used for pointer and keyboard input, with a controlled timer.
async function openDemo(query = "") {
  const ui = Object.fromEntries([...markup.matchAll(/\bid="([^"]+)"/g)].map(([, id]) => [id, element(id)]));
  for (const [, id, options] of markup.matchAll(/<select id="([^"]+)">([^<]*(?:<option[^>]*>[^<]*<\/option>)+)<\/select>/g)) {
    ui[id].options = [...options.matchAll(/value="([^"]+)"/g)].map(([, value]) => ({value}));
    ui[id].value = ui[id].options[0].value;
  }
  const buttons = [...markup.matchAll(/class="answer"/g)].map(() => element());
  ui.options.append = button => buttons.push(button);
  const document = Object.assign(element(), {
    documentElement: {}, querySelectorAll: key => key === "[id]" ? Object.values(ui) : buttons
  });
  const timer = {now: 0};
  vm.runInNewContext(read("ui/demo.js"), {
    document, location: {search: query}, URLSearchParams, performance: {now: () => timer.now},
    setInterval: run => { timer.tick = run; return 1; }, clearInterval: () => {},
    fetch: async () => ({ok: true, json: async () => ({cards})})
  });
  await new Promise(resolve => setImmediate(resolve));
  const action = () => { if (!ui.action.disabled && !ui.action.hidden) ui.action.listeners.click(); };
  const key = n => document.listeners.keydown({key: String(n)});
  const correct = () => buttons.flatMap((b, i) => b.classList.contains("correct") ? [i+1] : []);
  return {ui, buttons, timer, action, key, correct};
}

for (const language of ["ru", "en"]) {
  test(`multi-select grades the exact set across both layouts (${language})`, async () => {
    const {ui, buttons, action, key, correct} = await openDemo("?lobby=1&mode=multiple&lang=" + language);
    assert.equal(ui.round.hidden, true);
    action();
    const stem = ui.question.textContent;
    assert.equal(buttons.every(button => button.getAttribute("role") === "checkbox"), true);
    assert.equal(ui.action.disabled, true);
    key(3);
    assert.equal(ui.action.disabled, true);
    buttons[0].listeners.click();
    assert.equal(ui.action.disabled, false);
    key(4);
    assert.equal(ui.action.disabled, true);
    key(4);
    action();
    assert.deepEqual(correct(), [1, 3]);
    action();
    assert.equal(ui.question.textContent, stem);
    assert.equal(ui.round.classList.contains("list-layout"), true);
    assert.equal(buttons.every(button => button.getAttribute("aria-checked") === "false"), true);
    key(4);
    key(2);
    action();
    assert.deepEqual(correct(), [2, 4]);
    action();
    assert.match(ui.score.textContent, /2 .*2/);
    action();
    action();
    key(1);
    key(2);
    action();
    assert.equal(buttons[1].classList.contains("wrong"), true);
    assert.equal(buttons[0].classList.contains("wrong"), false);
  });
}

test("timer expires during a partial selection and the demo accepts the next round", async () => {
  const {ui, timer, action, key, correct} = await openDemo("?lobby=1&mode=multiple");
  action();
  key(1);
  timer.now = 20000;
  timer.tick();
  assert.match(ui.feedback.textContent, /^Время вышло/);
  assert.deepEqual(correct(), [1, 3]);
  action();
  assert.equal(ui.action.disabled, true);
});

test("variable demo grows from two to eight options and grades the displayed set", async () => {
  const {buttons, ui, action, key, correct} = await openDemo("?lobby=1&mode=variable");
  const counts = [2, 3, 5, 8];
  const answers = [[1], [2], [2, 5], [5]];
  action();
  for (const [i, expected] of answers.entries()) {
    assert.equal(buttons.filter(b => !b.hidden).length, counts[i]);
    key(8);
    if (counts[i] === 8) {
      // A single-choice click on option eight grades immediately.
      assert.equal(buttons[7].classList.contains("wrong"), true);
    } else {
      expected.forEach(key);
      if (expected.length > 1) action();
    }
    assert.deepEqual(correct(), expected);
    action();
  }
  assert.match(ui.score.textContent, /3 .*4/);
});

test("stock six-question quiz stays single-choice, and the setup selector can change mode", async () => {
  const {ui, buttons, action, key, correct} = await openDemo("?lobby=1");
  assert.equal(ui.mode.value, "single");
  action();
  assert.match(ui.position.textContent, /1 из 6/);
  assert.equal(buttons.some(b => b.getAttribute("role") === "checkbox"), false);
  assert.equal(ui.action.hidden, true);
  key(2);
  assert.deepEqual(correct(), [2]);
  assert.equal(ui.action.hidden, false);
  const setup = await openDemo("?lobby=1");
  setup.ui.mode.value = "multiple";
  setup.ui.mode.listeners.change();
  setup.action();
  assert.match(setup.ui.position.textContent, /1 из 2/);
  assert.equal(setup.buttons[0].getAttribute("role"), "checkbox");
});
