// Only the local capture page gets an automatic status listener. Binding and reading require a toolbar action.
(() => {
  if (location.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname) ||
      !['/', '/index.html'].includes(location.pathname) || !document.querySelector('#sessionView #tabReaderStatus')) return false;
  if (globalThis.quizLensReceiverInstalled) return true;
  globalThis.quizLensReceiverInstalled = true;
  let receiverKey;
  let generation = 0;
  function publish(message) {
    window.dispatchEvent(new CustomEvent('quizlens-extension', {detail:JSON.stringify(message)}));
  }
  async function presence(first) {
    const initial = generation;
    try {
      const state = await chrome.runtime.sendMessage({command:'presence', key:receiverKey, first});
      if (state?.type === 'presence' && generation === initial) publish(state);
    } catch { /* An extension reload or disable invalidates this isolated world. */ }
  }
  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (sender.id !== chrome.runtime.id || message?.to !== 'receiver') return;
    if (message.type === 'available') { reply({ok:true}); return; }
    if (message.type === 'bound') receiverKey = message.key;
    if (!receiverKey || message.key !== receiverKey) return;
    if (message.type !== 'ping') {
      ++generation;
      publish({type:message.type, frame:message.frame, sourceTitle:message.sourceTitle});
      if (message.type === 'unbound') receiverKey = null;
    }
    reply({ok:true});
  });
  window.addEventListener('quizlens-extension-probe', () => presence(false));
  window.addEventListener('pageshow', event => { if (event.persisted) presence(true); });
  window.addEventListener('quizlens-tab-disconnect', async () => {
    if (receiverKey) {
      try { await chrome.runtime.sendMessage({command:'receiver-stop', key:receiverKey}); }
      catch { /* The extension may have been disabled while the page was open. */ }
    }
  });
  presence(true);
  return true;
})();
