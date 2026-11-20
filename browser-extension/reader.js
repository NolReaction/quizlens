// Runs only in the isolated world of the top-level quiz tab selected by the user.
(() => {
  if (globalThis.quizLensReadVisibleQuestion) return;
  const controls = 'input[type=radio], input[type=checkbox], [role=radio], [role=checkbox]';
  const excluded = '[hidden],[inert],[aria-hidden="true"],[role=status],[role=alert],script,style,template,noscript';
  const promptSelector = 'legend,.qtext,.question-text,[data-question-text],h1,h2,h3,h4,h5,h6,[role=heading]';
  const groupSelector = 'fieldset,[role=radiogroup],[role=group],.que,.quiz-question,article,section,form';
  const compact = value => value.replace(/\s+/g,' ').trim();

  function painted(el) {
    if (!el?.getClientRects().length || el.closest(excluded)) return false;
    if (el.checkVisibility) return el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
    for (let node=el; node; node=node.parentElement) {
      const css = getComputedStyle(node);
      if (css.visibility !== 'visible' || css.opacity === '0') return false;
    }
    return true;
  }
  function onScreen(el, entire=false) {
    const b = el.getBoundingClientRect();
    if (b.width <= 1 || b.height <= 1 || b.bottom <= 0 || b.right <= 0 || b.top >= innerHeight || b.left >= innerWidth) return false;
    if (entire && (b.top < -1 || b.left < -1 || b.bottom > innerHeight+1 || b.right > innerWidth+1)) return false;
    // Scroll panes can hide options even while their boxes intersect the document viewport.
    for (let p=el.parentElement; p && p !== document.documentElement; p=p.parentElement) {
      const r=p.getBoundingClientRect(), css=getComputedStyle(p);
      if (/auto|scroll|hidden|clip/.test(css.overflowY) &&
          (entire ? b.top < r.top-1 || b.bottom > r.bottom+1 : b.bottom <= r.top || b.top >= r.bottom)) return false;
      if (/auto|scroll|hidden|clip/.test(css.overflowX) &&
          (entire ? b.left < r.left-1 || b.right > r.right+1 : b.right <= r.left || b.left >= r.right)) return false;
    }
    return true;
  }
  function visibleText(el) {
    if (!painted(el)) return '';
    const chunks = [];
    function append(node, depth) {
      if (chunks.length > 600 || depth > 40) return;
      if (node.nodeType === Node.TEXT_NODE) { chunks.push(node.data); return; }
      if (node.nodeType !== Node.ELEMENT_NODE || !painted(node) ||
          (node.matches('textarea,input,select,[contenteditable=true],svg,button') && node !== el)) return;
      if (node.tagName === 'BR') { chunks.push(' '); return; }
      const block = /^(block|list-item|table-row|grid|flex)$/.test(getComputedStyle(node).display);
      if (block) chunks.push(' ');
      for (const child of node.childNodes) append(child,depth+1);
      if (block) chunks.push(' ');
    }
    append(el,0);
    return chunks.length > 600 ? '' : compact(chunks.join(''));
  }
  const disabled = el => el.matches(':disabled') || !!el.closest('[aria-disabled=true]');
  const precedes = (a,b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  function namedElements(el) {
    return (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)).filter(painted);
  }
  function option(el) {
    if (el.closest('[hidden],[inert],[aria-hidden=true]')) return null;
    const labels = [...(el.labels || [])].filter(painted);
    const references = namedElements(el);
    const anchor = labels[0] || references[0] || (painted(el) ? el : null);
    if (!anchor) return null;
    const label = compact(references.map(visibleText).join(' ') || labels.map(visibleText).join(' ') ||
      visibleText(el) || (painted(el) ? el.getAttribute('aria-label') || '' : ''));
    return {el, anchor, label, disabled:disabled(el)};
  }
  function question(group, items) {
    const labelled = namedElements(group).filter(el => onScreen(el,true)).map(visibleText).filter(Boolean).join(' ');
    const generic = /^(?:answers?|options?|choices?|варианты(?: ответ(?:а|ов))?|ответы)$/i;
    if (labelled && !generic.test(labelled)) return labelled;
    const first = items[0].el;
    // A group heading or the nearest preceding heading inside one question is the usual accessible prompt.
    for (let scope=group; scope; scope=scope.parentElement?.closest(groupSelector)) {
      const candidates=[...scope.querySelectorAll(promptSelector)].filter(el =>
        precedes(el,first) && painted(el) && !generic.test(visibleText(el)) && !el.closest('label,button'));
      const heading=candidates.at(-1);
      if (heading) return onScreen(heading,true) ? visibleText(heading) : '';
    }
    return '';
  }
  function frame(question, items, selectionMode) {
    if (items.length < 2 || items.length > 8 || items.some(x => !onScreen(x.anchor,true))) return {texts:[],reason:'partial'};
    items.sort((a,b) => {
      const ra=a.anchor.getBoundingClientRect(), rb=b.anchor.getBoundingClientRect();
      // Same visual row in a grid; use the reading direction of the page.
      if (Math.abs(ra.top-rb.top) < Math.min(ra.height,rb.height)*.45) {
        return getComputedStyle(a.anchor).direction === 'rtl' ? rb.right-ra.right : ra.left-rb.left;
      }
      return ra.top-rb.top;
    });
    const texts=[question,...items.map(item=>item.label)];
    if (texts.some((x,i) => !x || x.length>(i?3000:6000)) || new Set(texts.slice(1)).size !== items.length) {
      return {texts:[],reason:'partial'};
    }
    return {texts, selectionMode};
  }
  function read() {
    // QuizLens's practice page uses plain buttons for single choice. Read its rendered labels.
    const demo = document.querySelector('#round[aria-label] #options');
    if (demo && document.querySelector('#round #question')) {
      const items=[...demo.querySelectorAll('button')].filter(btn => painted(btn) && !disabled(btn));
      const title=document.querySelector('#round #question');
      if (!items.length || !painted(title)) return {texts:[],reason:'waiting'};
      return frame(onScreen(title,true) ? visibleText(title) : '',items.map(el =>
        ({el,anchor:el,label:visibleText(el.querySelector('.option-text') || el)})),
        items.every(b => b.getAttribute('role') === 'checkbox') ? 'multiple':'single');
    }
    const groups = [];
    const nodes=document.querySelectorAll(controls);
    if (nodes.length>256) return {texts:[],reason:'multiple'};
    for (const el of nodes) {
      if (el.parentElement?.closest(controls) || el.closest('form')?.querySelector('input[type=password]')) continue;
      const item=option(el);
      if (!item) continue;
      const scope=el.closest(groupSelector) || document.body;
      const mode=(el.type === 'checkbox' || el.getAttribute('role') === 'checkbox') ? 'multiple' : 'single';
      const name=el.type === 'radio' ? el.name : '';
      let group=groups.find(g => g.scope===scope && g.mode===mode && g.name===name);
      if (!group) { group={scope,mode,name,items:[]}; groups.push(group); }
      group.items.push(item);
    }
    const shown=groups.filter(g => g.items.length>=2 && g.items.some(item => !item.disabled && onScreen(item.anchor)));
    if (shown.length !== 1) return {texts:[],reason:shown.length?'multiple':'waiting'};
    const g=shown[0];
    if (g.items.some(item => item.disabled)) return {texts:[],reason:'partial'};
    return frame(question(g.scope,g.items),g.items,g.mode);
  }

  globalThis.quizLensReadVisibleQuestion = read;
  let observer, timer, previous='', key;
  async function snapshot() {
    timer=null;
    if (!key) return;
    if (!chrome.runtime.id) { stop(); return; }
    const sessionKey=key;
    const frame=read(), signature=JSON.stringify(frame);
    if (signature===previous) return;
    previous=signature;
    try {
      const result=await chrome.runtime.sendMessage({command:'frame',key:sessionKey,frame});
      if (result?.ok===false && key===sessionKey) stop();
    } catch {
      if (key===sessionKey) {
        if (chrome.runtime.id) previous='';
        else stop();
      }
    }
  }
  function schedule() { if (key && !timer) timer=setTimeout(snapshot,220); }
  function stop() {
    key=null;
    observer?.disconnect();
    clearTimeout(timer); timer=null; previous='';
    removeEventListener('resize',schedule);
    removeEventListener('scroll',schedule,true);
  }
  chrome.runtime.onMessage.addListener((message,sender,reply) => {
    if (sender.id !== chrome.runtime.id || message?.to !== 'reader') return;
    if (message.type === 'start') {
      stop();
      key=message.key;
      observer=new MutationObserver(changes => {
        // Timers and progress bars can update dozens of times between questions.
        const noise='[role=timer],[role=progressbar],progress,.progress-track,#clock';
        if (changes.some(change => !(change.target.parentElement && (change.target.nodeType === Node.ELEMENT_NODE ?
            change.target : change.target.parentElement).closest(noise)))) schedule();
      });
      observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,characterData:true});
      addEventListener('resize',schedule);
      addEventListener('scroll',schedule,true);
      schedule();
    } else if (key !== message.key || !key) return;
    else if (message.type==='stop') stop();
    reply({ok:true});
  });
  addEventListener('pagehide',stop);
})();
