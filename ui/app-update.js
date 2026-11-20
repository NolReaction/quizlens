'use strict';

(() => {
  const byId = id => document.getElementById(id);
  const action = byId('updateAction');
  const choice = byId('automaticUpdates');
  const message = byId('updateMessage');
  const activity = window.quizLensUpdateActivity;
  let snapshot, pageVersion, restartingTo, timer;
  let pending = false, finishInstall = false, reloading = false;
  let activationAt = 0, pausedVersion;

  async function request(path, data) {
    const abort = new AbortController();
    const expires = setTimeout(() => abort.abort(), 10000);
    try {
      const options = data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {};
      const response = await fetch(path, {cache:'no-store', signal:abort.signal, ...options});
      const result = await response.json();
      if (!response.ok) throw Error(result.message || result.error || 'Сбой локального сервера.');
      return result;
    } finally { clearTimeout(expires); }
  }

  function draw(status) {
    const {currentVersion, latestVersion, automatic, supported, state} = status;
    const offered = ['available','ready'].includes(state);
    const working = ['checking','downloading','restarting'].includes(state);
    const label = {available:'Обновить QuizLens', ready:'Применить обновление', checking:'Проверяю…',
      downloading:'Скачиваю…', restarting:'Устанавливаю…'};
    byId('installedVersion').textContent = currentVersion ? 'v' + currentVersion : '';
    byId('updateBadge').textContent = offered && latestVersion ? 'v' + latestVersion : '';
    byId('updateBadge').hidden = !offered || !latestVersion;
    byId('automaticLabel').hidden = !supported;
    byId('manualRelease').hidden = !!supported;
    action.hidden = !supported;
    action.disabled = !supported || working || !!restartingTo;
    choice.disabled = !supported || state === 'restarting' || !!restartingTo;
    choice.checked = !!automatic;
    action.textContent = label[state] || 'Проверить обновления';
    if (message.textContent !== status.message) message.textContent = status.message || 'Ожидаю сведений об обновлении.';
  }

  function reloadFor(version) {
    if (!version || !pageVersion || version === pageVersion) return false;
    if (activity.busy()) {
      message.textContent = 'QuizLens обновился. Страница откроется заново после завершения захвата.';
      return false;
    }
    reloading = true;
    location.reload();
    return true;
  }

  async function advance(manual = false) {
    const ready = snapshot.state === 'ready';
    const next = ready ? 'activate' : 'apply';
    const accepted = await activity.whenIdle(async () => {
      // Remember the target before activation: the supervisor may replace our connection immediately.
      if (ready) { restartingTo = snapshot.latestVersion; activationAt = Date.now(); }
      snapshot = await request('/api/update', {action:next});
      if (ready && snapshot.state !== 'restarting') restartingTo = null;
      if (snapshot.state === 'error' || snapshot.state === 'unsupported') {
        restartingTo = null; finishInstall = false;
      }
      draw(snapshot);
    }, manual);
    if (!accepted && document.visibilityState === 'visible') {
      message.textContent = navigator.locks
        ? 'Обновление готово к следующему шагу. Оно продолжится, когда завершится занятие или захват.'
        : 'Обновление доступно. Заверши работу в других вкладках QuizLens и нажми кнопку обновления.';
    }
  }

  async function refresh(command, automatic) {
    if (pending || reloading) return;
    pending = true;
    clearTimeout(timer);
    action.disabled = choice.disabled = true;
    let delay = 30000;
    try {
      if (restartingTo) {
        const health = await request('/api/health');
        if (health.app === 'QuizLens' && health.version !== pageVersion) {
          reloadFor(health.version);
          delay = 2000;
          return;
        }
      }
      if (command === 'apply' || command === 'activate') {
        pausedVersion = null;
        finishInstall = true;
        await advance(true);
      } else {
        const data = command ? {action:command, ...(command === 'preferences' ? {automatic} : {})} : null;
        snapshot = await request('/api/update', data);
        pageVersion ||= snapshot.currentVersion;
        if (snapshot.currentVersion !== pageVersion) {
          reloadFor(snapshot.currentVersion);
          delay = 2000;
          return;
        }
        if (snapshot.state === 'error' || snapshot.state === 'unsupported') {
          restartingTo = null; finishInstall = false;
        }
        if (snapshot.state === 'restarting' && !restartingTo) {
          restartingTo = snapshot.latestVersion;
          activationAt = Date.now();
        }
        if (restartingTo && Date.now() - activationAt > 15000 && snapshot.state === 'ready') {
          pausedVersion = restartingTo;
          restartingTo = null;
          finishInstall = false;
          snapshot.message = 'Переключение версии не завершилось. Можно повторить установку.';
        }
        if (snapshot.state === 'current') { finishInstall = false; restartingTo = null; }
        draw(snapshot);
        if (!restartingTo && !command && pausedVersion !== snapshot.latestVersion && (snapshot.automatic || finishInstall) &&
            ['available','ready'].includes(snapshot.state) && document.visibilityState === 'visible') {
          await advance(finishInstall);
        }
      }
      if (restartingTo) {
        message.textContent = 'Применяю обновление. Страница откроется заново автоматически.';
      }
      if (restartingTo || ['checking','downloading','restarting','ready','available'].includes(snapshot.state)) delay = 2000;
    } catch (error) {
      if (snapshot) draw(snapshot);
      action.disabled = !!restartingTo;
      message.textContent = restartingTo ? 'QuizLens запускает новую версию. Восстанавливаю соединение…' :
        'Не удалось проверить обновление. ' + (error.message || 'Проверь соединение с приложением.');
      if (!snapshot) { action.hidden = false; byId('manualRelease').hidden = false; }
      delay = restartingTo ? 2000 : 15000;
    } finally {
      pending = false;
      if (!reloading) timer = setTimeout(refresh, delay);
    }
  }

  action.addEventListener('click', () => {
    const next = {available:'apply', ready:'activate'};
    refresh(next[snapshot?.state] || 'check');
  });
  choice.addEventListener('change', () => {
    if (!choice.checked) finishInstall = false;
    refresh('preferences', choice.checked);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh();
  });
  refresh();
})();
