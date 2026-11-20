'use strict';

// A shared lock protects work even when another QuizLens tab discovers an update.
(() => {
  const name = 'quizlens.active-session';
  const watched = (document.body.dataset.updateBusy || '').split(',').filter(Boolean).map(selector => {
    const element = document.getElementById(selector.match(/^#([\w-]+)/)[1]);
    return {element, selector};
  }).filter(item => item.element);
  const busy = () => watched.some(({element, selector}) => element.matches(selector));
  let lease;
  function sync(closing = false) {
    if (!navigator.locks) return;
    if (closing || !busy()) {
      if (lease) { lease.abort.abort(); lease.release(); lease = null; }
    } else if (!lease) {
      const abort = new AbortController();
      let release;
      const pending = new Promise(resolve => { release = resolve; });
      lease = {abort, release};
      navigator.locks.request(name, {mode:'shared', signal:abort.signal}, () => pending)
        .catch(() => { /* Navigation or the end of the session cancels a queued lock. */ });
    }
  }
  const observer = new MutationObserver(() => sync());
  for (const {element} of watched) {
    observer.observe(element, {attributes:true, attributeFilter:['class','open','hidden','disabled','data-status']});
  }
  sync();
  window.addEventListener('pagehide', () => sync(true));
  window.addEventListener('pageshow', () => sync());
  window.quizLensUpdateActivity = {
    busy,
    async whenIdle(action, manual = false) {
      if (busy() || document.visibilityState !== 'visible') return false;
      if (!navigator.locks) {
        if (!manual) return false;
        await action();
        return true;
      }
      return navigator.locks.request(name, {ifAvailable:true}, async lock => {
        if (!lock || busy() || document.visibilityState !== 'visible') return false;
        await action();
        return true;
      });
    }
  };
})();
