'use strict';

// The recipient page accepts one frame from its own opener. Keep it in memory
// only while the new page is loading, and only after the user opens the tool.
function openQuizVisual(makeFrame) {
  const tab = window.open('/visual#capture', '_blank');
  if (!tab) return false;
  const frame = Promise.resolve().then(makeFrame).catch(() => null);
  const timer = window.setTimeout(disconnect, 20000);
  function disconnect() {
    window.clearTimeout(timer);
    window.removeEventListener('message', send);
  }
  async function send(event) {
    if (event.origin !== location.origin || event.source !== tab || event.data?.type !== 'quizlens-visual-ready') return;
    disconnect();
    const value = await frame;
    if ((value?.image || (value?.question && value.options?.length >= 2)) && !tab.closed) tab.postMessage({type:'quizlens-visual-frame', ...value}, location.origin);
  }
  window.addEventListener('message', send);
  return true;
}
