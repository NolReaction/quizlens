'use strict';

(() => {
  const button = document.querySelector('[data-extension-folder]');
  const feedback = document.querySelector('[data-extension-status]');
  if (!button || !feedback) return;
  fetch('/api/health', {cache:'no-store'}).then(reply => reply.json()).then(info => {
    button.hidden = !['windows','macos'].includes(info.platform);
    if (info.app === 'QuizLens') window.dispatchEvent(new CustomEvent('quizlens-extension-package', {detail:info.version}));
  }).catch(() => {});
  button.addEventListener('click', async () => {
    button.disabled = true;
    feedback.hidden = false;
    feedback.textContent = 'Открываю папку расширения…';
    try {
      const response = await fetch('/api/components/extension', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'open-folder'})});
      const data = await response.json();
      if (!response.ok || !data.path) throw Error(data.error || 'Не удалось открыть папку расширения.');
      feedback.textContent = 'В настройках расширений браузера нажми «Загрузить распакованное расширение» и выбери ' + data.path;
    } catch (error) { feedback.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
