'use strict';

// Only disputed fields are editable. Keep the original ordering of every choice.
window.addQuizReadingEditor = function (container, texts, warnings, confirm) {
  const doc = container.ownerDocument;
  const form = doc.createElement('form');
  form.className = 'reading-editor';
  const fields = [...new Set(warnings.map(item => item.field))];
  const inputs = fields.map(field => {
    const label = doc.createElement('label');
    label.textContent = field === 0 ? 'Текст вопроса' : 'Текст варианта ' + field;
    const input = doc.createElement('textarea');
    input.rows = 2;
    input.required = true;
    input.maxLength = Math.max(4096, texts[field].length);
    input.value = texts[field];
    label.append(input);
    form.append(label);
    return input;
  });
  const button = doc.createElement('button');
  button.type = 'submit';
  button.className = 'button primary';
  button.textContent = 'Подтвердить текст';
  const message = doc.createElement('p');
  message.className = 'hint';
  message.setAttribute('role', 'status');
  form.append(button, message);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    const values = fields.map((field, i) => ({field, text: inputs[i].value.normalize('NFC').replace(/\s+/g, ' ').trim()}));
    const corrected = texts.slice();
    for (const edit of values) corrected[edit.field] = edit.text;
    if (!corrected.every(Boolean) || new Set(corrected.slice(1)).size !== corrected.length - 1) {
      message.textContent = 'Заполни вопрос и каждый вариант. Варианты должны различаться.';
      return;
    }
    button.disabled = true;
    try {
      await confirm(values);
      message.textContent = 'Проверяю, что на экране тот же вопрос…';
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  });
  container.append(form);
};
