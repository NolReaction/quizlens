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
let lastFields = null;
let previous = loadPreferences();

function loadPreferences() {
  try {
    const main = JSON.parse(localStorage.getItem("quizlens.settings.v1") || "{}") || {};
    const native = JSON.parse(localStorage.getItem("quizlens-native") || "{}") || {};
    return {model:main.model, lectureIds:main.selected, ...native, record:main.record ?? native.record};
  } catch { return {}; }
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
    const labels = lectures.map((lecture) => {
      const item = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = lecture.id;
      input.checked = !Array.isArray(selected) || selected.includes(lecture.id);
      input.addEventListener("change", controls);
      item.append(input, document.createTextNode(lecture.name));
      return item;
    });
    el("lectures").replaceChildren(...labels);
    if (!labels.length) el("files-info").textContent = "Добавь хотя бы одну лекцию.";
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
    el("model-info").textContent = models.length ? "" : "Загрузи модель в LM Studio и включи локальный сервер.";
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
  el("ready").textContent = !hasLectures ? "Отметь хотя бы одну лекцию."
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
  lastFields = null;
  el("review").hidden = true;
  ["answer", "evidence", "state", "current-question"].forEach(id => { el(id).textContent = ""; });
  el("options").replaceChildren();
}

function present(data) {
  const key = JSON.stringify(data);
  if (resultKey === key) return;
  resultKey = key;
  const answer = data.answer || {};
  const options = data.options || [];
  const choices = answer.choices || (answer.choice ? [answer.choice] : []);
  const valid = Array.isArray(choices) && choices.length > 0 && new Set(choices).size === choices.length
    && choices.every(choice => Number.isInteger(choice) && choice >= 1 && choice <= options.length)
    && (!data.expectedCount || choices.length === data.expectedCount)
    && (data.selectionMode !== "multiple" || choices.every(choice =>
      answer.evidence?.some(item => item.choice === choice && item.excerpt && item.source)));
  const confirmed = valid ? choices : [];
  lastFields = data.question && validFields(options.length + 1) ? {question:data.question,options} : null;
  el("review").hidden = !answer.journalId;
  if (answer.journalId) el("review").href = "/journal#" + encodeURIComponent(answer.journalId);
  el("answer").replaceChildren(...confirmed.map(choice => {
    const line = document.createElement("div");
    line.textContent = choice + " · " + data.options[choice - 1];
    return line;
  }));
  const modeLabel = data.selectionMode === "multiple"
    ? (data.expectedCount ? "Выбрать " + data.expectedCount : "Множественный выбор") : "";
  const timing = confirmed.length ? "Ответ за " + data.elapsed + " с" : data.message || answer.detail;
  el("state").textContent = [modeLabel, timing, answer.journalWarning].filter(Boolean).join(" · ");
  const evidence = answer.evidence || (confirmed.length === 1 ? [{ ...answer, choice: confirmed[0] }] : []);
  el("evidence").replaceChildren(...evidence.filter(proof =>
    confirmed.includes(proof.choice) && (proof.source || proof.excerpt)).map(proof => {
    const details = document.createElement("details");
    const title = document.createElement("summary");
    title.textContent = "Вариант " + proof.choice + " · " + (proof.source || "Цитата из лекции");
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
el("record").onchange = () => {
  try {
    const settings = JSON.parse(localStorage.getItem("quizlens.settings.v1") || "{}");
    localStorage.setItem("quizlens.settings.v1", JSON.stringify({...settings,record:el("record").checked}));
  } catch { /* Session settings still apply when storage is unavailable. */ }
};
el("visual").addEventListener("click", event => {
  if (!token) return;
  event.preventDefault();
  const id = el("windows").value;
  const fields = lastFields;
  const opened = openQuizVisual(async () => {
    const response = await call("/api/native/preview?id=" + encodeURIComponent(id));
    const blob = await response.blob();
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    return {image, ...(fields || {})};
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
