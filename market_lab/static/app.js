'use strict';
// Localhost-only synthetic simulator. No third-party libraries, telemetry or data.
const $ = (id) => document.getElementById(id);
let state = null;
let playing = false;
let pending = false;
let timer = null;
let initialPrice = null;

function number(x, places = 1) { return x === null || x === undefined ? '—' : Number(x).toLocaleString('ru-RU', {minimumFractionDigits:places,maximumFractionDigits:places}); }
async function request(path, body) {
 const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if (!response.ok) {const error = await response.json(); throw new Error(error.error || `HTTP ${response.status}`);}
 return response.json();
}
function setError(value){$('error').textContent = value || '';}
function stop() { playing=false; clearInterval(timer); timer=null; $('play').textContent='▶ Запуск'; }
async function step(count=1) {
 if(pending || !state || state.done) return;
 pending = true;
 try { state = await request('/api/step', {count}); render(); if(state.done) stop(); }
 catch(e) {stop();setError('Ошибка: '+e.message);}
 finally {pending=false;}
}
function togglePlay() {
 if (playing) {stop();return;}
 if(!state || state.done) return;
 playing = true; $('play').textContent='Ⅱ Пауза';
 // Clock advances only on explicit browser ticks; paused UI never changes market.
 timer=setInterval(() => step(Number($('speed').value)), 190);
}
function settings() {
 const fields={seed:'seed',ticks:'ticks',shockTick:'shock_tick',cancelPercent:'cancel_percent',sellQty:'sell_qty',eventsPerTick:'events_per_tick'};
 const cfg={};
 for(const [id,key] of Object.entries(fields)) {const raw=$(id).value.trim(); if(!/^-?\d+$/.test(raw)) throw new Error(`Введите целое число: ${id}`); cfg[key]=Number(raw);}
 cfg.shock_enabled=$('shockEnabled').checked;
 return cfg;
}
async function reset() {
 stop(); if(pending) return;
 pending=true;
 try {state=await request('/api/reset',settings());initialPrice=state.market.last_price;setError('');render();}
 catch(e){setError('Неверные параметры: '+e.message);}
 finally{pending=false;}
}
function bookRows(orders, type) {
 if(!orders.length) return '<div class="empty">В стакане нет заявок</div>';
 const sum=orders.reduce((acc,[,q])=>acc+q,0);let cum=0;
 return orders.map(([p,q])=>{cum+=q;return `<div class="book-row ${type}"><div class="shade" style="width:${(cum/Math.max(sum,1)*100).toFixed(1)}%"></div><span>${number(p,1)}</span><span>${q}</span><span>${cum}</span></div>`;}).join('');
}
function render() {
 if(!state) return;
 const m=state.market, cfg=state.config;
 $('last').textContent=number(m.last_price,1);
 $('spread').textContent=number(m.spread_bps,2);
 $('volume').textContent=state.volume.toLocaleString('ru-RU');
 $('trades').textContent=state.total_trades.toLocaleString('ru-RU');
 $('depth').textContent=m.bid_depth_8;
 $('tick').textContent=state.tick; $('maxTicks').textContent=cfg.ticks;
 $('progress').style.width=`${100*state.tick/cfg.ticks}%`;
 $('shockMarker').style.left=`${100*cfg.shock_tick/cfg.ticks}%`;
 $('runStatus').textContent=state.done?'ЗАВЕРШЁН':playing?'СИМУЛЯЦИЯ ИДЁТ':'ПАУЗА / ГОТОВ';
 $('barCount').textContent=`${state.candles.length} свечей`;
 $('priceDelta').textContent=initialPrice===null?'Цена формируется виртуальными сделками':`От начальной: ${m.last_price>=initialPrice?'+':''}${number(m.last_price-initialPrice,1)} VCU`;
 $('midPrice').textContent=number(m.last_price,1);
 $('bidask').textContent=`${number(m.best_bid,1)} / ${number(m.best_ask,1)}`;
 $('asks').innerHTML=bookRows([...state.best_asks].reverse(),'ask');
 $('bids').innerHTML=bookRows(state.best_bids,'bid');
 const acts=state.agent_actions;
 const total=Math.max(1,Object.values(acts).reduce((a,b)=>a+b,0));
 const names={NOISE:'NOISE',MAKER:'MAKER',TREND:'TREND',REVERSION:'REVERSION'};
 $('agents').innerHTML=Object.entries(names).map(([kind,name])=>`<div class="agent-line"><div class="agent-label">${name}</div><div class="agent-track"><div style="width:${acts[kind]/total*100}%"></div></div><div class="agent-count">${acts[kind]}</div></div>`).join('');
 $('shockReport').innerHTML=state.shock
 ? `<strong>${state.shock.enabled?'СНИЖЕНИЕ ЛИКВИДНОСТИ':'КОНТРОЛЬ'}</strong><div>Тик ${state.shock.tick} · отменено ${state.shock.cancelled_qty} BUY ед.</div><div>Глубина до / после: ${state.shock.bid_depth_before} / ${state.shock.bid_depth_after}</div><div>Продано: ${state.shock.sell_filled} из ${state.shock.sell_requested} ед. · Остаток: ${state.shock.sell_unfilled}</div><div>Проскальзывание: ${number(state.shock.slippage_bps,2)} bps</div>`
 : `Событие ещё не произошло · тик ${cfg.shock_tick}`;
 $('tradeFeed').innerHTML=state.recent_trades.length
 ? [...state.recent_trades].reverse().slice(0,18).map(t=>`<div class="trade-row"><span>${t.tick}</span><span class="${t.side==='BUY'?'buy':'sell'}">${t.side}</span><span>${number(t.price,1)}</span><span>${t.qty}</span></div>`).join('')
 : '<div class="empty">Сделки появятся после запуска</div>';
 $('empty').classList.toggle('hide',state.tick>0);
 $('step').disabled=state.done; $('play').disabled=state.done;
 drawCandles();
}
function drawCandles() {
 const canvas=$('candleCanvas');if(!canvas||!state)return;
 const box=canvas.getBoundingClientRect();const width=Math.max(box.width,100),height=Math.max(box.height,140),scale=Math.min(window.devicePixelRatio||1,2);
 canvas.width=width*scale;canvas.height=height*scale;
 const ctx=canvas.getContext('2d');ctx.scale(scale,scale);
 const left=15,right=62,top=18,bottom=16,volHeight=56,plotBottom=height-bottom-volHeight-12;
 const plotH=plotBottom-top;ctx.clearRect(0,0,width,height);
 const candles=state.candles.slice(-66);if(!candles.length)return;
 let min=Math.min(...candles.map(x=>x.low)),max=Math.max(...candles.map(x=>x.high));
 const range=Math.max(max-min,0.8);min-=range*.12;max+=range*.12;
 const toY=(p)=>top+(max-p)/(max-min)*plotH;
 ctx.strokeStyle='#243748';ctx.lineWidth=1;ctx.fillStyle='#8ba2b8';ctx.font='11px ui-monospace,monospace';
 for(let i=0;i<=4;i++){const y=top+plotH*i/4;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(number(max-(max-min)*i/4,1),width-right+6,y+3);}
 const plotWidth=width-right-left;const step=plotWidth/candles.length;const bw=Math.max(2,Math.min(11,step*.62));
 const maxVolume=Math.max(...candles.map(x=>x.volume),1);
 for(let i=0;i<candles.length;i++){
  const c=candles[i],x=left+step*(i+.5),up=c.close>=c.open,col=up?'#1fc6a0':'#f37375';
  if(c.start<=state.config.shock_tick&&c.start+state.config.candle_ticks>state.config.shock_tick){
   ctx.strokeStyle='#efc16d';ctx.setLineDash([4,5]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,height-bottom);ctx.stroke();ctx.setLineDash([]);
   ctx.fillStyle='#efc16d';ctx.font='10px system-ui';ctx.fillText('SHOCK',Math.min(x+5,width-90),top+9);
  }
  ctx.strokeStyle=col;ctx.fillStyle=col;
  ctx.beginPath();ctx.moveTo(x,toY(c.high));ctx.lineTo(x,toY(c.low));ctx.stroke();
  const y1=toY(c.open),y2=toY(c.close);ctx.fillRect(x-bw/2,Math.min(y1,y2),bw,Math.max(1.8,Math.abs(y2-y1)));
  ctx.globalAlpha=.55;const h=c.volume/maxVolume*volHeight;ctx.fillRect(x-bw/2,height-bottom-h,bw,h);ctx.globalAlpha=1;
 }
 ctx.strokeStyle='#355267';ctx.beginPath();ctx.moveTo(left,plotBottom+6);ctx.lineTo(width-right,plotBottom+6);ctx.stroke();
 ctx.font='10px ui-monospace,monospace';ctx.fillStyle='#7c98ac';ctx.fillText('VOLUME',left,plotBottom+18);
}
$('play').addEventListener('click',togglePlay);
$('step').addEventListener('click',()=>step(1));
$('reset').addEventListener('click',reset);$('apply').addEventListener('click',reset);
window.addEventListener('resize',()=>drawCandles());
document.addEventListener('visibilitychange',()=>{if(document.hidden&&playing)stop();});
(async()=>{try{state=await request('/api/state');initialPrice=state.market.last_price;render();}catch(e){setError('Локальный сервер недоступен: '+e.message);}})();
