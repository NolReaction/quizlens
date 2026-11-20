'use strict';

const files = {mac: 'QuizLens-Mac.zip', windows: 'QuizLens-Windows.zip'};
const labels = {mac: 'Mac', windows: 'Windows x64'};
const deviceNotes = {
  mac: 'Для твоего Mac подойдёт сборка macOS.',
  windows: 'Для твоего ПК подойдёт сборка Windows x64.',
  windowsUnknown: 'Похоже, у тебя Windows. Для сборки нужен процессор x64. Тип системы указан в параметрах Windows в разделе «Система → О системе».',
  windowsOther: 'Для компьютеров с Windows ARM и 32-разрядных систем отдельной сборки пока нет. Ниже — архив для Windows x64.',
  mobile: 'QuizLens работает на компьютере. Скопируй ссылку, чтобы открыть её на Mac или ПК.',
  linux: 'Готовой сборки для Linux пока нет. Исходный код доступен на GitHub.',
  unknown: 'Выбери систему своего компьютера.'
};

function detectDevice(info, hints = {}) {
  const agent = info.userAgent || '';
  const platform = hints.platform || info.userAgentData?.platform || info.platform || '';
  if (info.userAgentData?.mobile || /android|iphone|ipad|ipod/i.test(agent)
      || (/mac/i.test(platform) && info.maxTouchPoints > 1)) return 'mobile';
  if (/cros/i.test(agent) || /chrome.?os/i.test(platform)) return 'unknown';
  if (/win/i.test(platform) || /windows/i.test(agent)) {
    const arch = (hints.architecture || '').toLowerCase();
    if (arch === 'arm' || /arm64|aarch64|windows (?:rt|arm)/i.test(agent)) return 'windowsOther';
    if (arch === 'x86' && (hints.bitness === '64' || hints.wow64)) return 'windows';
    if (hints.bitness === '32' && !hints.wow64) return 'windowsOther';
    if (/win64|wow64|x86_64|amd64/i.test(agent)) return 'windows';
    return 'windowsUnknown';
  }
  if (/mac/i.test(platform) || /macintosh/i.test(agent)) return 'mac';
  if (/linux/i.test(platform) || /linux/i.test(agent)) return 'linux';
  return 'unknown';
}

function parseRelease(data, repoURL) {
  if (!data || typeof data.tag_name !== 'string' || !data.tag_name
      || data.draft || data.prerelease || !Array.isArray(data.assets)) {
    throw new Error('Некорректный ответ GitHub');
  }
  const tag = data.tag_name;
  const listed = new Set(data.assets.filter(asset => asset && asset.state === 'uploaded' && asset.size > 0)
    .map(asset => asset.name));
  const releaseURL = repoURL + '/releases/tag/' + encodeURIComponent(tag);
  const download = name => listed.has(name) ? repoURL + '/releases/download/' + encodeURIComponent(tag) + '/' + name : null;
  const body = typeof data.body === 'string' ? data.body : '';
  const notarized = body.includes('<!-- quizlens-macos: notarized -->');
  const unsigned = body.includes('<!-- quizlens-macos: unsigned -->');
  return {
    tag, releaseURL, guide: download('QuizLens-Guide.md'), checksums: download('SHA256SUMS.txt'),
    mac: download(files.mac), windows: download(files.windows),
    macStatus: notarized === unsigned ? 'unknown' : notarized ? 'notarized' : 'unsigned'
  };
}

function enablePage(doc, browser, location, fetchRelease) {
  const get = id => doc.getElementById(id);
  const repo = doc.body.dataset.repository;
  const repoURL = 'https://github.com/' + repo;
  let device = detectDevice(browser);

  function renderDevice() {
    get('device-note').textContent = deviceNotes[device];
    for (const system of Object.keys(files)) {
      const card = get(system + '-card');
      const preferred = device === system;
      card.classList.toggle('recommended', preferred);
      card.querySelector('.match').hidden = !preferred;
    }
  }

  function renderRelease(result) {
    for (const system of Object.keys(files)) {
      const button = get(system + '-download');
      const url = result && result[system];
      button.hidden = !url;
      if (url) {
        button.href = url;
        if (system === 'mac' && result.macStatus === 'unsigned') {
          button.textContent = 'Скачать без нотарификации ↓';
        } else {
          button.textContent = 'Скачать для ' + labels[system] + ' ↓';
        }
      }
      get(system + '-info').textContent = url
        ? result.tag + ' · ' + files[system]
        : 'Архив для этой системы пока не опубликован.';
    }
    const signLabels = {
      notarized: 'Выпуск подписан Developer ID и прошёл нотарификацию Apple.',
      unsigned: 'Выпуск без нотарификации Apple. macOS может заблокировать запуск.',
      unknown: 'Статус подписи в этом выпуске не указан.'
    };
    const sign = get('sign-info');
    sign.hidden = !result?.mac;
    if (result?.mac) sign.textContent = signLabels[result.macStatus];
    const status = get('release-status');
    if (!result) {
      status.textContent = 'Первый публичный выпуск пока не опубликован.';
    } else {
      status.textContent = 'Выпуск ' + result.tag + '. ';
      const links = [[result.releaseURL, 'Описание выпуска'], [result.guide, 'Скачать инструкцию'],
        [result.checksums, 'Контрольные суммы']];
      let first = true;
      for (const [href, label] of links) {
        if (!href) continue;
        if (!first) status.append(' · ');
        const anchor = doc.createElement('a');
        anchor.href = href;
        anchor.textContent = label;
        status.append(anchor);
        first = false;
      }
    }
    renderDevice();
  }

  renderDevice();
  const hintSource = browser.userAgentData;
  if ((device === 'windowsUnknown' || device === 'windows' || device === 'windowsOther')
      && typeof hintSource?.getHighEntropyValues === 'function') {
    hintSource.getHighEntropyValues(['architecture', 'bitness', 'wow64']).then(hints => {
      device = detectDevice(browser, hints);
      renderDevice();
    }).catch(() => {});
  }

  const share = get('share');
  share.hidden = false;
  share.addEventListener('click', async () => {
    const link = new URL(location.href);
    link.search = '';
    link.hash = '';
    link.pathname = link.pathname.replace(/\/index\.html$/, '/');
    try {
      await browser.clipboard.writeText(link.href);
      get('share-message').textContent = 'Ссылка на страницу загрузки скопирована.';
    } catch {
      get('share-message').textContent = 'Ссылка для пересылки — ' + link.href;
    }
  });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4500);
  return fetchRelease('https://api.github.com/repos/' + repo + '/releases/latest', {
    credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal
  }).then(async response => {
    if (response.status === 404) return renderRelease(null);
    if (!response.ok) throw new Error('GitHub недоступен');
    renderRelease(parseRelease(await response.json(), repoURL));
  }).catch(() => {
    get('release-status').textContent = 'Сейчас не удалось проверить последний выпуск. Архивы можно посмотреть в списке выпусков GitHub.';
    get('sign-info').textContent = 'Сведения о подписи указаны в выпуске.';
  }).finally(() => clearTimeout(timer));
}

function enableNavigation(doc, view) {
  const button = doc.getElementById('nav-toggle');
  const nav = doc.getElementById('site-nav');
  const links = Array.from(nav.querySelectorAll('a'));
  const sections = links.map(link => doc.getElementById(link.hash.slice(1)));
  const compact = view.matchMedia('(max-width: 740px)');
  const motion = view.matchMedia('(prefers-reduced-motion: no-preference)');
  const closedPanel = {opacity:'0', transform:'translateY(-5px) scale(.98)'};
  const shownPanel = {opacity:'1', transform:'none'};
  let animation;

  function setOpen(open, animate = true) {
    const hide = compact.matches && !open;
    button.hidden = !compact.matches;
    button.setAttribute('aria-expanded', String(open && compact.matches));
    if (animate && hide === nav.inert && (animation || hide === nav.hidden)) return;
    nav.inert = hide;

    // Sample the current visual state before cancelling so another tap can reverse it.
    const transition = animate && compact.matches && motion.matches && typeof nav.animate === 'function';
    let from = closedPanel;
    if (transition && !nav.hidden) {
      const current = view.getComputedStyle(nav);
      from = {opacity:current.opacity, transform:current.transform};
    }
    animation?.cancel();
    animation = null;
    if (!transition) {
      nav.hidden = hide;
      return;
    }
    nav.hidden = false;
    const next = nav.animate([from, hide ? closedPanel : shownPanel], {
      duration:hide ? 135 : 180, easing:'cubic-bezier(.2,.8,.2,1)'
    });
    animation = next;
    next.onfinish = () => {
      if (animation !== next) return;
      nav.hidden = hide;
      animation = null;
    };
  }
  function closeOnEscape(event) {
    if (event.key === 'Escape' && compact.matches && !nav.inert) {
      setOpen(false);
      button.focus();
    }
  }
  compact.addEventListener('change', () => setOpen(false, false));
  motion.addEventListener('change', () => setOpen(!nav.inert, false));
  button.addEventListener('click', () => setOpen(nav.inert));
  doc.addEventListener('keydown', closeOnEscape);
  function closeOutside(event) {
    if (!nav.contains(event.target) && !button.contains(event.target)) setOpen(false);
  }
  doc.addEventListener('click', closeOutside);
  doc.addEventListener('focusin', closeOutside);
  links.forEach((link, index) => link.addEventListener('click', () => {
    if (compact.matches) {
      setOpen(false);
      sections[index].focus({preventScroll: true});
    }
  }));
  function indicateSection() {
    const top = doc.querySelector('.bar').offsetHeight + 24;
    let active = 0;
    sections.forEach((section, index) => {
      if (section.getBoundingClientRect().top <= top) active = index;
    });
    if (Math.ceil(view.scrollY + view.innerHeight) >= doc.documentElement.scrollHeight) active = sections.length - 1;
    links.forEach((link, index) => {
      if (index === active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  let scheduled = false;
  function onScroll() {
    if (scheduled) return;
    scheduled = true;
    view.requestAnimationFrame(() => {
      scheduled = false;
      indicateSection();
    });
  }
  view.addEventListener('scroll', onScroll, {passive:true});
  view.addEventListener('resize', onScroll);
  setOpen(false, false);
  indicateSection();
}

if (typeof document !== 'undefined') {
  enableNavigation(document, window);
  enablePage(document, navigator, window.location, window.fetch.bind(window));
}
if (typeof module !== 'undefined') module.exports = {detectDevice, parseRelease, enablePage, enableNavigation};
