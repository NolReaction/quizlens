'use strict';

(() => {
  if (!['localhost','127.0.0.1','[::1]'].includes(location.hostname)) return;
  const status = document.querySelector('#tabReaderStatus');
  const stop = document.querySelector('#stopTabReader');
  if (!status || !stop) return;
  let active = false;

  window.addEventListener('quizlens-extension', event => {
    if (typeof event.detail !== 'string' || event.detail.length > 48000 || !window.quizLensTabCapture) return;
    let data;
    try { data=JSON.parse(event.detail); } catch { return; }
    const adapter=window.quizLensTabCapture;
    if (data?.type==='bound') {
      status.textContent='QuizLens подключён к расширению. В квизе нажми значок расширения и выбери «Читать вопросы».';
    } else if (data?.type==='started') {
      active=true; stop.hidden=false;
      adapter.start();
      status.textContent='Читаю вкладку. Жду вопрос с вариантами.';
    } else if (data?.type==='frame' && active) {
      const frame=data.frame;
      if (!Array.isArray(frame?.texts) || frame.texts.length>9) return;
      if (frame.texts.length && (frame.texts.length<3 || !['multiple','single'].includes(frame.selectionMode) ||
          frame.texts.some((value,i)=>typeof value!=='string'||!value.trim()||value.length>(i?3000:6000)))) return;
      if (frame.texts.length) {
        status.textContent='Вопрос и варианты прочитаны из вкладки.';
      } else {
        const hints={
          partial:'Покажи вопрос и все его варианты одновременно либо выбери захват окна.',
          multiple:'Вижу несколько групп ответов. Оставь в кадре одну или выбери захват окна.',
          waiting:'Жду вопрос с вариантами. Для квиза на картинке нажми «Выбрать окно».'
        };
        status.textContent=hints[frame.reason] || hints.waiting;
      }
      adapter.read(frame.texts,{selectionMode:frame.selectionMode,expectedCount:null});
    } else if (data?.type==='stopped' || data?.type==='unbound') {
      if (active) adapter.stop();
      active=false; stop.hidden=true;
      status.textContent=data.type==='unbound' ? 'Расширение отключено.' : 'Чтение вкладки остановлено.';
    }
  });
  // A source change in the host UI takes immediate precedence over an in-flight extension snapshot.
  window.addEventListener('quizlens-tab-disconnect', () => {
    active=false; stop.hidden=true;
    status.textContent='Чтение вкладки остановлено.';
  });
  stop.addEventListener('click', () => {
    if (active) window.quizLensTabCapture?.stop();
    window.dispatchEvent(new Event('quizlens-tab-disconnect'));
  });
})();
