"use strict";

const el = (id) => document.getElementById(id);
const fieldCount = () => Number(el("area-count").value) + 1;
const validFields = value => Number.isInteger(value) && value >= 3 && value <= 9;
let regions = [];
let draft = null;
let origin = null;
let snapshotUrl = "";
let snapshotId = "";
let previewVersion = 0;
let token = "";
let busy = false;
let changingMode = false;
let modeVersion = 0;
let resultKey = "";
let previous = loadPreferences();

function loadPreferences() {
  try {
    const main = JSON.parse(localStorage.getItem("quizlens.settings.v1") || "{}") || {};
    const native = JSON.parse(localStorage.getItem("quizlens-native") || "{}") || {};
    return {...native, model:main.model ?? native.model, lectureIds:main.selected ?? native.lectureIds,
      record:main.record ?? native.record};
  } catch { return {}; }
}

function saveSharedSetting(key, value) {
  try {
    const settings = JSON.parse(localStorage.getItem("quizlens.settings.v1") || "{}");
    localStorage.setItem("quizlens.settings.v1", JSON.stringify({...settings, [key]:value}));
  } catch { /* Optional when browser storage is unavailable. */ }
}

function savePreferences(auto) {
  const prefs = {
    ...previous,
    model: el("model").value,
    record: el("record").checked,
    lectureIds: selectedLectures(),
    selectionMode: el("selection-mode").value,
  };
  if (!auto) Object.assign(prefs, {regions: regions.slice(), width: el("still").naturalWidth, height: el("still").naturalHeight});
  previous = prefs;
  try { localStorage.setItem("quizlens-native", JSON.stringify(prefs)); } catch { /* Optional. */ }
}

async function call(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "Не удалось подключиться к QuizLens.");
  }
  return response;
}

async function json(path, payload) {
  const opts = payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  return (await call(path, opts)).json();
}

function selectedLectures() {
  return [...el("lectures").querySelectorAll("input:checked")].map((input) => input.value);
}

async function loadLectures(selected = previous.lectureIds) {
  try {
    const { lectures } = await json("/api/lectures");
    const list = el("lectures");
    list.replaceChildren();
    const groups = [
      ["Твои документы", lectures.filter(item => !item.builtIn)],
      ["Примеры для пробного квиза", lectures.filter(item => item.builtIn)]
    ];
    for (const [name, items] of groups) {
      if (!items.length) continue;
      const heading = document.createElement("p");
      heading.className = "document-group";
      heading.textContent = name;
      list.append(heading);
      for (const lecture of items) {
        const item = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = lecture.id;
        input.checked = !Array.isArray(selected) || selected.includes(lecture.id);
        item.append(input, document.createTextNode(lecture.name));
        list.append(item);
      }
    }
    if (!lectures.length) el("files-info").textContent = "Добавь хотя бы один документ.";
  } catch (error) { el("files-info").textContent = error.message; }
  controls();
}

async function importLectures() {
  const selected = new Set(selectedLectures());
  const files = [...el("files").files];
  const errors = [];
  el("files").disabled = true;
  for (const file of files) {
    if (file.size > 32 * 1024 * 1024) { errors.push(file.name + " больше 32 МБ."); continue; }
    el("files-info").textContent = "Импорт " + file.name + "…";
    try {
      const response = await call("/api/import?name=" + encodeURIComponent(file.name), {
        method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" },
      });
      const result = await response.json();
      selected.add(result.lecture.id);
    } catch (error) { errors.push(file.name + " — " + error.message); }
  }
  saveSharedSetting("selected", [...selected]);
  await loadLectures([...selected]);
  el("files").value = "";
  el("files").disabled = false;
  el("files-info").textContent = errors.join(" ") || "Материалы готовы.";
}

async function loadModels() {
  const wanted = el("model").value || previous.model;
  el("models").disabled = true;
  controls();
  try {
    const { models } = await json("/api/models");
    el("model").replaceChildren(new Option("Карточки и поиск (без модели)", ""));
    for (const name of models) el("model").add(new Option(name, name));
    if (models.includes(wanted)) el("model").value = wanted;
    el("model-info").textContent = models.length ? (el("model").value ? "Модель доступна через локальный сервер." : "Выбери модель для ответов по своим документам.") : "Загрузи модель в LM Studio и включи локальный сервер.";
  } catch (error) {
    el("model").replaceChildren(new Option("Карточки и поиск (без модели)", ""));
    el("model-info").textContent = "Модели недоступны. Можно проверить захват на пробных карточках.";
  } finally { el("models").disabled = false; controls(); }
}

function resetMark() {
  previewVersion++;
  snapshotId = "";
  regions = [];
  draft = origin = null;
  el("mark").hidden = true;
  el("still").removeAttribute("src");
  if (snapshotUrl) URL.revokeObjectURL(snapshotUrl);
  snapshotUrl = "";
  controls();
}

async function loadWindows() {
  el("list").disabled = el("windows").disabled = el("preview").disabled = true;
  resetMark();
  el("window-message").textContent = "Ожидаем список окон от macOS…";
  try {
    const { windows } = await json("/api/native/windows");
    el("windows").replaceChildren(...windows.map((item) =>
      new Option(item.title ? item.app + " · " + item.title : item.app, String(item.id))));
    el("windows").disabled = el("preview").disabled = !windows.length;
    el("window-message").textContent = windows.length
      ? "Выбери окно квиза и нажми «Автозахват». Можно начать, пока на экране идёт ожидание."
      : "Доступных окон нет. Открой квиз и обнови список.";
  } catch (error) {
    el("windows").replaceChildren(new Option("Окно не выбрано", ""));
    el("window-message").textContent = error.message;
  } finally { el("list").disabled = false; controls(); }
}

async function takePreview() {
  resetMark();
  const version = previewVersion;
  const id = el("windows").value;
  el("preview").disabled = true;
  el("window-message").textContent = "Читаем выбранное окно…";
  try {
    const response = await call("/api/native/preview?id=" + encodeURIComponent(id));
    const blob = await response.blob();
    if (version !== previewVersion) return;
    snapshotUrl = URL.createObjectURL(blob);
    el("still").src = snapshotUrl;
    await el("still").decode();
    if (version !== previewVersion) return;
    snapshotId = id;
    el("mark").hidden = false;
    el("window-message").textContent = "Пока идёт квиз, сохрани размер окна и масштаб страницы.";
    controls();
    el("mark").scrollIntoView({ block: "nearest" });
  } catch (error) {
    if (version === previewVersion) el("window-message").textContent = error.message;
  } finally {
    if (version === previewVersion) el("preview").disabled = false;
  }
}

function controls() {
  el("undo").disabled = !regions.length;
  const canReuse = snapshotId && validFields(previous.regions?.length) &&
    Math.abs(previous.width / previous.height - el("still").naturalWidth / el("still").naturalHeight) < .035;
  el("reuse").hidden = !canReuse || regions.length > 0;
  const complete = snapshotId && regions.length === fieldCount();
  const hasLectures = !!selectedLectures().length;
  const windowSelected = !el("windows").disabled && Number(el("windows").value) > 0;
  const starting = busy || el("models").disabled;
  el("selection-mode").disabled = busy || changingMode;
  el("focus").disabled = starting || !snapshotId || regions.length !== 1 || !hasLectures;
  el("auto").disabled = starting || !windowSelected || !hasLectures;
  el("start").hidden = !snapshotId;
  el("start").disabled = starting || !complete || !hasLectures;
  el("ready").textContent = !hasLectures ? "Отметь хотя бы один документ."
    : snapshotId && !complete ? "Для ручного чтения выдели вопрос и каждый видимый вариант." : "";
  el("mark-step").textContent = regions.length === fieldCount()
    ? "Разметка готова" : regions.length ? "Выдели вариант " + regions.length : "Выдели вопрос или весь квиз";
  const boxes = draft ? [...regions, draft] : regions;
  el("rects").replaceChildren(...boxes.map(([x, y, w, h], index) => {
    const div = document.createElement("div");
    div.className = "area" + (index === 0 ? " question" : "");
    Object.assign(div.style, { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" });
    const name = document.createElement("b");
    name.textContent = index === 0 ? "?" : String(index);
    div.append(name);
    return div;
  }));
}

function position(event) {
  const box = el("stage").getBoundingClientRect();
  const clamp = value => Math.min(1, Math.max(0, value));
  return [clamp((event.clientX - box.left) / box.width), clamp((event.clientY - box.top) / box.height)];
}

function draw(event) {
  if (!origin) return;
  const [x, y] = position(event);
  draft = [Math.min(x, origin[0]), Math.min(y, origin[1]), Math.abs(x - origin[0]), Math.abs(y - origin[1])];
  controls();
}

function clearResult() {
  resultKey = "";
  window.QuizChart?.show(null);
  el("reading-warnings").replaceChildren();
  el("reading-warnings").hidden = true;
  el("review").hidden = true;
  el("answer-proof").hidden = true;
  ["answer", "evidence", "state", "current-question"].forEach(id => { el(id).textContent = ""; });
  el("options").replaceChildren();
}

function present(data) {
  // The crop may move slightly between frames. Preserve keyboard focus and the
  // user's draft for this revision while the native reader compares the same fields.
  const key = data.state === 'review'
    ? JSON.stringify(['review', data.revision, data.canConfirmReading, data.message])
    : JSON.stringify(data);
  if (resultKey === key) return;
  resultKey = key;
  const answer = data.answer || {};
  const options = data.options || [];
  const warnings = data.readingWarnings || [];
  const warningBox = el("reading-warnings");
  warningBox.hidden = !warnings.length;
  warningBox.replaceChildren(...warnings.map(item => {
    const line = document.createElement("div");
    const label = document.createElement("p");
    const field = item.field === 0 ? "Вопрос" : "Вариант " + item.field;
    label.textContent = field + " · «" + item.readings[0] + "» или «" + item.readings[1] + "»";
    line.append(label);
    if (item.image?.startsWith("data:image/png;base64,")) {
      const image = new Image();
      image.alt = "Фрагмент окна для сверки";
      image.src = item.image;
      line.append(image);
    }
    return line;
  }));
  if (warnings.length && data.canConfirmReading) {
    const currentSession = token;
    window.addQuizReadingEditor(warningBox, [data.question, ...options], warnings, edits => {
      if (!currentSession || token !== currentSession || key !== resultKey) {
        throw new Error('Кадр уже изменился. Сверь текущий вопрос.');
      }
      return json('/api/native/confirm-reading', {session: currentSession, revision: data.revision, edits});
    });
  }
  const needsFigure = window.QuizChart?.show(warnings.length ? null : data.question || null);
  const choices = answer.choices || (answer.choice ? [answer.choice] : []);
  const valid = Array.isArray(choices) && choices.length > 0 && new Set(choices).size === choices.length
    && choices.every(choice => Number.isInteger(choice) && choice >= 1 && choice <= options.length)
    && (!data.expectedCount || choices.length === data.expectedCount)
    && (data.selectionMode !== "multiple" || choices.every(choice =>
      answer.evidence?.some(item => item.choice === choice && item.excerpt && item.source)));
  const pendingFigure = Boolean(needsFigure && answer.kind === "model");
  const confirmed = valid && !pendingFigure ? choices : [];
  const proof = confirmed.length ? answerProof(answer, options) : {};
  const proofLine = el("answer-proof");
  proofLine.hidden = !proof.note;
  proofLine.textContent = proof.note || "";
  proofLine.className = proof.tentative ? "problem-text" : "answer-text";
  el("review").hidden = !answer.journalId;
  if (answer.journalId) el("review").href = "/journal#" + encodeURIComponent(answer.journalId);
  el("answer").replaceChildren(...confirmed.map(choice => {
    const line = document.createElement("div");
    line.textContent = choice + " · " + data.options[choice - 1];
    return line;
  }));
  const modeLabel = data.selectionMode === "multiple"
    ? (data.expectedCount ? "Выбрать " + data.expectedCount : "Множественный выбор") : "";
  const timing = pendingFigure ? "Для ответа нужны значения на изображении. Открой разбор графика или таблицы."
    : confirmed.length ? "Ответ за " + data.elapsed + " с" : answer.detail || data.message;
  el("state").textContent = [modeLabel, timing, formatQuizTokenUsage(answer.tokenUsage), answer.journalWarning].filter(Boolean).join(" · ");
  let evidence = (answer.evidence || []).filter(item => confirmed.includes(item.choice));
  if (!evidence.length && (answer.source || answer.excerpt)) {
    evidence = [{...answer, choice:confirmed.length === 1 ? confirmed[0] : null}];
  }
  el("evidence").replaceChildren(...evidence.filter(item => item.source || item.excerpt).map(proof => {
    const details = document.createElement("details");
    details.open = true;
    const title = document.createElement("summary");
    const label = proof.choice ? "Вариант " + proof.choice : "Фрагмент для сверки";
    title.textContent = [label, proof.source].filter(Boolean).join(" · ");
    details.append(title);
    if (proof.excerpt) {
      const quote = document.createElement("blockquote");
      quote.textContent = proof.excerpt;
      details.append(quote);
    }
    return details;
  }));
  el("current-question").textContent = data.question || "Ожидание вопроса.";
  el("options").replaceChildren(...(data.options || []).filter(Boolean).map(text => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
}

async function poll(session) {
  if (token !== session) return;
  const version = modeVersion;
  try {
    const data = await json("/api/native/status?session=" + encodeURIComponent(session));
    if (token !== session) return;
    if (data.state === "ended" || data.state === "error") {
      await stopReading(data.message);
      return;
    }
    if (version === modeVersion && !changingMode) {
      if (data.requestedMode) el("selection-mode").value = data.requestedMode;
      present(data);
    }
  } catch (error) {
    if (token !== session) return;
    if (version === modeVersion && !changingMode) {
      clearResult();
      el("state").textContent = "Связь с приложением прервана. Переподключаемся…";
    }
  }
  if (token === session) window.setTimeout(() => poll(session), 300);
}

async function changeSelectionMode() {
  const session = token;
  if (!session) return;
  const selectionMode = el("selection-mode").value;
  changingMode = true;
  modeVersion++;
  controls();
  clearResult();
  el("state").textContent = "Применяем режим ответа…";
  try {
    await json("/api/native/mode", { session, selectionMode });
    if (token === session) savePreferences(true);
  } catch (error) {
    if (token === session) el("state").textContent = error.message;
  } finally {
    changingMode = false;
    modeVersion++;
    controls();
  }
}

async function startReading(auto, useFocus=false) {
  if (busy || token) return;
  const id = Number(auto ? el("windows").value : snapshotId);
  if (!id || !selectedLectures().length || (!auto && regions.length !== fieldCount()) || (useFocus && (!snapshotId || regions.length !== 1))) return;
  busy = true;
  el("setup").inert = true;
  controls();
  try {
    const result = await json("/api/native/start", {
      id, ...(auto ? {auto: true} : {regions}), model: el("model").value, lectureIds: selectedLectures(),
      selectionMode: el("selection-mode").value, record:el("record").checked,
      ...(useFocus ? {focus:regions[0],focusAspect:el("still").naturalWidth/el("still").naturalHeight} : {}),
    });
    token = result.session;
    savePreferences(auto);
    clearResult();
    el("manual-live").hidden = !auto;
    el("state").textContent = auto ? "Ищу вопрос и варианты…" : "Получаем первый вопрос…";
    el("setup").hidden = true;
    el("live").hidden = false;
    document.body.classList.add("reading");
    window.scrollTo(0, 0);
    poll(token);
  } catch (error) { el("window-message").textContent = error.message; }
  finally { busy = false; el("setup").inert = false; controls(); }
}

async function stopReading(message = "Захват остановлен.") {
  const session = token;
  token = "";
  clearResult();
  if (session) {
    try { await json("/api/native/stop", { session }); } catch { /* Server lease expires on its own. */ }
  }
  el("setup").hidden = false;
  el("live").hidden = true;
  document.body.classList.remove("reading");
  el("window-message").textContent = message;
  controls();
}

el("stage").addEventListener("pointerdown", event => {
  if (event.button !== 0 || regions.length >= fieldCount() || !snapshotId) return;
  origin = position(event);
  el("stage").setPointerCapture(event.pointerId);
  draw(event);
});
el("stage").addEventListener("pointermove", draw);
el("stage").addEventListener("pointerup", event => {
  if (!origin) return;
  draw(event);
  if (draft[2] >= .002 && draft[3] >= .002) regions.push(draft);
  origin = draft = null;
  controls();
});
el("stage").addEventListener("pointercancel", () => { draft = origin = null; controls(); });
el("undo").onclick = () => { regions.pop(); controls(); };
el("reuse").onclick = () => {
  regions = previous.regions.map(box => box.slice());
  el("area-count").value = String(regions.length - 1);
  controls();
};
el("area-count").onchange = () => {
  regions = regions.slice(0, fieldCount());
  draft = origin = null;
  controls();
};
el("focus").onclick = () => startReading(true, true);
el("list").onclick = loadWindows;
el("windows").onchange = () => { resetMark(); el("preview").disabled = !el("windows").value; };
el("preview").onclick = takePreview;
el("models").onclick = loadModels;
el("files").onchange = importLectures;
el("start").onclick = () => startReading(false);
el("auto").onclick = () => startReading(true);
el("record").checked = previous.record !== false;
el("record").onchange = () => saveSharedSetting("record", el("record").checked);
el("lectures").onchange = () => {
  saveSharedSetting("selected", selectedLectures());
  controls();
};
el("model").onchange = () => {
  previous.model = el("model").value;
  saveSharedSetting("model", previous.model);
  el("model-info").textContent = previous.model ? "Модель доступна через локальный сервер." : "Доступны карточки и поиск по тексту.";
};
el("visual").addEventListener("click", event => {
  if (!token) return;
  event.preventDefault();
  const id = el("windows").value;
  const opened = openQuizVisual(async () => {
    const response = await call("/api/native/preview?id=" + encodeURIComponent(id));
    const blob = await response.blob();
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    // The native picker takes a fresh snapshot after the click. A timer may already
    // have advanced the quiz; let the visual page read its fields from that same image.
    return {image};
  });
  if (!opened) el("state").textContent = "Разреши открыть вкладку для разбора графика.";
});
window.addEventListener("storage", async event => {
  if(event.key !== "quizlens.corrections.rev" || !token) return;
  const session = token;
  try {
    modeVersion++;
    clearResult();
    await json("/api/native/refresh", {session});
  } catch (error) {
    if(token === session) el("state").textContent = error.message;
  }
});
if (["auto", "single", "multiple"].includes(previous.selectionMode)) el("selection-mode").value = previous.selectionMode;
el("selection-mode").onchange = changeSelectionMode;
el("manual-live").onclick = async () => {
  if (!token) return;
  await stopReading();
  await takePreview();
};
el("stop").onclick = () => stopReading();
window.addEventListener("pagehide", () => {
  if (!token) return;
  navigator.sendBeacon("/api/native/stop", new Blob([JSON.stringify({ session: token })], { type: "application/json" }));
  token = "";
});
window.addEventListener("pageshow", event => {
  if (event.persisted) { clearResult(); el("live").hidden = true; el("setup").hidden = false; document.body.classList.remove("reading"); controls(); }
});
controls();
loadLectures();
loadModels();
