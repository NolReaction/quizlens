'use strict';

const action = document.querySelector('#action');
const message = document.querySelector('#message');
let tabId;
let command;

async function update(next = 'inspect') {
  action.disabled = true;
  try {
    if (!tabId) [ {id: tabId} ] = await chrome.tabs.query({active: true, currentWindow: true});
    const reply = await chrome.runtime.sendMessage({command: next, tabId});
    if (!reply?.ok) throw Error(reply?.error || 'Не удалось связаться с расширением.');
    command = reply.command;
    action.textContent = reply.label || 'Закрыть';
    message.textContent = reply.message;
    action.disabled = false;
    action.focus();
  } catch (error) {
    message.textContent = error.message;
    command = 'inspect';
    action.textContent = 'Проверить снова';
    action.disabled = false;
    action.focus();
  }
}

action.addEventListener('click', () => command ? update(command) : window.close());
update();
