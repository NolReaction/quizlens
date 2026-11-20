'use strict';
const el = id => document.getElementById(id);
const state = {page:'practice', items:[], index:0, full:null, rect:null, drag:null, cropping:false,
  stream:null, pendingImage:false, epoch:0, imageVersion:0, sourceVersion:0, busy:false};

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function api(path, payload) {
  const response = await fetch(path, payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)} : {});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Локальный сервер вернул ошибку.');
  return data;
}
function invalidate() {
  state.epoch++;
  el('answerPanel').replaceChildren();
  controls();
}
function modelId() {
  if (el('otherModels').open && el('customModel').value.trim()) {
    return el('confirmModel').checked ? el('customModel').value.trim() : '';
  }
  return el('model').value;
}
function controls() {
  const hasImage = state.page === 'practice' ? state.items.length > 0 : Boolean(state.full);
  el('solve').disabled = state.busy || !modelId() || !hasImage;
  el('solve').textContent = state.busy ? 'Модель читает изображение…' : 'Разобрать изображение';
}
async function loadModels() {
  const previous = el('model').value;
  el('refresh').disabled = true;
  try {
    const {models} = await api('/api/visual/models');
    el('model').replaceChildren(...models.map(name => new Option(name, name)));
    if (!models.length) el('model').add(new Option('Модель не выбрана', ''));
    else if (models.includes(previous)) el('model').value = previous;
    el('modelStatus').textContent = models.length ? 'Модель получит изображение на этом компьютере.' :
      'Для разбора своего кадра загрузи в LM Studio или Bionic модель с поддержкой изображений и нажми «Обновить». Примеры можно решать самостоятельно.';
  } catch (error) {
    el('model').replaceChildren(new Option('Сервер недоступен', ''));
    el('modelStatus').textContent = error.message + ' Примеры доступны без модели.';
  } finally {el('refresh').disabled = false;invalidate();}
}
function switchPage(page) {
  state.page = page;
  el('practice').hidden = page !== 'practice';
  el('custom').hidden = page !== 'custom';
  el('practiceTab').setAttribute('aria-pressed', String(page === 'practice'));
  el('imageTab').setAttribute('aria-pressed', String(page === 'custom'));
  if (page === 'practice') { stopStream();state.sourceVersion++; }
  invalidate();
}
function text(ctx, content, x, y, align = 'left', size = 24, weight = 500, color = '#233f44', maxWidth) {
  ctx.font = weight + ' ' + size + 'px system-ui, sans-serif';
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  ctx.fillText(String(content), x, y, maxWidth || 1080);
}
function drawFigure(f) {
  const canvas = el('chart');
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = '#f1f8f4';ctx.fillRect(0,0,w,h);
  text(ctx, f.title, 47, 54, 'left', 36, 680);
  if (f.kind === 'table') {
    const cell = (w-96)/f.headers.length;
    const height = Math.min(87, (h-140)/(f.rows.length+1));
    [f.headers,...f.rows].forEach((row,i) => {
      const y = 110 + i*height;
      ctx.fillStyle = i === 0 ? '#c6e8d9' : i%2 === 1 ? '#ffffff' : '#e8f1ed';
      ctx.fillRect(48,y,w-96,height-2);
      row.forEach((value,j) => text(ctx, value, 62+j*cell,y+height/2,'left',25,i === 0 ? 700 : 500,'#233f44',cell-26));
    });
  } else {
    const l=104, r=w-45, t=138, b=h-79;
    const max=Math.max(...f.values);
    const step = 5 * Math.max(1, Math.ceil(max/25));
    const top=Math.ceil(max/step)*step;
    const y=v=>b-(b-t)*v/top;
    text(ctx,f.unit,l,t-47,'left',21);
    for(let v=0;v<=top;v+=step) {
      ctx.strokeStyle='#c7d8d1';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(l,y(v));ctx.lineTo(r,y(v));ctx.stroke();
      text(ctx,v,l-15,y(v),'right',19);
    }
    const pitch=(r-l)/f.values.length;
    const x=i=>l+(i+.5)*pitch;
    if(f.kind==='line') {
      ctx.strokeStyle='#00795e';ctx.lineWidth=5;ctx.beginPath();
      f.values.forEach((v,i)=>i ? ctx.lineTo(x(i),y(v)) : ctx.moveTo(x(i),y(v)));ctx.stroke();
    }
    f.values.forEach((v,i)=>{
      ctx.fillStyle='#127c64';
      if(f.kind==='line') {ctx.beginPath();ctx.arc(x(i),y(v),8,0,Math.PI*2);ctx.fill();}
      else ctx.fillRect(x(i)-pitch*.27,y(v),pitch*.54,b-y(v));
      text(ctx,v,x(i),y(v)-23,'center',26,700);
      text(ctx,f.labels[i],x(i),b+34,'center',23,520,'#233f44',pitch-8);
    });
  }
  const headers = f.kind === 'table' ? f.headers : ['Период', f.unit];
  const rows = f.kind === 'table' ? f.rows : f.labels.map((label,i)=>[label,f.values[i]]);
  const table=document.createElement('table');
  [headers,...rows].forEach((row,i)=>{
    const tr=document.createElement('tr');
    row.forEach(value=>tr.append(element(i===0?'th':'td',String(value))));table.append(tr);
  });
  el('readableData').replaceChildren(table);
  canvas.setAttribute('aria-label',f.title+'. Числа доступны в разделе «Открыть данные текстом».');
}
function renderExercise() {
  const item=state.items[state.index];
  if (!item) return;
  el('exercise').value=String(state.index);
  el('grade').textContent='';el('next').hidden=true;
  drawFigure(item.figure);
  el('practiceQuestion').textContent=item.question;
  el('picks').replaceChildren();
  item.options.forEach((option,i)=>{
    const button=element('button',(i+1)+'. '+option,'button pick');
    button.type='button';
    button.onclick=()=>{
      [...el('picks').children].forEach((b,j)=>{
        b.disabled=true;
        if(item.choices.includes(j+1))b.classList.add('correct');
        else if(j===i)b.classList.add('wrong');
      });
      el('grade').textContent=(item.choices.includes(i+1)?'Верно. ':'Верный ответ — '+item.options[item.choices[0]-1]+'. ')+item.explanation;
      el('next').hidden=false;
    };
    el('picks').append(button);
  });
  invalidate();
}
function stopStream() {
  if(state.stream)state.stream.getTracks().forEach(track=>track.stop());
  state.stream=null;
  el('video').srcObject=null;el('video').hidden=el('stop').hidden=el('freeze').hidden=true;
}
function snapshot(source, width, height) {
  const maxSide=2000;
  if (!width || !height) throw Error('Пока не удалось прочитать изображение.');
  const scale=Math.min(1,maxSide/Math.max(width,height));
  const canvas=document.createElement('canvas');
  canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
  const context=canvas.getContext('2d');
  context.fillStyle='white';context.fillRect(0,0,canvas.width,canvas.height);
  context.drawImage(source,0,0,canvas.width,canvas.height);
  return canvas;
}
function showPhoto() {
  const full=state.full;
  const r=state.rect || {x:0,y:0,w:full.width,h:full.height};
  const canvas=el('photo');canvas.width=r.w;canvas.height=r.h;
  canvas.getContext('2d').drawImage(full,r.x,r.y,r.w,r.h,0,0,r.w,r.h);
  state.drag=null;state.cropping=false;
  el('photoBox').classList.remove('cropping');
  el('cropBox').hidden=true;el('resetCrop').hidden=!state.rect;
  el('photoBox').hidden=el('cropActions').hidden=false;el('crop').textContent='Обрезать кадр';invalidate();
}
function setPhoto(full, question='', options=[]) {
  if (state.pendingImage && !question) {
    question = el('manualQ').value.trim();
    options = el('manualOptions').value.split('\n').map(line => line.trim()).filter(Boolean);
  }
  state.pendingImage=false;
  state.full=full;state.rect=null;
  el('manualQ').value=question;el('manualOptions').value=options.join('\n');
  el('wording').open=Boolean(question && options.length);
  el('photoStatus').textContent='Проверь, что видны деления осей, легенда или заголовки таблицы. Область для разбора можно обрезать.';
  showPhoto();
}
async function openImage(url, question, options) {
  const version=++state.imageVersion;
  const img=new Image();
  img.src=url;
  await img.decode();
  if(version!==state.imageVersion || state.page!=='custom')return;
  if(img.naturalWidth*img.naturalHeight>70e6)throw Error('Изображение слишком большое. Открой уменьшенную копию.');
  setPhoto(snapshot(img,img.naturalWidth,img.naturalHeight),question,options);
}
async function share() {
  if(!navigator.mediaDevices?.getDisplayMedia){el('photoStatus').textContent='Захват недоступен в этом браузере. Открой файл или используй Chrome.';return;}
  const request=++state.sourceVersion;
  try {
    const next=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});
    if(request!==state.sourceVersion || state.page!=='custom'){next.getTracks().forEach(t=>t.stop());return;}
    stopStream();state.stream=next;
    const v=el('video');v.srcObject=next;v.hidden=el('stop').hidden=false;
    next.getVideoTracks()[0].addEventListener('ended',()=>{if(state.stream===next)stopStream();},{once:true});
    await v.play();
    if(state.stream!==next)return;
    el('freeze').hidden=false;
    el('photoStatus').textContent='Открой вопрос в выбранном окне и нажми «Взять текущий кадр».';
  } catch(error) {if(request===state.sourceVersion)el('photoStatus').textContent='Окно не подключено. Можно повторить выбор или открыть файл.';}
}
function position(event) {
  const b=el('photo').getBoundingClientRect();
  return {x:Math.max(0,Math.min(1,(event.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(event.clientY-b.top)/b.height))};
}
function cropRect(a,b) {return{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)};}
el('photoBox').onpointerdown=event=>{
  if(!state.cropping)return;
  event.preventDefault();state.drag=position(event);el('photoBox').setPointerCapture(event.pointerId);
};
el('photoBox').onpointermove=event=>{
  if(!state.drag)return;
  const r=cropRect(state.drag,position(event)), box=el('cropBox');
  box.hidden=false;Object.assign(box.style,{left:r.x*100+'%',top:r.y*100+'%',width:r.w*100+'%',height:r.h*100+'%'});
};
el('photoBox').onpointercancel=()=>{state.drag=null;el('cropBox').hidden=true;};
el('photoBox').onpointerup=event=>{
  if(!state.drag)return;
  const r=cropRect(state.drag,position(event));
  const old=state.rect || {x:0,y:0,w:state.full.width,h:state.full.height};
  state.drag=null;el('cropBox').hidden=true;
  const width=Math.floor(r.w*old.w),height=Math.floor(r.h*old.h);
  if(width<25||height<25){el('photoStatus').textContent='Рамка слишком мала. Обведи область целиком.';return;}
  state.rect={x:old.x+Math.floor(r.x*old.w),y:old.y+Math.floor(r.y*old.h),w:width,h:height};
  el('photoStatus').textContent='На разбор пойдёт показанная область.';showPhoto();
};
el('crop').onclick=()=>{
  state.cropping=!state.cropping;state.drag=null;el('cropBox').hidden=true;
  el('photoBox').classList.toggle('cropping',state.cropping);
  el('crop').textContent=state.cropping?'Отменить обрезку':'Обрезать кадр';
  el('photoStatus').textContent=state.cropping?'Обведи график или таблицу. Вопрос и варианты можно ввести под кадром.':'Кадр готов.';
};
el('resetCrop').onclick=()=>{state.rect=null;showPhoto();};
function showAnswer(answer, duration, item) {
  const box=el('answerPanel');box.replaceChildren();
  if (answer.choices?.length) {
    box.append(element('h2',(answer.equation ? 'Ответ по расчёту · ' : 'Ответ модели · ') + answer.choices.join(', ')));
    const list=document.createElement('ul');
    answer.choices.forEach(n=>list.append(element('li',answer.options[n-1])));
    box.append(list);
  } else box.append(element('p',answer.detail || 'Недостаточно данных.'));
  if (answer.readings?.length) {
    box.append(element('h3','Значения с изображения'));
    const list=document.createElement('dl');
    list.className='figure-readings';
    for (const row of answer.readings) {
      list.append(element('dt',row.label),element('dd',(row.estimated?'≈ ':'') + row.value + ' ' + row.unit));
    }
    box.append(list);
  }
  if (answer.equation) box.append(element('p',answer.equation,'equation'));
  if (answer.observation) box.append(element('p','Модель прочитала на изображении'),element('p',answer.observation));
  if (item && answer.choices?.length) {
    const ok=answer.choices.length===item.choices.length && item.choices.every(n=>answer.choices.includes(n));
    box.append(element('p',ok?'Совпало с эталоном.':'Эталон · '+item.choices.join(', ')+'. '+item.explanation));
  }
  box.append(element('p','Запрос и ответ · '+(duration/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})+' с','time'));
}
async function solve() {
  if(state.busy)return;
  const item=state.page==='practice'?state.items[state.index]:null;
  const question=item?item.question:el('manualQ').value.trim();
  const options=item?item.options:el('manualOptions').value.split('\n').map(line=>line.trim()).filter(Boolean);
  const box=el('answerPanel');
  if((question||options.length)&&(!question||options.length<2||options.length>8)){box.textContent='Нужны вопрос и 2–8 вариантов, по одному на строку. Если всё видно на кадре, очисти оба поля.';return;}
  const canvas=item?el('chart'):el('photo');
  const epoch=state.epoch;
  const start=performance.now();
  state.busy=true;controls();box.textContent='Ожидаю ответ локальной модели…';
  try {
    const answer=await api('/api/visual/solve',{image:canvas.toDataURL('image/jpeg',.93),model:modelId(),visionConfirmed:el('otherModels').open&&el('confirmModel').checked,question,options,selectionMode:item?'single':el('selection').value});
    if(epoch===state.epoch)showAnswer(answer,performance.now()-start,item);
  } catch(error) {if(epoch===state.epoch)box.textContent=error.message;}
  finally {state.busy=false;controls();}
}
el('exercise').onchange=()=>{state.index=Number(el('exercise').value);renderExercise();};
el('next').onclick=()=>{state.index=(state.index+1)%state.items.length;renderExercise();};
el('share').onclick=share;el('stop').onclick=()=>{state.sourceVersion++;stopStream();};
el('freeze').onclick=()=>{
  try{const v=el('video');state.imageVersion++;setPhoto(snapshot(v,v.videoWidth,v.videoHeight));}
  catch(error){el('photoStatus').textContent=error.message;}
};
el('file').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  const url=URL.createObjectURL(file);
  try {
    if(file.size>24*1024*1024||!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Выбери изображение PNG, JPEG или WebP до 24 МиБ.');
    stopStream();state.sourceVersion++;await openImage(url,'',[]);
  } catch(error){el('photoStatus').textContent=error.message;}
  finally{URL.revokeObjectURL(url);event.target.value='';}
};
el('practiceTab').onclick=()=>switchPage('practice');el('imageTab').onclick=()=>switchPage('custom');
el('refresh').onclick=loadModels;el('solve').onclick=solve;
for(const id of ['model','customModel','confirmModel','selection','manualQ','manualOptions'])el(id).addEventListener('input',invalidate);
el('otherModels').addEventListener('toggle',invalidate);
window.addEventListener('pagehide',()=>{state.sourceVersion++;stopStream();});
if(location.hash==='#capture') {
  switchPage('custom');
  if(window.opener) {
    const opener=window.opener;
    window.addEventListener('message',function receive(event) {
      if(event.origin!==location.origin || event.source!==opener || event.data?.type!=='quizlens-visual-frame')return;
      window.removeEventListener('message',receive);
      if (state.sourceVersion || state.full || state.imageVersion || state.page!=='custom') return;
      const {image,question,options,selectionMode}=event.data;
      const valid=typeof question==='string' && question.trim().length>0 && question.length<=6000 &&
        Array.isArray(options) && options.length>=2 && options.length<=8 &&
        options.every(s=>typeof s==='string' && s.trim().length>0 && s.length<=900);
      if (['auto','single','multiple'].includes(selectionMode)) el('selection').value=selectionMode;
      if (!image && valid) {
        state.pendingImage=true;
        el('manualQ').value=question;
        el('manualOptions').value=options.join('\n');
        el('wording').open=true;
        el('photoStatus').textContent='Вопрос и варианты получены. Добавь изображение той же задачи с подписями и данными.';
        invalidate();
        return;
      }
      if(typeof image!=='string'||image.length>14*1024*1024||!/^data:image\/(?:jpeg|png);base64,/.test(image))return;
      openImage(image,valid?question:'',valid?options:[]).catch(()=>{el('photoStatus').textContent='Не удалось открыть кадр. Выбери окно или файл.';});
    });
    opener.postMessage({type:'quizlens-visual-ready'},location.origin);
  }
}
loadModels();
api('/api/visual/exercises').then(({items})=>{
  state.items=items;
  el('exercise').replaceChildren(...items.map((item,i)=>new Option((i+1)+' / '+items.length+' · '+item.title,String(i))));
  el('exercise').disabled=false;renderExercise();
}).catch(e=>{el('grade').textContent=e.message;});
