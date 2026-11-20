"use strict";

function formatQuizTokenUsage(usage) {
  if (!usage) return "";
  if (usage.source === "cache") return "Повтор вопроса · 0 новых токенов";
  if (usage.source === "none") return "Ответ без вызова языковой модели · 0 токенов";
  if (usage.source === "unknown") return "Расход токенов неизвестен";
  if (usage.source !== "model") return "";
  const count = (value, estimated) => Number.isSafeInteger(value) && value >= 0
    ? (estimated ? "≈ " : "") + value.toLocaleString("ru-RU") : "—";
  return "Токены модели · вход " + count(usage.input, usage.inputEstimated)
    + " · выход " + count(usage.output, usage.outputEstimated);
}
