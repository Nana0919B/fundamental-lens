'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let currency='USD';
const hkTickers=['0700.HK','0941.HK','0291.HK','0883.HK','2899.HK','0669.HK','0002.HK','1211.HK','1177.HK','0005.HK','1109.HK','0001.HK','1308.HK'];
const money = n => Number.isFinite(n) ? (currency==='HKD'?'HK$':'US$') + n.toLocaleString('en-US',{maximumFractionDigits:2,minimumFractionDigits:2}) : '—';
const pct = n => Number.isFinite(n) ? (n*100).toFixed(2)+'%' : '—';
const table = (headers, rows) => `<div class="scroll"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
let current, timer, blobURL, generation = 0;
async function api(path, options) { const r = await fetch(path,options); const data = await r.json(); if(!r.ok) throw Error(data.error || r.status); return data; }
function message(text,error=false){$('status').textContent=text;$('status').className=error?'error':'';}
function chart(rows, keys, labels) {
  if(!rows.length) return '<p>无曲线数据</p>';
  const w=1080,h=280,pad=65, vals=rows.flatMap(r=>keys.map(k=>r[k])).filter(Number.isFinite);
  const low=Math.min(...vals)*.95, high=Math.max(...vals)*1.05, span=high-low||1;
  const colors=['#225d48','#ae8357','#8dadd0','#a46779'];
  let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(labels.join('与'))}历史曲线">`;
  for(let i=0;i<4;i++){const y=20+i*(h-65)/3;svg+=`<path d="M${pad},${y}H${w-15}" stroke="#e4e9e1"/><text x="2" y="${y+4}" font-size="11" fill="#6d7e71">${money(high-(high-low)*i/3)}</text>`;}
  keys.forEach((key,j)=>{const points=rows.map((r,i)=>`${pad+i*(w-pad-15)/Math.max(1,rows.length-1)},${20+(high-r[key])/span*(h-65)}`).join(' ');svg+=`<polyline points="${points}" fill="none" stroke="${colors[j]}" stroke-width="2"/>`;});
  svg+=`<text x="${pad}" y="${h-8}" font-size="12">${rows[0].date}</text><text x="${w-100}" y="${h-8}" font-size="12">${rows.at(-1).date}</text></svg>`;
  return svg+`<div class="legend">${labels.map((s,i)=>`<span style="color:${colors[i]}">${esc(s)}</span>`).join('')}</div>`;
}
function valuation(value, close, result){
  if(value.error)return `<p class="error">${esc(value.date)}：${esc(value.error)}</p>`;
  const factor=value.native_factor;
  return `${value.research_job?`<button type="button" data-open-research="${esc(value.date)}" data-ticker="${esc(result.ticker)}">在研究页打开同一份估值 ↗</button>`:''}<p>${esc(value.date)} · 当日股价 <b>${money(close*factor)}</b> · 最新可用年报 ${esc(value.period_end)}，披露于 ${esc(value.filed)}</p><div class="cards">${['bear','base','bull'].map((k,i)=>`<div class="metric">${['悲观','基准','乐观'][i]}每股价值<b>${value.scenarios[k].native_price<=0?"普通股残值≤0":money(value.scenarios[k].native_price)}</b><p>普通股内在价值 ${(value.scenarios[k].equity_value/1e9).toFixed(2)} 十亿 ${currency}<br>相对当日价格 ${pct(value.scenarios[k].price/close-1)}</p></div>`).join('')}<div class="metric">对应当时股数<b>${(value.native_shares/1e9).toFixed(3)} B</b><p>内在股权价值 ÷ 股数 = 每股估值</p></div></div><p class="muted">上述价格均为当日拆股前单位；跨日期曲线使用统一复权单位。股数以已披露年末股数为基础，仅调整已发生拆股。</p><details><summary>查看计算假设、现金流和原始申报字段</summary>${table(['情景',value.research_job?'首年营收增长':'现金流增长','WACC','永续增长'],['bear','base','bull'].map(k=>[k,pct(value.scenarios[k].growth),pct(value.scenarios[k].wacc),pct(value.scenarios[k].terminal_growth)]))}<p>${value.research_job?'统一研究引擎 '+esc(value.engine_version)+' · 输入指纹 '+esc(value.input_digest.slice(0,16)):'旧版正常化 FCFF：'+money(value.normalized_fcff)}；可分配现金桥 ${money(value.cash_bridge)}；债务 ${money(value.debt)}。</p>${value.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}${value.research_job?`<p>逐年预测、敏感性矩阵和反向 DCF 已由研究页同一入口计算；打开研究页可查看及调整假设。</p><pre>${esc(JSON.stringify(value.research_job.valuation.inputs,null,2))}</pre>`:''}${result.snapshots.filter(s=>value.source_periods.includes(s.end)).map(s=>`<details><summary>${esc(s.end)} · ${esc(s.filed)}</summary><pre>${esc(JSON.stringify(s.evidence,null,2))}</pre></details>`).join('')}</details>`;
}
function detail(index){
  const result=current.result.results[index];
  document.querySelectorAll('#tabs button').forEach((el,i)=>el.classList.toggle('active',i===index));
  const v=result.inspection,c=result.valuation_coverage;
  const monthly=result.decisions.filter(d=>!d.valuation.error).map(d=>({date:d.date,price:d.close,bear:d.valuation.scenarios.bear.price,base:d.valuation.scenarios.base.price,bull:d.valuation.scenarios.bull.price}));
  $('detail').innerHTML=`<section class="panel"><h2>${esc(result.ticker)} · 历史观察日</h2>${valuation(v,v.close,result)}</section><section class="panel"><h3>账户净值 · 策略与买入持有</h3><p>累计费用 ${money(result.fees)} · 持仓天数占比 ${pct(result.exposure)} · 有效估值 ${c.valid}/${c.total} 个月。${!result.trades.length?'该参数下没有触发交易，策略全程持有现金。':''}</p>${chart(result.curve,['strategy','benchmark'],['估值策略','买入持有'])}</section><section class="panel"><h3>月末股价与内在价值区间</h3><p class="muted">统一为行情抓取日的拆股单位，不含分红复权；仅绘制有估值的月份。</p>${chart(monthly,['price','bear','base','bull'],['股价','悲观价值','基准价值','乐观价值'])}</section><section class="panel"><h3>买卖记录</h3>${result.trades.length?table(['信号日','成交日','方向','当时开盘价','费用'],result.trades.map(t=>[t.signal_date,t.date,t.action,money(t.native_price),money(t.fee)])):'<p>没有成交。月末信号及未成交原因见下方。</p>'}</section><section class="panel"><h3>逐月估值与决策</h3><label class="signal-select">查看某次决策<select id="decision">${result.decisions.map((d,i)=>`<option value="${i}">${d.date} · ${d.action}${d.valuation.error?' · 数据不足':''}</option>`).join('')}</select></label><div id="decision-detail"></div><details><summary>全部月末信号</summary>${table(['月末','当时股价','悲观价值','乐观价值','信号','执行日 / 原因'],result.decisions.map(d=>[d.date,money(d.native_close),money(d.valuation.scenarios?.bear.native_price),money(d.valuation.scenarios?.bull.native_price),d.action,esc(d.execution_date||d.reason)]))}</details></section><details class="panel"><summary>数据来源与归档指纹</summary>${result.sources.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a><br>抓取 ${esc(s.retrieved_at)}<br><code>${esc(s.sha256)}</code></p>`).join('')}</details>`;
  const show=()=>{const d=result.decisions[Number($('decision').value)];$('decision-detail').innerHTML=d?`<p class="badge">${esc(d.action)} · ${esc(d.reason)}${d.execution_date?' · '+esc(d.execution_date)+' 开盘成交':''}</p>`+valuation(d.valuation,d.close,result):'<p>区间内没有月末信号</p>';};
  $('decision').addEventListener('change',show);show();
}
function render(row){
  currency=row.parameters.tickers.some(t=>t.endsWith(".HK"))?"HKD":"USD";
  current=row; $('results').hidden=false;
  $('assumption-source').options[1].disabled=!row.parameters.research_template;
  $('assumption-source').value=row.parameters.research_template?'saved':'default';
  for(const k of ['start','end','inspect_date','capital','cost_bps'])$(k).value=row.parameters[k];
  $('safety_margin').value=row.parameters.safety_margin*100;
  const selection=row.parameters.tickers.every(t=>hkTickers.includes(t))&&row.parameters.tickers.length===13?'HK':row.parameters.tickers.join(',');
  if(!Array.from($('tickers').options).some(o=>o.value===selection))$('tickers').add(new Option(selection,selection));
  $('tickers').value=selection;
  $('comparison').innerHTML=table(['股票 / 行业','策略总收益','买入持有总收益','策略年化','买入持有年化','策略最大回撤','持有最大回撤','成交次数'],row.result.results.map(r=>[esc(r.ticker+' '+(r.name||'')+' '+(r.industry||'')),pct(r.strategy.total_return),pct(r.benchmark.total_return),pct(r.strategy.cagr),pct(r.benchmark.cagr),pct(r.strategy.max_drawdown),pct(r.benchmark.max_drawdown),r.trades.length]));
  if(currency==='HKD')$('comparison').insertAdjacentHTML('afterbegin','<p class="error">行业迁移试验：沿用研究默认增长和利润率，现金、投资及其他权益采用已列明的代理口径。以下是模型试算，不是完成公司专项核实的目标价；未买入不等于市场一定高估。</p>');
  $('comparison').innerHTML+=Object.entries(row.result.excluded||{}).map(([t,v])=>`<p class="muted">${esc(t)} ${esc(v.name)} · ${esc(v.industry)}：${esc(v.exclusion)}</p>`).join('');
  $('tabs').innerHTML=row.result.results.map((r,i)=>`<button data-index="${i}">${esc(r.ticker)}</button>`).join('');
  $('tabs').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>detail(Number(b.dataset.index))));
  $('detail').innerHTML='';if(row.result.results.length)detail(0);
  $('method').innerHTML=`<p>模型版本 ${esc(row.result.version)} · 本次参数：${esc(JSON.stringify({...row.parameters,research_template:row.parameters.research_template?{source_job_id:row.parameters.research_template.source_job_id,as_of_date:row.parameters.research_template.as_of_date,input_digest:row.parameters.research_template.input_digest}:undefined}))}</p><ul>${row.result.limitations.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`;
  if(blobURL)URL.revokeObjectURL(blobURL);blobURL=URL.createObjectURL(new Blob([JSON.stringify(row,null,2)],{type:'application/json'}));$('download').href=blobURL;$('download').download=`valuation-backtest-${row.id}.json`;$('download').hidden=false;
}
async function load(id, token){
  if(token!==generation)return;
  try{const row=await api('/api/valuation-backtests/'+id);if(token!==generation)return;
    if(['queued','running'].includes(row.status)){message('正在获取历史数据并回测；首次运行可能需要数分钟，可关闭页面后回来查看。');$('run').disabled=true;timer=setTimeout(()=>load(id,token),2000);return;}
    $('run').disabled=false;
    if(row.result){render(row);const errors=Object.entries(row.result.errors).map(([k,v])=>k+': '+v).join('；');message(errors?'部分数据失败：'+errors:(row.result.version.startsWith('research-')?'统一研究引擎验证完成；每个历史估值可直接在研究页打开。':'这是旧版独立模型记录，请重新运行以使用研究页统一引擎。'),!!errors);}else message(row.error||'任务未完成',true);
    await loadHistory(id);
  }catch(e){$('run').disabled=false;message(e.message,true);}
}
async function loadHistory(selected){const rows=await api('/api/valuation-backtests');$('history').innerHTML='<option value="">选择记录</option>'+rows.map(r=>`<option value="${r.id}">${esc(new Date(r.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Singapore',hour12:false}))} · ${esc(r.parameters.tickers.join('/'))} · ${esc(r.parameters.start)}～${esc(r.parameters.end)} · ${esc(r.status)}</option>`).join('');if(selected)$('history').value=selected;return rows;}
function select(id){clearTimeout(timer);generation++;$('results').hidden=true;$('download').hidden=true;window.history.replaceState(null,'','/backtest?id='+id);load(id,generation);}
$('history').addEventListener('change',()=>{if($('history').value)select($('history').value);});
$('form').addEventListener('submit',async e=>{e.preventDefault();$('run').disabled=true;message('正在建立回测…');try{const p={tickers:$('tickers').value==='ALL'?['AAPL','MSFT','NVDA']:$('tickers').value==='HK'?hkTickers:$('tickers').value.split(',')};for(const k of ['start','end','inspect_date'])p[k]=$(k).value;for(const k of ['capital','safety_margin','cost_bps'])p[k]=Number($(k).value);p.safety_margin/=100;if($('assumption-source').value==='saved'&&current?.parameters.research_template)p.template_run_id=current.id;const row=await api('/api/valuation-backtests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});select(row.id);}catch(e){$('run').disabled=false;message(e.message,true);}});
(async()=>{try{const rows=await loadHistory();const id=new URLSearchParams(location.search).get('id')||rows[0]?.id;if(id)select(id);else message('选择期间后运行。无需模型调用或额外登录。');}catch(e){message(e.message,true);}})();

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-open-research]');
  if(!button)return;
  button.disabled=true;
  try{
    const record=await api('/api/valuation-backtests/'+current.id+'/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:button.dataset.ticker,date:button.dataset.openResearch})});
    location.href=record.url;
  }catch(error){message(error.message,true);button.disabled=false;}
});

$('tickers').addEventListener('change',()=>{if($('tickers').value==='HK'||$('tickers').value.endsWith('.HK')){$('start').value='2025-05-01';$('end').value='2025-12-31';$('inspect_date').value='2025-05-30';$('cost_bps').value='25';message('港股行业试验：金额为港币；默认25基点为每边综合费用假设；3个不适用行业单独列示。');}});
