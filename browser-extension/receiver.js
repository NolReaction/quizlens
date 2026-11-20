// Installed in the isolated world of the local page explicitly paired by the user.
(() => {
  if (location.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname) ||
      !['/', '/index.html'].includes(location.pathname) || !document.querySelector('#tabReaderStatus')) return false;
  if (globalThis.quizLensReceiverInstalled) return true;
  globalThis.quizLensReceiverInstalled = true;
  let receiverKey;
  function publish(message) {
    window.dispatchEvent(new CustomEvent('quizlens-extension', {detail: JSON.stringify(message)}));
  }
  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (sender.id !== chrome.runtime.id || message?.to !== 'receiver') return;
    if (message.type === 'bound') receiverKey = message.key;
    if (!receiverKey || message.key !== receiverKey) return;
    publish({type: message.type, frame: message.frame});
    if (message.type === 'unbound') receiverKey = null;
    reply({ok: true});
  });
  window.addEventListener('quizlens-tab-disconnect', () => {
    if (receiverKey) chrome.runtime.sendMessage({command:'receiver-stop', key:receiverKey}).catch(() => {});
  });
  return true;
})();
