'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname,'../ui/chart-assist.js'),'utf8');

test('offer visual reading for a referenced figure; retain ordinary text questions', () => {
  const elements = {visual:{}, visualPrompt:{}};
  const ctx = {window:{}, document:{getElementById:id=>elements[id]}};
  vm.runInNewContext(code,ctx);
  const chart = ctx.window.QuizChart;
  for (const [question,type] of [
    ['По графику определи месяц максимальных продаж.', 'chart'],
    ['Чему равна конверсия по данным таблицы?', 'table'],
    ['На рисунке показано распределение. Найди медиану.', 'chart'],
    ['Какое значение у Москвы на графике?', 'chart'],
    ['Which channel grew the most according to the table?', 'table'],
    ['Какие типы данных можно хранить в таблице базы данных?', null],
    ['Какая диаграмма лучше подойдёт для сравнения?', null],
    ['Какие типы данных можно хранить в таблице?', null],
    ['Из 1000 клиентов ушли 10. Чему равна Accuracy?', null],
  ]) assert.equal(chart.kind(question),type,question);
  const fields=['По графику определи год.','2019','2020'];
  chart.show('Какая диаграмма лучше подойдёт для сравнения?');
  assert.equal(elements.visual.hidden,true);
  assert.equal(elements.visualPrompt.hidden,true);
  chart.show(fields[0], 'multiple');
  assert.equal(chart.selectionMode,'multiple');
  assert.equal(elements.visual.hidden,false);
  assert.equal(elements.visualPrompt.hidden,false);
  assert.match(elements.visual.textContent,/рисунок/);
  chart.remember(fields,'frozen-pixels',7);
  assert.equal(chart.imageFor(fields,7),'frozen-pixels');
  assert.equal(chart.imageFor([...fields].reverse(),7),null);
  assert.equal(chart.imageFor(fields,8),null);
  chart.show(null);
  assert.equal(elements.visual.hidden,true);
  assert.equal(chart.selectionMode,null);
  assert.equal(chart.imageFor(fields,7),null);
});
