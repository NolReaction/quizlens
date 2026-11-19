'use strict';

const files = {mac: 'QuizLens-Mac.zip', windows: 'QuizLens-Windows.zip'};
const labels = {mac: 'Mac', windows: 'Windows x64'};
const deviceNotes = {
  mac: 'Для этого Mac подойдёт сборка macOS. Требуется macOS 14 или новее.',
  windows: 'Для этого ПК подойдёт сборка Windows x64.',
  windowsUnknown: 'Похоже, у тебя Windows. Для сборки нужен процессор x64. Тип системы указан в параметрах Windows в разделе «Система → О системе».',
  windowsOther: 'Для компьютеров с Windows ARM и 32-разрядных систем отдельной сборки пока нет. Ниже — архив для Windows x64.',
  mobile: 'QuizLens работает на компьютере. Скопируй ссылку, чтобы открыть её на Mac или ПК.',
  linux: 'Готовой сборки для Linux пока нет. На GitHub доступны исходники.',
  unknown: 'Выбери систему компьютера, на котором будешь проходить квиз.'
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
  let release = null;

  function renderDevice() {
    get('device-note').textContent = deviceNotes[device];
    for (const system of Object.keys(files)) {
      const card = get(system + '-card');
      const preferred = device === system;
      card.classList.toggle('recommended', preferred);
      card.querySelector('.match').hidden = !preferred;
    }
    const hero = get('hero-action');
    if (device === 'mac' && release?.mac && release.macStatus !== 'notarized') {
      hero.href = '#mac-card';
      hero.textContent = 'Посмотреть сборку для Mac ↓';
    } else if (release && release[device]) {
      hero.href = release[device];
      hero.textContent = 'Скачать для ' + labels[device] + ' ↓';
    } else {
      hero.href = '#download';
      hero.textContent = 'Выбрать сборку ↓';
    }
  }

  function renderRelease(result) {
    release = result;
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

  function openSection() {
    const id = location.hash.slice(1);
    if (id === 'mac-install' || id === 'windows-install') get(id).open = true;
  }
  openSection();
  doc.defaultView?.addEventListener('hashchange', openSection);

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
    get('sign-info').textContent = 'Статус подписи можно уточнить в описании выпуска.';
  }).finally(() => clearTimeout(timer));
}

if (typeof document !== 'undefined') enablePage(document, navigator, window.location, window.fetch.bind(window));
if (typeof module !== 'undefined') module.exports = {detectDevice, parseRelease, enablePage};
