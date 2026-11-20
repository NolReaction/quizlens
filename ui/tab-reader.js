'use strict';

(() => {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return;
  const status = document.querySelector('#tabReaderStatus');
  const hint = document.querySelector('#tabReaderHint');
  const install = document.querySelector('#tabReaderInstall');
  const stop = document.querySelector('#stopTabReader');
  if (!status || !hint || !install || !stop) return;

  const messages = {
    checking: ['Проверяю связь', 'Ищу QuizLens в этом браузере…'],
    undetected: ['Нет связи', 'Если расширение уже установлено, включи его в этом браузере и обнови страницу. Для первой установки раскрой инструкцию.'],
    unsupported: ['Другой браузер', 'Для чтения через расширение открой QuizLens и квиз в Chrome или Edge на компьютере.'],
    available: ['Готово к подключению', 'Нажми значок QuizLens в панели этого браузера и выбери «Подключить QuizLens».'],
    paired: ['Подключено', 'Открой вкладку квиза в этом браузере и нажми в расширении «Читать вопросы».'],
    waiting: ['Жду вопрос', 'Покажи вопрос с вариантами. Для квиза на картинке нажми «Выбрать окно».'],
    partial: ['Часть вопроса скрыта', 'Покажи вопрос и все варианты одновременно или используй захват окна.'],
    multiple: ['Несколько вопросов', 'Оставь на экране один вопрос или используй захват окна.'],
    question: ['Вопрос прочитан', 'Вопрос и варианты переданы в QuizLens.'],
    stopped: ['Чтение остановлено', 'Чтобы продолжить, открой вкладку квиза и нажми в расширении «Читать вопросы».'],
    disconnected: ['Связь потеряна', 'Проверь, включено ли расширение QuizLens. После обновления расширения перезагрузи эту страницу.']
  };
  let active = false;
  let awaitingFrame = false;
  let modern = false;
  let paired = false;
  let title = '';
  let deadline = null;
  let timer = null;

  function display(state) {
    const [label, instruction] = messages[state];
    const reading = ['waiting', 'partial', 'multiple', 'question'].includes(state);
    const text = reading && title ? 'Вкладка «' + title + '». ' + instruction : instruction;
    if (status.dataset.state !== state) {
      status.dataset.state = state;
      status.textContent = label;
    }
    if (hint.textContent !== text) hint.textContent = text;
    install.hidden = !['checking', 'undetected', 'disconnected'].includes(state);
    stop.hidden = !reading;
  }

  function cancelDeadline() {
    window.clearTimeout(deadline);
    deadline = null;
  }

  function stopReading() {
    if (active) window.quizLensTabCapture?.stop();
    active = false;
    awaitingFrame = false;
    title = '';
    if (document.hidden) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  function probe() {
    if ((document.hidden && !active) || deadline !== null) return;
    deadline = window.setTimeout(() => {
      deadline = null;
      // Previous releases announce binding and send frames only when content changes.
      if (!modern && (paired || active)) return;
      if (modern) {
        stopReading();
        paired = false;
        display('disconnected');
        return;
      }
      const userAgent = navigator.userAgent;
      const unsupported = /Firefox\/|iPhone|iPad|Android|Version\/.+Safari\//.test(userAgent);
      display(unsupported ? 'unsupported' : 'undetected');
    }, 2500);
    window.dispatchEvent(new Event('quizlens-extension-probe'));
  }

  function readingState(reason) {
    return ['partial', 'multiple', 'question'].includes(reason) ? reason : 'waiting';
  }

  window.addEventListener('quizlens-extension', event => {
    if (typeof event.detail !== 'string' || event.detail.length > 48000) return;
    let data;
    try { data = JSON.parse(event.detail); } catch { return; }
    const adapter = window.quizLensTabCapture;
    if (!adapter) return;

    if (data?.type === 'presence') {
      if (typeof data.paired !== 'boolean' || typeof data.reading !== 'boolean') return;
      cancelDeadline();
      modern = true;
      paired = data.paired;
      if (paired && data.reading) {
        if (typeof data.sourceTitle === 'string') title = data.sourceTitle.replace(/\s+/g, ' ').trim().slice(0, 100);
        awaitingFrame = !active;
        display(readingState(data.reason));
      } else {
        const stopped = active || awaitingFrame || status.dataset.state === 'stopped';
        stopReading();
        display(paired ? (stopped ? 'stopped' : 'paired') : 'available');
      }
    } else if (data?.type === 'bound') {
      cancelDeadline();
      paired = true;
      display('paired');
    } else if (data?.type === 'started') {
      cancelDeadline();
      paired = true;
      active = true;
      awaitingFrame = false;
      title = typeof data.sourceTitle === 'string' ? data.sourceTitle.slice(0, 100) : '';
      adapter.start();
      schedule();
      display('waiting');
    } else if (data?.type === 'frame' && (active || awaitingFrame)) {
      const frame = data.frame;
      if (!Array.isArray(frame?.texts) || frame.texts.length > 9) return;
      if (frame.texts.length && (frame.texts.length < 3 || !['multiple', 'single'].includes(frame.selectionMode) ||
          frame.texts.some((value, i) => typeof value !== 'string' || !value.trim() || value.length > (i ? 3000 : 6000)))) return;
      cancelDeadline();
      if (!active) {
        active = true;
        awaitingFrame = false;
        adapter.start();
        schedule();
      }
      display(frame.texts.length ? 'question' : readingState(frame.reason));
      adapter.read(frame.texts, {selectionMode:frame.selectionMode, expectedCount:null});
    } else if (data?.type === 'stopped' || data?.type === 'unbound') {
      cancelDeadline();
      paired = data.type === 'stopped';
      stopReading();
      display(paired ? 'stopped' : 'available');
    }
  });

  window.addEventListener('quizlens-tab-disconnect', () => {
    stopReading();
    display(paired ? 'stopped' : 'available');
  });
  stop.addEventListener('click', () => {
    window.dispatchEvent(new Event('quizlens-tab-disconnect'));
    document.querySelector('#tabReaderSetup > summary').focus();
  });

  // Keep the floating answer in sync even when the quiz covers the capture tab.
  function schedule() {
    if (timer !== null || (document.hidden && !active)) return;
    timer = window.setInterval(probe, document.hidden ? 12000 : 5000);
  }
  function watch() {
    window.clearInterval(timer);
    timer = null;
    cancelDeadline();
    schedule();
    probe();
  }
  window.addEventListener('focus', probe);
  window.addEventListener('pageshow', watch);
  window.addEventListener('pagehide', () => { window.clearInterval(timer); cancelDeadline(); });
  document.addEventListener('visibilitychange', watch);
  watch();
})();
