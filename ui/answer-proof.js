'use strict';

function answerProof(result, options) {
  const verification = result.verification;
  if (verification?.kind === 'calculation' && verification.equation) {
    return {note:'Проверено расчётом · ' + verification.equation};
  }
  if (result.kind === 'ocr_card' && result.matchedQuestion) {
    return {note:'Вопрос в карточке · «' + result.matchedQuestion + '»'};
  }
  if (result.kind === 'exact') return {note:'Ответ из готовой карточки'};
  if (result.kind === 'correction') return {note:'Ответ из твоей карточки'};
  const percentage = /^[+−-]?\d+(?:[.,]\d+)?\s*%$/;
  if (result.kind === 'model' && options.length && options.every(text => percentage.test(text.trim()))) {
    return {note:'Вариант модели · расчёт не проверен', tentative:true};
  }
  return {};
}
