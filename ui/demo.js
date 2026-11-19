"use strict";

const ui = Object.fromEntries(
  [...document.querySelectorAll("[id]")].map(element => [element.id, element])
);
const optionButtons = [...document.querySelectorAll(".answer")];
const translations = {
  ru: {
    title: "Метрики в аналитике", category: "Пробный квиз", duration: "На вопрос", language: "Язык",
    mode: "Режим", modes: ["Один ответ", "Несколько ответов", "От 2 до 8 вариантов"], submit: "Отправить ответы",
    selection: (n, total) => `Отмечено ${n} из ${total}.`,
    times: ["20 секунд", "15 секунд", "Без таймера"], ready: "Экран готов для разметки. Когда закончите, нажмите «Начать».",
    lobbyTitle: "Проверить захват окна",
    lobbySteps: "Вернись на страницу «Захват», нажми «Выбрать окно» и выбери «Пробный квиз». Затем вернись сюда и нажми «Начать».",
    lobby: "Первый вопрос появится, когда ты будешь готов.",
    start: "Начать", next: "Дальше", result: "Результат", reset: "Ещё раз", correct: "Верно.", wrong: "Неверно.",
    timeout: "Время вышло.", noTimer: "Без таймера", choosing: "Выберите один вариант.", complete: "Квиз завершён",
    position: (n, total) => `Вопрос ${n} из ${total}`, score: (n, total) => `Верных ответов — ${n} из ${total}`,
    seconds: n => n + " с"
  },
  en: {
    title: "Analytics metrics", category: "Practice quiz", duration: "Per question", language: "Language",
    mode: "Mode", modes: ["One answer", "Multiple answers", "2–8 options"], submit: "Submit answers",
    selection: (n, total) => `${n} of ${total} selected.`,
    times: ["20 seconds", "15 seconds", "No timer"], ready: "The question is ready for calibration. Press Start when you are ready.",
    lobbyTitle: "Try window capture",
    lobbySteps: "In QuizLens, open Capture, choose a window and select Practice quiz. Then return here and press Start.",
    lobby: "The first question will appear when you are ready.",
    start: "Start", next: "Next", result: "Results", reset: "Try again", correct: "Correct.", wrong: "Incorrect.",
    timeout: "Time is up.", noTimer: "No timer", choosing: "Choose one answer.", complete: "Quiz complete",
    position: (n, total) => `Question ${n} of ${total}`, score: (n, total) => `${n} of ${total} correct`,
    seconds: n => n + " s"
  }
};

let bank = [];
let questions = [];
let dictionary = translations.ru;
let index = 0;
let score = 0;
let state = "loading";
let timer = null;
let deadline = null;
const selected = new Set();
const query = new URLSearchParams(location.search);
const lobbyMode = query.get("lobby") === "1";

function reset() {
  clearInterval(timer);
  deadline = null;
  index = 0;
  score = 0;
  const lang = ui.language.value;
  dictionary = translations[lang];
  const multiple = ui.mode.value === "multiple";
  const variable = ui.mode.value === "variable";
  questions = bank.filter(item => item.language === lang &&
    (variable ? item.format === "variable" : item.format !== "variable" && Array.isArray(item.answers) === multiple));
  if (multiple) {
    // A second round with the same text checks whether suggestions follow the current option order.
    questions = questions.flatMap(card => [card, {...card, options: [1, 0, 3, 2].map(i => card.options[i])}]);
  }
  document.documentElement.lang = lang;
  document.title = dictionary.category + " · " + dictionary.title;
  ui.title.textContent = dictionary.title;
  ui.category.textContent = dictionary.category;
  ui["result-label"].textContent = dictionary.complete;
  ui["duration-label"].textContent = dictionary.duration;
  ui["language-label"].textContent = dictionary.language;
  ui["mode-label"].textContent = dictionary.mode;
  ui["lobby-heading"].textContent = dictionary.lobbyTitle;
  ui["lobby-steps"].textContent = dictionary.lobbySteps;
  [...ui.mode.options].forEach((option, i) => { option.textContent = dictionary.modes[i]; });
  [...ui.duration.options].forEach((option, i) => { option.textContent = dictionary.times[i]; });
  ui.mode.disabled = ui.language.disabled = ui.duration.disabled = false;
  ui.result.hidden = true;
  state = "ready";
  showQuestion();
}

function showQuestion() {
  const card = questions[index];
  const multiple = Array.isArray(card.answers);
  selected.clear();
  while (optionButtons.length < card.options.length) {
    const index = optionButtons.length;
    const button = optionButtons[0].cloneNode(true);
    button.querySelector(".marker").textContent = String(index + 1);
    ui.options.append(button);
    button.addEventListener("click", () => choose(index));
    optionButtons.push(button);
  }
  ui.round.hidden = lobbyMode && state === "ready";
  ui.lobby.hidden = !ui.round.hidden;
  if (lobbyMode) ui.round.classList.toggle("list-layout", index % 2 === 1);
  ui.position.textContent = dictionary.position(index + 1, questions.length);
  ui.question.textContent = card.question;
  optionButtons.forEach((button, i) => {
    button.className = "answer";
    button.hidden = i >= card.options.length;
    if (button.hidden) { button.disabled = true; return; }
    button.disabled = state !== "active";
    if (multiple) {
      button.setAttribute("role", "checkbox");
      button.setAttribute("aria-checked", "false");
    } else {
      button.removeAttribute("role");
      button.removeAttribute("aria-checked");
    }
    button.querySelector(".option-text").textContent = card.options[i];
  });
  const duration = Number(ui.duration.value);
  ui.clock.textContent = duration ? dictionary.seconds(duration) : dictionary.noTimer;
  ui.bar.style.transform = "scaleX(1)";
  ui.feedback.textContent = state === "active" ? dictionary.choosing : (lobbyMode ? dictionary.lobby : dictionary.ready);
  ui.action.hidden = state === "active" && !multiple;
  ui.action.disabled = false;
  ui.action.textContent = state === "active" ? dictionary.submit : dictionary.start;
  if (state === "active" && multiple) updateSelection();
}

function play() {
  state = "active";
  ui.mode.disabled = ui.duration.disabled = ui.language.disabled = true;
  showQuestion();
  const duration = Number(ui.duration.value);
  if (duration > 0) {
    deadline = performance.now() + duration * 1000;
    timer = setInterval(updateClock, 200);
  }
}

function updateClock() {
  if (deadline === null || state !== "active") return;
  const remaining = Math.max(0, deadline - performance.now());
  ui.clock.textContent = dictionary.seconds(Math.ceil(remaining / 1000));
  ui.bar.style.transform = "scaleX(" + remaining / (Number(ui.duration.value) * 1000) + ")";
  if (remaining === 0) settle(null);
}

function updateSelection() {
  const required = questions[index].answers.length;
  optionButtons.forEach((button, i) => button.setAttribute("aria-checked", String(selected.has(i))));
  ui.feedback.textContent = dictionary.selection(selected.size, required);
  ui.action.disabled = selected.size !== required;
}

function choose(i) {
  if (state !== "active" || i >= questions[index].options.length) return;
  if (deadline !== null && performance.now() >= deadline) {
    updateClock();
    return;
  }
  if (!questions[index].answers) {
    settle(new Set([i]));
    return;
  }
  if (selected.has(i)) selected.delete(i);
  else selected.add(i);
  updateSelection();
}

function settle(picked) {
  if (state !== "active") return;
  if (picked !== null && deadline !== null && performance.now() >= deadline) {
    updateClock();
    return;
  }
  state = "graded";
  clearInterval(timer);
  deadline = null;
  const card = questions[index];
  const answers = card.answers || [card.answer];
  const correct = picked !== null && picked.size === answers.length &&
    [...picked].every(i => answers.includes(card.options[i]));
  if (correct) score++;
  optionButtons.forEach((button, i) => {
    button.disabled = true;
    const expected = answers.includes(card.options[i]);
    button.classList.toggle("correct", expected);
    button.classList.toggle("wrong", Boolean(picked?.has(i)) && !expected);
  });
  const label = picked === null ? dictionary.timeout : correct ? dictionary.correct : dictionary.wrong;
  ui.feedback.textContent = label + " " + card.explanation;
  ui.action.textContent = index + 1 === questions.length ? dictionary.result : dictionary.next;
  ui.action.disabled = false;
  ui.action.hidden = false;
}

function advance() {
  if (state === "ready") play();
  else if (state === "active" && selected.size === questions[index].answers?.length) settle(selected);
  else if (state === "complete") reset();
  else if (state === "graded") {
    index++;
    if (index < questions.length) {
      play();
      return;
    }
    state = "complete";
    ui.round.hidden = true;
    ui.result.hidden = false;
    ui.mode.disabled = ui.duration.disabled = ui.language.disabled = false;
    ui.score.textContent = dictionary.score(score, questions.length);
    ui.feedback.textContent = "";
    ui.action.textContent = dictionary.reset;
    ui.action.hidden = false;
  }
}

ui.language.addEventListener("change", reset);
ui.mode.addEventListener("change", reset);
ui.duration.addEventListener("change", reset);
ui.action.addEventListener("click", advance);
optionButtons.forEach((button, i) => button.addEventListener("click", () => choose(i)));
document.addEventListener("keydown", event => {
  if (event.repeat || event.altKey || event.metaKey || event.ctrlKey) return;
  if (state === "active" && /^[1-8]$/.test(event.key)) choose(Number(event.key) - 1);
});

async function load() {
  ui.action.hidden = true;
  ui.mode.disabled = ui.language.disabled = ui.duration.disabled = true;
  ui.language.value = query.get("lang") === "en" ? "en" : "ru";
  ui.mode.value = ["multiple", "variable"].includes(query.get("mode")) ? query.get("mode") : "single";
  try {
    const response = await fetch("./analytics-demo.quiz.json");
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    bank = data.cards;
    reset();
  } catch {
    ui.feedback.textContent = "Не удалось загрузить вопросы. Откройте пробный квиз из приложения и обновите страницу.";
  }
}
load();
