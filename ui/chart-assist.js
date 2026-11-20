'use strict';

// A diagram is required only when the wording refers to data on this figure.
// "Which chart type is best?" remains an ordinary text question.
window.QuizChart = (() => {
  const figure = '(?:график[а-яё]*|диаграмм[а-яё]*|рисунк[а-яё]*|рисунок|таблиц[а-яё]*|гистограмм[а-яё]*|схем[а-яё]*)';
  const refer = new RegExp(
    '(?:по (?:данным |значениям |показаниям )?|согласно |судя по |используя |рассмотри(?:те)? |посмотри(?:те)? на |опираясь на )' +
    '(?:(?:показанн|привед[её]нн|представленн|следующ|данн|расположенн)[а-яё]* (?:ниже |выше )?)?' + figure +
    '|(?:на|из|в) (?:эт[а-яё]* |привед[её]нн[а-яё]* |следующ[а-яё]* )?' + figure +
    '(?: (?:показан|представлен|изображ[её]н|отображ[её]н|привед[её]н|видн|дан[ыа]|ниже|выше)| [0-9]|[?.,:;!]|$)' +
    '|(?:график|диаграмма|рисунок|таблица|гистограмма|схема) (?:выше|ниже|показывает|содержит данные)|' +
    '\\b(?:according to|using|as (?:shown|seen) (?:in|on)|based on) (?:the |this )?(?:(?:following|given|shown) )?(?:figure|graph|chart|table|plot)\\b|' +
    '\\b(?:the |this )?(?:table|chart|graph|figure|plot) (?:above|below|shows)\\b', 'iu');
  let last = null;
  let confirmedMode = null;

  function kind(question) {
    if (typeof question !== 'string') return null;
    const match = refer.exec(question);
    if (!match) return null;
    // A plain "в таблице?" can refer to a concept rather than to visible data.
    if (/^(?:на|в) /iu.test(match[0]) &&
        /(?:обычно|можно хранить|можно представить|для чего|термин|как (?:правильно |лучше )?(?:построить|обозначить|оформить))/iu.test(question)) return null;
    return /таблиц|table/iu.test(match[0]) ? 'table' : 'chart';
  }

  function show(question, selectionMode) {
    const type = kind(question);
    confirmedMode = type && ['single','multiple'].includes(selectionMode) ? selectionMode : null;
    const button = document.getElementById('visual');
    const prompt = document.getElementById('visualPrompt');
    if (button && prompt) {
      prompt.hidden = !type;
      prompt.textContent = type === 'table' ? 'Проверь заголовки строк и столбцов на кадре.'
        : type === 'chart' ? 'Для разбора должны быть видны оси, их единицы и легенда.' : '';
      button.hidden = !type;
      if (type) button.textContent = type === 'table' ? 'Открыть таблицу с вопросом ↗' : 'Открыть рисунок с вопросом ↗';
    }
    if (!question) last = null;
    return type;
  }

  function remember(fields, image, session) {
    if (!Array.isArray(fields) || fields.length < 3 || !kind(fields[0])) { last = null; return; }
    last = {session, fields: JSON.stringify(fields.map(s => s.normalize('NFC').replace(/\s+/g, ' ').trim())), image};
  }
  function needsFrame(session) { return Boolean(last && last.session === session); }
  function imageFor(fields, session) {
    return needsFrame(session) && JSON.stringify(fields) === last.fields ? last.image : null;
  }
  return {kind, show, remember, needsFrame, imageFor, get selectionMode() { return confirmedMode; }};
})();
