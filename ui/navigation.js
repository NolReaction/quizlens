'use strict';

(() => {
  const nav = document.querySelector('.top .primary-nav');
  const sections = nav.querySelector('.nav-sections');
  const dialog = document.getElementById('appUpdates');
  const menu = document.createElement('details');
  menu.id = 'serviceMenu';
  menu.innerHTML = `
    <summary><span class="nav-toggle-label">Настройки</span><i aria-hidden="true"></i></summary>
    <div class="nav-popover">
      <a id="navSetup" href="/setup#connection">LM Studio</a>
      <a id="navCheck" href="/check">Проверка модели и OCR</a>
      <a id="navUpdates" href="${dialog ? '#appUpdates' : '/#appUpdates'}">Обновления <span id="updateBadge" hidden></span></a>
    </div>`;
  nav.append(menu);
  const toggle = menu.querySelector('summary');
  const label = toggle.querySelector('span');
  const panel = menu.querySelector('.nav-popover');
  panel.inert = true;
  menu.addEventListener('toggle', () => { panel.inert = !menu.open; });
  const updateLink = document.getElementById('navUpdates');
  for (const link of panel.querySelectorAll('a')) {
    if (link !== updateLink && link.pathname === location.pathname) link.setAttribute('aria-current', 'page');
  }

  const compact = matchMedia('(max-width: 660px)');
  function layout() {
    menu.open = false;
    if (compact.matches) panel.prepend(sections);
    else nav.insertBefore(sections, menu);
    label.textContent = compact.matches ? 'Меню' : 'Настройки';
  }
  layout();
  compact.addEventListener('change', layout);

  document.addEventListener('pointerdown', event => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('focusin', event => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      toggle.focus();
      event.preventDefault();
    }
  });
  nav.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (dialog && link === updateLink) {
      event.preventDefault();
      if (location.hash !== link.hash) history.pushState({quizLensUpdates: true}, '', link.hash);
      showUpdates();
    } else {
      link.target = window.quizLensUpdateActivity?.busy() ? '_blank' : '_self';
      link.rel = 'noopener';
      if (menu.open) { menu.open = false; toggle.focus(); }
    }
  });

  function showUpdates() {
    menu.open = false;
    toggle.focus({preventScroll: true});
    if (!dialog.open) dialog.showModal();
  }
  if (dialog) {
    function syncDialog() {
      if (location.hash === '#appUpdates') showUpdates();
      else if (dialog.open) dialog.close();
    }
    dialog.addEventListener('close', () => {
      if (location.hash === '#appUpdates') {
        if (history.state?.quizLensUpdates) history.back();
        else history.replaceState(history.state, '', location.pathname + location.search);
      }
      toggle.focus({preventScroll: true});
    });
    window.addEventListener('hashchange', syncDialog);
    syncDialog();
  }
})();
