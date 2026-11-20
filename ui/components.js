'use strict';

(() => {
  const box = document.getElementById('ocrComponent');
  const message = document.getElementById('ocrComponentStatus');
  const install = document.getElementById('ocrInstall');
  let timer;
  let previous;
  let checking = false;
  let requestedInstall = false;

  function updateVisibility() {
    const isLinked = location.hash === '#ocrComponent';
    box.hidden = !isLinked && !requestedInstall && (!box.dataset.status || box.dataset.status === 'ready');
    return isLinked;
  }

  function followComponentLink() {
    if (updateVisibility()) {
      box.focus({preventScroll:true});
      box.scrollIntoView({block:'start'});
    }
  }
  window.addEventListener('hashchange', followComponentLink);
  followComponentLink();

  async function refresh(start = false) {
    if (checking) return;
    checking = true;
    clearTimeout(timer);
    install.disabled = true;
    if (start) {
      requestedInstall = true;
      box.dataset.status = 'installing';
      message.textContent = 'Запускаю установщик распознавания…';
    }
    try {
      const options = start ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'install'})} : {};
      const response = await fetch('/api/components/ocr', {cache:'no-store', ...options});
      const result = await response.json();
      if (!response.ok) throw Error(result.message || result.error || 'Сбой при проверке распознавания.');
      previous = result;
      box.dataset.status = result.status;
      message.textContent = result.message;
      install.hidden = !result.canInstall || result.status === 'ready';
      install.disabled = result.status === 'installing';
      install.textContent = result.status === 'installing' ? 'Идёт установка…' : 'Установить OCR';
      if (install.disabled) timer = setTimeout(refresh, 2000);
    } catch (error) {
      message.textContent = error.message || 'Не удалось связаться с QuizLens.';
      // A failed response to POST can occur while the installer is already running.
      // Re-read its status before allowing another install.
      if (start || previous?.status === 'installing') timer = setTimeout(refresh, 3000);
      else { box.dataset.status = 'unknown'; install.disabled = false; }
    } finally {
      checking = false;
      updateVisibility();
    }
  }
  window.addEventListener('quizlens-check-ocr', () => {
    if (box.dataset.status !== 'installing') refresh();
  });
  install.addEventListener('click', () => refresh(true));
  refresh();
})();
