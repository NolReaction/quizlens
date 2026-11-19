'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const root=path.join(__dirname,'..');
const js=fs.readFileSync(path.join(root,'ui/visual.js'),'utf8');
const html=fs.readFileSync(path.join(root,'ui/visual.html'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
const settle=()=>new Promise(resolve=>setImmediate(resolve));

class Node {
  constructor(tag){this.tag=tag;this.children=[];this.value='';this.hidden=false;this.style={};this.events={};this.props={};this.classes=new Set();this.width=1120;this.height=600;}
  get classList(){return {add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)}}
  addEventListener(event,fn){this.events[event]=fn;}
  setAttribute(k,v){this.props[k]=v;}
  replaceChildren(...list){this.children=list;if(this.tag==='exercise'||this.tag==='model')this.value=list[0]?.value||'';}
  append(...list){this.children.push(...list);}
  getContext(){return {fillRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){},drawImage(){}};}
  toDataURL(){return 'data:image/jpeg;base64,test';}
  getBoundingClientRect(){return {left:0,top:0,width:500,height:300};}
  setPointerCapture(){}
}
const inputCase={id:'growth',title:'Прирост',question:'Когда прирост выше?',options:['Июнь','Март','Май'],choices:[2],explanation:'В марте +8.',figure:{kind:'bar',labels:['Январь','Март'],values:[4,12],unit:'заказов',title:'График'}};
async function boot({capture=false}={}){
 const els=Object.fromEntries(ids.map(id=>[id,new Node(id)]));
 const events={}, sent=[], calls=[];
 const origin='http://127.0.0.1:8765';
 const opener={postMessage:(data,host)=>sent.push([data,host])};
 const win={opener,addEventListener:(event,listener)=>events[event]=listener,removeEventListener:event=>delete events[event]};
 const doc={getElementById:id=>els[id],createElement:tag=>new Node(tag)};
 const fetch=async (url,init={})=>{
   if(url==='/api/visual/solve'){
     calls.push(JSON.parse(init.body));
     return{ok:true,json:async()=>({choices:[2],observation:'В марте 12.',options:inputCase.options})};
   }
   return{ok:true,json:async()=>url==='/api/visual/models'?{models:['local/vision']}:{items:[inputCase]}};
 };
 class Option extends Node {constructor(title,value){super('option');this.textContent=title;this.value=value;}}
 class Image {constructor(){this.naturalWidth=1000;this.naturalHeight=600;}async decode(){}}
 const ctx=vm.createContext({document:doc,window:win,fetch,Option,Image,navigator:{},location:{origin,hash:capture?'#capture':''},performance,console});
 vm.runInContext(js,ctx);await settle();return {els,events,win,opener,origin,sent,calls};
}
test('practice grading works without inference; optional comparison sends only displayed chart and options',async()=>{
 const {els,calls}=await boot();
 assert.equal(els.picks.children.length,3);
 els.picks.children[1].onclick();
 assert.ok(els.grade.textContent.startsWith('Верно.'));
 assert.equal(calls.length,0);
 await els.solve.onclick();
 assert.equal(calls.length,1);
 assert.ok(calls[0].image.startsWith('data:image/jpeg;base64,'));
 assert.ok(!('choices' in calls[0]) && !('figure' in calls[0]) && !('explanation' in calls[0]));
 assert.equal(calls[0].model,'local/vision');
 assert.ok(els.answerPanel.children.some(x=>x.textContent==='Совпало с эталоном.'));
});
test('only opener from same origin may import captured frame; crop changes the pixels used',async()=>{
 const {els,events,opener,origin,sent}=await boot({capture:true});
 assert.equal(sent[0][0].type,'quizlens-visual-ready');
 assert.equal(sent[0][1],origin);
 const payload={type:'quizlens-visual-frame',image:'data:image/png;base64,dGVzdA==',question:'Вопрос?',options:['Первый','Второй']};
 events.message({data:payload,source:{},origin});
 events.message({data:payload,source:opener,origin:'https://another.example'});
 assert.equal(els.manualQ.value,'');
 events.message({data:payload,source:opener,origin});await settle();
 assert.equal(els.manualQ.value,'Вопрос?');
 assert.equal(els.manualOptions.value,'Первый\nВторой');
 assert.equal(els.photo.width,1000);
 els.crop.onclick();
 els.photoBox.onpointerdown({clientX:50,clientY:60,pointerId:1,preventDefault(){}});
 els.photoBox.onpointerup({clientX:300,clientY:210});
 assert.equal(els.photo.width,500);
 assert.ok(Math.abs(els.photo.height-300)<=1);
 els.resetCrop.onclick();assert.equal(els.photo.width,1000);assert.equal(els.photo.height,600);
});
