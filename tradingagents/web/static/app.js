const $=s=>document.querySelector(s), tabs=['研究简报','公司资料','经营分析','财务报告','财务指标','新闻分析','比较分析','市场环境','内在价值'];
let selected=new URLSearchParams(globalThis.location?.search||'').get('job')||null,job=null,tab=globalThis.location?.hash==='#valuation'?8:globalThis.location?.hash==='#market-environment'?7:0,frequency='annual',statement='income',jobs=[],loading=false;
let selectionRevision=0,submitting=false,chartMode='revenue',toastTimer=null;
let metricKey='net_margin',financialQuery='',financialRow='';
let environmentMetric='score',environmentRange=30;
const jobCache=new Map(), reportCache=new Map();
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
const pct=v=>v==null?'—':num(v*100)+'%';
const money=v=>v==null?'—':Math.abs(v)>=1e12?num(v/1e12)+' 万亿':Math.abs(v)>=1e8?num(v/1e8)+' 亿':Math.abs(v)>=1e4?num(v/1e4)+' 万':num(v);
async function api(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
function error(message){for(const id of ['#error','#form-error']){const node=$(id);node.hidden=!message;node.textContent=message||'';}}
function empty(message){return `<div class="card"><p class="note">${escape(message)}</p></div>`;}
function card(title,body){return `<div class="card"><h3>${title}</h3>${body}</div>`;}
function stat(label,value,unit=''){return `<div class="stat"><small>${label}</small><strong>${value}</strong><em>${escape(unit||'未提供的数据以 — 显示')}</em></div>`;}
function evidencePanel(){const refs=prioritizedEvidence(job.display_evidence||[]);return refs.length?`<details class="card" id="evidence-panel"><summary>分析依据 · 指标、期间与来源</summary><p class="note">这里说明分析使用了哪些数据，以及这些数据代表什么。财务报告页优先展示当前报表的关键科目依据，其他资料仍完整保留。点击报告中的引用可定位到对应行；数据支持某种判断，不代表已证实其变化原因。</p><div class="table-wrap"><table><thead><tr><th>指标</th><th>报告期间</th><th>数据值</th><th>如何理解</th><th>来源</th></tr></thead><tbody>${refs.map(r=>`<tr id="evidence-${escape(r.id)}"><td>${escape(r.label)}</td><td>${escape(r.period)}</td><td>${escape(r.display_value)}</td><td>${escape(r.explanation)}</td><td>${escape(r.source)}</td></tr>`).join('')}</tbody></table></div></details>`:'';}

function versionsPanel(){const versions=job.versions||[];return versions.length?card('研究版本',table(['研究日期','采集时间','报告状态'],versions.map(v=>[v.as_of_date,v.retrieved_at,v.has_report?'已生成':'数据已归档']))+`<p class="note">每次研究生成新版本，不覆盖历史数据。可在左侧选择对应任务；本地版本差异不等同于公司披露更正。</p>${job.report_html?.company_brief?`<a href="/api/jobs/${encodeURIComponent(job.id)}/export" target="_blank" rel="noopener">导出完整 HTML 研究报告 ↗</a>`:''}`):'';}
function companyExtras(){const p=job.data?.profile||{},officers=p.companyOfficers||[],changes=job.profile_changes;return card('管理层与股权概览',table(['姓名','职位','年龄'],officers.map(o=>[o.name||'—',o.title||'—',o.age||'—']))+`<p class="note">内部人持股：${pct(p.heldPercentInsiders)} · 机构持股：${pct(p.heldPercentInstitutions)}。为供应商概览，不代表完整股东名册。</p>`)+(changes?.previous_id?card('公司档案变化',table(['字段','前次采集','本次采集'],changes.changes.map(c=>[c.field,typeof c.before==='object'?JSON.stringify(c.before):c.before??'—',typeof c.after==='object'?JSON.stringify(c.after):c.after??'—']))+`<p class="note">${escape(changes.note)}</p>`):'');}
function filingPanel(){const f=job.data?.filings;if(!f)return '';return card('原始报告、附注与修订文件',(f.documents||[]).map(d=>`<p><a href="${escape(d.url)}" target="_blank" rel="noopener noreferrer">${escape(d.form)} · 报告期 ${escape(d.period)} · 披露 ${escape(d.filed)}${d.amendment?' · 修订文件':''} ↗</a></p>`).join('')+`<p class="note">${escape(f.note)} 此处提供原文入口，尚未自动抽取附注、产品及地区拆分。</p>`);}
function calculatedMetrics(){const a=job.research_snapshot?.analytics;if(!a)return '';const defs=a.definitions||{},history=a.history||[];return card('统一公式 · 历史财务指标',table(['年度',...Object.values(defs).map(d=>d.label)],history.map(h=>[h.period,...Object.entries(defs).map(([k,d])=>d.unit==='ratio'?pct(h.values[k]):d.unit==='currency'?money(h.values[k]):num(h.values[k]))])))+card('TTM 与同比 / 环比',table(['TTM 项目','数值'],Object.entries(a.ttm?.values||{}).map(([k,v])=>[k,money(v)]))+`<p class="note">${escape(a.ttm?.note)}</p>`+table(['指标','报告期','比较期','口径','增长率'],(a.growth||[]).map(g=>[g.field,g.period,g.base_period,g.basis,pct(g.value)+(g.note?'（'+g.note+'）':'')])) )+`<details class="card"><summary>计算公式与口径（版本 ${escape(a.formula_version)}）</summary>${table(['指标','公式','单位'],Object.values(defs).map(d=>[d.label,d.formula,d.unit]))}<p class="note">${(a.limitations||[]).map(escape).join('<br>')}</p></details>`;}
function alignedComparison(){const c=job.research_snapshot?.comparison;if(!c)return '';return card('比较口径与样本分位',table(['公司','行业','财报时间戳','期间对齐','行业标签一致'],c.rows.map(r=>[r.symbol,r.industry||'—',r.financial_period?new Date(r.financial_period*1000).toISOString().slice(0,10):'—',r.period_aligned?'是':'否 / 未知',r.same_industry?'是':'否 / 未知']))+table(['指标','可比样本数','所选样本分位'],c.distributions.map(d=>[d.metric,d.sample_size,d.percentile==null?'样本不足或口径不符':num(d.percentile)+'%']))+`<p class="note">${escape(c.basis)}<br>${escape(c.currency_policy)}</p>`);}
function source(){const d=job.data;return d?`<p class="note">来源：${escape(d.source)} · 获取时间 ${escape(new Date(d.retrieved_at).toLocaleString())}<br>${job.research_snapshot?.point_in_time?'历史重建研究：按当时公开资料重新生成，非当年留存预测。':job.historical_context?'历史规则估值快照：未生成当年的AI文字研究。':job.imported?'历史报告的数据可能与后来补充的资料不同。':'页面与智能体使用同一份研究数据。'}研究日期：${escape(job.date)}。<br>${escape(d.market?.label||'')} ${escape(d.market?.coverage_note||'')}<br>${escape(d.period_note)} ${escape((d.errors||[]).join('；'))}</p>`:'';}

function report(key){return displayReport(job.report_html?.[key])||'<p class="note">报告尚未生成。完整分析需要数分钟，完成后自动显示。</p>';}
function specialistReport(key,title){const body=job.report_html?.[key];const pending=['queued','running'].includes(job.status);return card(title,body?`<div class="prose">${displayReport(body)}</div>`:`<p class="note">${pending?'智能体正在基于本次财务数据生成独立分析，完成后自动展示。':'本次报告尚无此项智能体分析；请重新发起研究以生成结论、变化解释和风险提示。'}</p>`);}
function table(headers,rows){return `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${escape(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
const fieldNames={'Total Revenue':'营业收入','Net Income':'净利润','Total Assets':'总资产','Stockholders Equity':'股东权益','Operating Cash Flow':'经营活动现金流','Capital Expenditure':'资本开支','Free Cash Flow':'自由现金流','Gross Profit':'毛利润','Cost Of Revenue':'营业成本','Operating Income':'营业利润','Investing Cash Flow':'投资活动现金流','Financing Cash Flow':'融资活动现金流','Cash Dividends Paid':'现金分红','Stock Based Compensation':'股份支付费用','Name':'姓名','Position':'职务','Start Date':'交易日期','Shares':'股数','Value':'金额','Text':'交易说明','Transaction':'交易类型','longName':'公司名称','sector':'板块','industry':'行业','marketCap':'市值','trailingPE':'市盈率（TTM）','returnOnEquity':'ROE','profitMargins':'净利率','financialCurrency':'财报币种'};
function fieldName(name){return fieldNames[name]||(job.display_evidence||[]).find(r=>r.field===name)?.label||name;}
function fundamentalSources(){const d=job.data;if(!d)return empty('研究资料尚未获取，完成后将展示五类基本面资料。');const p=d.profile||{},statements=d.statements||{};let html=card('基本面研究资料',`<details><summary>公司基本面（fundamentals）</summary>${table(['项目','内容'],Object.entries(p).filter(([k,v])=>v!=null&&typeof v!=='object'&&k!=='longBusinessSummary').map(([k,v])=>[fieldName(k),typeof v==='number'?num(v):v]))}</details>`+[['balance','资产负债表（balance_sheet）'],['cashflow','现金流量表（cashflow）'],['income','利润表（income_statement）']].map(([key,label])=>`<details><summary>${label}</summary>${['annual','quarterly'].map(f=>{const t=statements[f]?.[key];return `<h4>${f==='annual'?'年度':'季度'}</h4>`+(t?.rows?.length?table(['科目',...t.periods],t.rows.map(r=>[fieldName(r.name),...r.values.map(v=>num(v))])):'<p class="note">数据源未提供此报表。</p>');}).join('')}</details>`).join('')+`<details><summary>内部人交易（insider_transactions）</summary>${d.insider_transactions?.rows?.length?table(d.insider_transactions.columns.map(fieldName),d.insider_transactions.rows):'<p class="note">'+escape(d.insider_transactions?.note||'本次历史研究未包含内部人交易数据；请重新发起研究获取。')+'</p>'}<p class="note">${escape(d.insider_transactions?.rows?.length?d.insider_transactions.note:'')} 内部人交易不是管理层持股比例。</p></details>`);return html;}
function trend(rowName,label){const t=job.data?.statements?.annual?.income;const row=t?.rows.find(r=>r.name===rowName);if(!row)return '';const pairs=t.periods.map((p,i)=>({p,v:row.values[i]})).filter(x=>x.v!=null).slice(0,5).reverse();const max=Math.max(...pairs.map(x=>Math.abs(x.v)),1);return card(label,`<p class="note">${escape(job.data.profile.financialCurrency||'报告币种未提供')} · 年度财务数据；负值使用文字标示</p><div class="chart">${pairs.map(x=>`<div class="bar-col"><small>${money(x.v)}</small><svg width="100%" height="120" aria-hidden="true"><rect x="20%" width="60%" y="${120-Math.round(Math.abs(x.v)/max*110)}" height="${Math.round(Math.abs(x.v)/max*110)}" fill="#7eac98" rx="4"/></svg><small>${escape(x.p.slice(0,4))}</small></div>`).join('')}</div>`);}

// View helpers extend the existing report/data renderers; values still come from /api/jobs.
const iconPaths = {
  grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  layers:'m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  search:'m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z',
  arrow:'M5 12h14m-5-5 5 5-5 5', check:'m5 12 4 4L19 6',
  file:'M14 2H6v20h12V6l-4-4Zm0 0v5h5M8 12h8M8 16h8',
  clock:'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  download:'M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5',
  shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6'
};
function icon(name,size=17){return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${iconPaths[name]||iconPaths.file}"/></svg>`;}
function excerpt(text,limit){
  const plain=String(text||'').replace(/[（(][^()（）]*(?:研究资料|快照)[^()（）]*[）)]/g,'').replace(/annual growth数组/g,'年度同比').replace(/calculated_metrics\.history/g,'历史统一公式').replace(/\s+/g,' ').trim();
  return plain.length>limit?plain.slice(0,limit).replace(/[，、；：][^，。；：]*$/,'')+'…':plain;
}
function displayReport(html){
  if(!html)return '';
  if(reportCache.has(html))return reportCache.get(html).html;
  const root=document.createElement('div');
  root.innerHTML=html;
  root.querySelectorAll('p').forEach(p=>{
    if(/^(快照信息|数据快照\s*ID|快照编号|数据基准)\s*[：:]/.test(p.textContent.trim()))p.classList.add('report-meta');
  });
  root.querySelectorAll('th').forEach(th=>{
    if(/证据\s*ID/.test(th.textContent))th.textContent='数据依据';
  });
  const result=root.innerHTML.replace(/快照信息/g,'研究资料说明').replace(/快照\s*ID|快照编号/g,'研究资料').replace(/(?:ID\s*)?本次研究数据/g,'本次研究资料');
  if(reportCache.size>40)reportCache.clear();
  reportCache.set(html,{html:result});
  return result;
}
function reportParagraph(key,limit=140,section='核心结论'){
  const html=displayReport(job.report_html?.[key]);
  if(!html)return '';
  const root=document.createElement('div');root.innerHTML=html;
  const heading=[...root.querySelectorAll('h1,h2,h3,h4')].find(h=>h.textContent.includes(section));
  let node=heading?.nextElementSibling;
  while(node&&!/^H[1-4]$/.test(node.tagName)){
    const text=node.textContent.trim();
    if(text.length>35)return excerpt(text,limit);
    node=node.nextElementSibling;
  }
  const candidates=[...root.querySelectorAll('p')].map(p=>p.textContent.trim());
  const text=candidates.find(t=>t.length>45&&!/^(来源[：:]|数据来源[：:]|知识边界|身份确认|标的确认|研究资料说明|研究资料|数据基准)/.test(t));
  return excerpt(text||'',limit);
}
function pendingReportText(){
  return ['queued','running'].includes(job.status)?'智能体正在生成本次研究，完成后会自动展示。':'本次归档尚未包含这项分析，可新建研究补充。';
}
function fixed(value,d=1){return value==null||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});}
function yi(value){return value==null||!Number.isFinite(Number(value))?'—':fixed(value/1e8,1);}
function monogram(name){const text=String(name||'');return text.match(/[A-Z]/i)?.[0].toUpperCase()||text.slice(0,1)||'·';}
function statusLabel(status){return {completed:'已完成研究',running:'分析中',queued:'等待分析',failed:'研究未完成',paused:'已暂停'}[status]||'研究归档';}
function ratingLabel(value){return {Buy:'买入',Overweight:'增持',Hold:'持有',Underweight:'减持',Sell:'卖出'}[value]||value||'尚未生成';}
function annualSeries(rowName,kind='income',includeMissing=false){
  const t=job.data?.statements?.annual?.[kind],row=t?.rows?.find(r=>r.name===rowName);
  if(!row)return [];
  return t.periods.map((period,i)=>({period,year:period.slice(0,4),value:row.values[i]}))
    .filter(x=>includeMissing||(x.value!=null&&Number.isFinite(Number(x.value)))).slice(0,5).reverse();
}
function lastPair(name,kind='income'){
  const series=annualSeries(name,kind);return series.length>=2?series.slice(-2):null;
}
function researchHeadline(){
  if(!job.report_html?.fundamentals_report)return '基本面研究，<br>等待本次分析完成。';
  const revenue=lastPair('Total Revenue'),profit=lastPair('Net Income'),cash=lastPair('Free Cash Flow','cashflow');
  if(revenue&&profit&&revenue[1].value>revenue[0].value&&profit[1].value>profit[0].value){
    return cash&&cash[1].value<cash[0].value?'营收与利润增长，<br>现金转化仍需观察。':'营收与利润增长，<br>结合现金流看经营质量。';
  }
  if(revenue&&revenue[1].value>revenue[0].value)return '营收延续增长，<br>盈利质量值得跟踪。';
  return '经营表现与财务质量，<br>从本次研究资料看起。';
}
function supplierGrowthNote(p){
  if(p.revenueGrowth==null)return '最近报告期增速未提供';
  const value=Number(p.mostRecentQuarter),parsed=new Date(value*1000),date=Number.isFinite(value)&&value>0&&Number.isFinite(parsed.getTime())?parsed.toISOString().slice(0,10):'';
  return `<strong>${p.revenueGrowth>=0?'+':''}${pct(p.revenueGrowth)}</strong> 最近报告期同比<br><span>${date?'截至 '+escape(date):'来源未标注比较期间'} · 非 TTM 增速</span>`;
}
function financialOverview(){
  const p=job.data?.profile||{},currency=p.financialCurrency||'币种未提供';
  const fields=[
    ['营业收入 <small>TTM</small>',yi(p.totalRevenue),'亿 '+currency,supplierGrowthNote(p)],
    ['归母净利润 <small>TTM</small>',yi(p.netIncomeToCommon),'亿 '+currency,'净利率 '+(p.profitMargins==null?'—':fixed(p.profitMargins*100,1)+'%')],
    ['市盈率 · TTM',fixed(p.trailingPE,2),'倍',p.trailingPE==null?'缺失或不适用，不以零替代':'供应商披露口径'],
    ['净资产收益率',p.returnOnEquity==null?'—':num(p.returnOnEquity*100,1),p.returnOnEquity==null?'':'%','ROE · 当前归档资料']
  ];
  return fields.map(([label,value,unit,note])=>`<div class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}<em>${escape(unit)}</em></div><div class="metric-note">${note}</div></div>`).join('');
}
function trendChart(){
  const series=annualSeries(chartMode==='revenue'?'Total Revenue':'Net Income');
  const latest=series.at(-1),max=Math.max(1,...series.map(x=>Math.abs(x.value)));
  const currency=job.data?.profile?.financialCurrency||'币种未提供';
  return `<div class="side-top"><h3>年度经营趋势</h3><div class="chart-tabs"><button class="chart-tab ${chartMode==='revenue'?'active':''}" data-chart="revenue" aria-pressed="${chartMode==='revenue'}">营收</button><button class="chart-tab ${chartMode==='income'?'active':''}" data-chart="income" aria-pressed="${chartMode==='income'}">利润</button></div></div>`+
    (latest?`<div class="chart-total">${yi(latest.value)}<span>亿 ${escape(currency)}</span></div><p class="chart-caption">FY${escape(latest.year)} · ${chartMode==='revenue'?'年度营业收入':'年度净利润'}</p><div class="trend-bars" aria-label="年度财务数据">${series.map((x,i)=>`<button class="trend-bar" aria-label="${escape(x.year)} 年 ${yi(x.value)} 亿 ${escape(currency)}"><span class="bar-fill ${i===series.length-1?'latest':''} ${x.value<0?'negative':''}" style="height:${Math.max(6,Math.abs(x.value)/max*96)}px"><span class="bar-tooltip">${yi(x.value)} 亿</span></span><span class="bar-year">${escape(x.year)}${x.value<0?' −':''}</span></button>`).join('')}</div><p class="chart-legend">来源：年度利润表 · 未作币种换算${series.some(x=>x.value<0)?'；棕色柱与负号表示负值':''}</p>`:'<p class="note">本次资料尚无可展示的年度趋势。</p>');
}
function researchRail(){
  const r=job.report_html||{},active=['queued','running'].includes(job.status);
  const steps=[['file','财务与经营研究',['fundamentals_report','operating_report','metrics_report'].every(k=>r[k])],['layers','多空观点讨论',Boolean(r.investment_plan)],['shield','风险评估',Boolean(r.final_trade_decision)],['check','形成综合判断',job.status==='completed'&&Boolean(r.final_trade_decision)]];
  const macro=job.macro_context||job.macro_update;
  const sources=[['Yahoo Finance',job.data?'财务与公司资料':'待获取'],...(macro?[['FRED','宏观指标']]:[]),...(job.news_context?[['TrendRadar','新闻资料']]:[]),...((job.market_environment_update||job.market_environment)?[[(job.market_environment_update||job.market_environment).source,'市场环境']]:[])];
  return `<div class="right-rail"><section class="side-section" id="trend-chart">${trendChart()}</section><section class="side-section"><div class="side-top"><h3>本次研究脉络</h3><span class="mini-label">${job.status==='completed'?'已归档':escape(statusLabel(job.status))}</span></div><div class="process-list">${steps.map(([i,label,done])=>`<div class="process-line"><span>${icon(i,15)}${label}</span><small class="${done?'done':''}">${done?icon('check',13):active?'进行中':'未生成'}</small></div>`).join('')}</div></section><section class="side-section"><div class="side-top"><h3>资料与来源</h3>${icon('layers',15)}</div><div class="source-list">${sources.map(([name,kind])=>`<div class="source-row"><span>${name}</span><small>${kind}</small></div>`).join('')}<div class="source-row"><span>研究日期</span><small>${escape(job.date)}</small></div></div><p class="source-foot">采用本次研究归档的资料。不同期间、单位与供应商口径，请在具体指标中核对。</p><button class="text-button" data-show-sources style="margin-top:15px">查看数据口径 ${icon('arrow',14)}</button></section></div>`;
}
function researchCard(title,number,body,footer=''){
  return `<section class="card"><div class="card-heading"><h3><span class="section-no">${number}</span>${title}</h3><span class="mini-label">${icon('file',13)} ${job.status==='completed'?'已归档':escape(statusLabel(job.status))}</span></div>${body}${footer?`<div class="card-bottom">${footer}</div>`:''}</section>`;
}
function reportButton(key,title,label){return `<button class="text-button" data-report="${key}" data-report-title="${title}">${label} ${icon('arrow',15)}</button>`;}
function summaryMarkup(text){return escape(text).replace(/(?:[+-]?\d[\d,.]*)(?:%|\s*亿(?:美元|元)?)/g,'<strong>$&</strong>');}
function summaryFact(key){
  const row=annualSeries(key==='operating_report'?'Total Revenue':'Free Cash Flow',key==='operating_report'?'income':'cashflow').at(-1);
  if(!row)return '';
  return `<p class="finding-facts">FY${escape(row.year)} ${key==='operating_report'?'营业收入':'自由现金流'} <strong>${yi(row.value)}</strong> 亿 ${escape(job.data?.profile?.financialCurrency||'币种未提供')}</p>`;
}
function briefView(){
  const findings=[
    ['经营观察','operating_report',reportParagraph('operating_report',125)],
    ['财务质量','metrics_report',reportParagraph('metrics_report',115)],
    ['变化原因与待验证解释','operating_report',reportParagraph('operating_report',115,'变化原因')]
  ];
  const first=researchCard('基本面研究','01',`<h2 class="article-title">${researchHeadline()}</h2><p class="lede">围绕本次财务表现，结合经营分析、财务指标与风险评估梳理公司基本面。</p><div class="findings">${findings.map(([title,key,text])=>`<div class="finding"><span class="finding-icon">${icon(text?'check':'clock',17)}</span><div><div class="finding-heading"><h4>${title}</h4>${reportButton(key,title,'阅读全文')}</div><p>${summaryMarkup(readableSummary(text||pendingReportText()))}</p>${title==='变化原因与待验证解释'?'':summaryFact(key)}</div></div>`).join('')}</div>`,`<span class="source-label">财务事实 · 经营分析 · 风险核验</span>${reportButton('fundamentals_report','基本面研究','阅读完整研究')}`);
  const decision=researchCard('综合判断','02',`<div class="rating-line"><span class="rating-tag">${escape(ratingLabel(job.rating))}${job.rating?' · '+escape(job.rating):''}</span><span class="note">本次研究的最终判断</span></div><p class="decision-note">${escape(reportParagraph('final_trade_decision',240)||pendingReportText())}</p>`,`<span class="source-label">来自多空讨论与风险评估</span>${reportButton('final_trade_decision','综合判断','查看判断依据')}`);
  const company=researchCard('公司研究简报','03',`<p class="prose">${escape(reportParagraph('company_brief',200)||pendingReportText())}</p>`,`<span class="source-label">汇集公司研究结论与风险提示</span>${reportButton('company_brief','公司研究简报','展开公司简报')}`);
  const plans=card('研究与交易计划',`<details><summary>研究经理观点</summary><div class="prose">${report('investment_plan')}</div></details><details><summary>交易方案</summary><div class="prose">${report('trader_investment_plan')}</div></details>`);
  return `<div class="content-grid"><div class="content-main">${first+decision+company+fundamentalSources()+plans+versionsPanel()}</div>${researchRail()}</div>`;
}
function financialValue(row,value){
  if(value==null)return '—';
  if(/Tax Rate/i.test(row))return pct(value);
  if(/EPS|Per Share/i.test(row))return num(value,2);
  return num(value/1e6,1);
}
const financialPriorities={
  income:['Total Revenue','Cost Of Revenue','Gross Profit','Operating Expense','Operating Income','Tax Provision','Net Income','EBITDA','Diluted EPS','Basic EPS'],
  balance:['Total Assets','Current Assets','Cash Cash Equivalents And Short Term Investments','Total Liabilities Net Minority Interest','Current Liabilities','Total Debt','Stockholders Equity'],
  cashflow:['Operating Cash Flow','Capital Expenditure','Free Cash Flow','Net Income From Continuing Operations','Depreciation And Amortization','Investing Cash Flow','Financing Cash Flow','Cash Dividends Paid','Repurchase Of Capital Stock','End Cash Position']
};
function prioritizedFinancialRows(rows,kind){
  const priority=financialPriorities[kind]||[];
  const score=name=>{const index=priority.indexOf(name);return index<0?100:index;};
  return [...rows].sort((a,b)=>score(a.name)-score(b.name));
}
function prioritizedEvidence(refs){
  if(tab!==3)return refs;
  const prefix=frequency+'.'+statement+'.',priority=financialPriorities[statement]||[];
  const score=r=>{if(!String(r.id||'').startsWith(prefix))return 1000;const i=priority.indexOf(r.field);return i<0?500:i;};
  return [...refs].sort((a,b)=>score(a)-score(b));
}
// Module views reuse archived statements, standardized analytics and original analyst reports.
function moduleHeader(title,caption,action=''){
  return `<div class="module-heading"><div><h2>${title}</h2><p>${escape(caption)}</p></div>${action}</div>`;
}
function reportSection(key,match){
  const root=document.createElement('div');root.innerHTML=displayReport(job.report_html?.[key]||'');
  const heading=[...root.querySelectorAll('h2,h3')].find(h=>h.textContent.includes(match));
  if(!heading)return '';
  let html='',node=heading.nextElementSibling;
  while(node){if(/^H[1-6]$/.test(node.tagName)&&Number(node.tagName[1])<=Number(heading.tagName[1]))break;html+=node.outerHTML;node=node.nextElementSibling;}
  return html;
}
function sectionExcerpt(key,section,limit){
  const root=document.createElement('div');root.innerHTML=reportSection(key,section);
  return excerpt(readableSummary(root.textContent||''),limit);
}
function analystDigest(key,title){
  const text=reportParagraph(key,330,'核心结论');
  return `<section class="card analyst-digest"><div class="card-heading"><h3>${icon('file',16)} ${title}</h3><span class="mini-label">智能体研究</span></div><p class="digest-copy">${summaryMarkup(readableSummary(text||pendingReportText()))}</p><div class="digest-notes">${[['变化原因与待验证解释','变化原因'],['风险提示','风险提示']].map(([label,section])=>`<div><h4>${label}</h4><p>${summaryMarkup(sectionExcerpt(key,section,175)||'请查看完整报告中的相关说明；本次资料未提供独立段落。')}</p></div>`).join('')}</div><div class="card-bottom"><span class="source-label">摘要来自本次归档报告 · 原始引用保留在全文</span>${reportButton(key,title,'阅读完整分析')}</div></section>`;
}
function fullAnalyst(key,title){return `<details class="card full-analyst"><summary>${title}</summary><div class="prose">${report(key)}</div></details>`;}
function metricDisplay(value,definition){return definition?.unit==='ratio'?pct(value):definition?.unit==='currency'?money(value):num(value);}
function seriesChart(points,{unit='',percent=false,label='历史趋势',compact=false,digits=1}={}){
  const hasValue=p=>p.value!=null&&Number.isFinite(Number(p.value));
  const valid=points.filter(hasValue);
  if(!valid.length)return '<p class="note chart-empty">本次资料没有可用的历史序列。</p>';
  const values=valid.map(p=>Number(p.value)),lo=Math.min(0,...values),hi=Math.max(0,...values),span=hi-lo||1;
  const width=compact?340:580,font=compact?16:13;
  const y=v=>32+(hi-v)/span*125;
  const x=i=>points.length===1?width/2:48+i*(width-100)/(points.length-1);
  const format=v=>percent?pct(v):num(v,digits);
  // Keep missing periods on the time axis and break the line at each gap.
  const segments=[];let segment=[];
  points.forEach((p,i)=>{
    if(hasValue(p)){segment.push(x(i)+','+y(p.value));}
    else if(segment.length){segments.push(segment);segment=[];}
  });
  if(segment.length)segments.push(segment);
  const grid=[0,.5,1].map(n=>`<line x1="30" x2="${width-30}" y1="${y(lo+span*n)}" y2="${y(lo+span*n)}" stroke="#e7ece4"/>`).join('');
  const lines=segments.map(points=>`<polyline points="${points.join(' ')}" fill="none" stroke="#205c48" stroke-width="2.5" stroke-linejoin="round"/>`).join('');
  const marks=points.map((p,i)=>`${hasValue(p)?`<g><title>${escape(p.period+': '+format(p.value)+' '+unit)}</title><circle cx="${x(i)}" cy="${y(p.value)}" r="4" fill="#205c48" stroke="white" stroke-width="2"/><text x="${x(i)}" y="${y(p.value)-12}" text-anchor="middle" fill="#205c48" font-size="${font}">${escape(format(p.value))}</text></g>`:''}<text x="${x(i)}" y="188" text-anchor="middle" fill="#606d65" font-size="${compact?16:12}">${escape(p.period.slice(0,compact?4:7))}</text>`).join('');
  return `<div class="series-chart${compact?' compact-chart':''}"><svg viewBox="0 0 ${width} 205" role="img" aria-label="${escape(label+' · '+unit)}">${grid}<line x1="30" x2="${width-30}" y1="${y(0)}" y2="${y(0)}" stroke="#aebdaf" stroke-dasharray="3 4"/><text x="18" y="${y(0)+4}" text-anchor="end" fill="#606d65" font-size="${font}">0</text>${lines}${marks}</svg></div><p class="chart-caption">${escape(unit)}${valid.length<points.length?' · 缺失值不绘制、不连线，完整期间见数据明细':''}</p>`;
}
function readableSummary(text){
  return String(text||'').replace(/snapshot/gi,'本次资料').replace(/calculated_metrics\.history/g,'历史指标').replace(/(?:annual|quarterly)\.(?:income|balance|cashflow)\.\d+(?:\.\d+)?/g,'对应报表科目').replace(/operating_margin/g,'经营利润率（比率原值）').replace(/net_margin/g,'净利率（比率原值）').replace(/free_cashflow/g,'自由现金流').replace(/insider_transactions/g,'内部人交易').replace(/period_note/g,'期间说明');
}
function readableFormula(formula){
  const labels={revenue:'营业收入',gross_profit:'毛利润',operating_income:'营业利润',net_income:'净利润',prior_equity:'上期股东权益',equity:'股东权益',prior_assets:'上期总资产',assets:'总资产',current_assets:'流动资产',current_liabilities:'流动负债',total_liabilities:'负债合计',total_assets:'总资产',operating_cashflow:'经营活动现金流',capex:'资本开支'};
  return String(formula||'本次资料未提供统一计算公式').replace(/[a-z_]+/g,word=>labels[word]||word).replace('Yahoo outflow-negative','现金流出使用负值');
}
function compactNewsHTML(html){
  const root=document.createElement('div');root.innerHTML=html;
  root.querySelectorAll('a[href]').forEach(a=>{
    const url=a.getAttribute('href');
    if(/^https?:\/\//i.test(url)&&a.textContent.trim()===url){
      try{a.textContent=new URL(url).hostname.replace(/^www\./,'')+' 原文 ↗';}catch{}
    }
  });
  // Older analyst reports contain plain-text URLs as well as Markdown links.
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes=[];
  while(walker.nextNode())if(!walker.currentNode.parentElement.closest('a,code,pre'))nodes.push(walker.currentNode);
  nodes.forEach(node=>{
    const matches=[...node.textContent.matchAll(/https?:\/\/[^\s<>]+/gi)];
    if(!matches.length)return;
    const fragment=document.createDocumentFragment();let end=0;
    matches.forEach(match=>{
      const url=match[0].replace(/[。，、；）]+$/,'');
      fragment.append(document.createTextNode(node.textContent.slice(end,match.index)));
      const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';
      try{a.textContent=new URL(url).hostname.replace(/^www\./,'')+' 原文 ↗';}catch{a.textContent=url;}
      fragment.append(a);end=match.index+url.length;
    });
    fragment.append(document.createTextNode(node.textContent.slice(end)));node.replaceWith(fragment);
  });
  return root.innerHTML;
}
function operatingView(){
  const a=job.research_snapshot?.analytics||{},latest=a.history?.[0],currency=job.data.profile?.financialCurrency||'币种未提供';
  const revenue=annualSeries('Total Revenue').at(-1),profit=annualSeries('Net Income').at(-1),cash=annualSeries('Free Cash Flow','cashflow').at(-1);
  const tiles=[["年度营业收入",revenue],["年度净利润",profit],["年度自由现金流",cash]];
  return moduleHeader('经营分析','年度经营表现与智能体解读',`<span class="period-tag">${escape(latest?.period||revenue?.period||'报告期未提供')} · 年度</span>`)+`<div class="module-stats">${tiles.map(([label,row])=>stat(label,yi(row?.value),'亿 '+currency+' · '+(row?.period||'期间未提供'))).join('')}${stat('年度经营利润率',pct(latest?.values?.operating_margin),'统一公式 · '+(latest?.period||'期间未提供'))}</div><div class="module-columns"><div class="content-main">${analystDigest('operating_report','独立经营结论')}${card('营收与利润的年度变化',`<div class="paired-charts"><div><h4>营业收入</h4>${seriesChart(annualSeries('Total Revenue','income',true).map(p=>({...p,value:p.value==null?null:p.value/1e8})),{unit:'亿 '+currency,label:'年度营业收入',compact:true})}</div><div><h4>净利润</h4>${seriesChart(annualSeries('Net Income','income',true).map(p=>({...p,value:p.value==null?null:p.value/1e8})),{unit:'亿 '+currency,label:'年度净利润',compact:true})}</div></div>`)}${fullAnalyst('operating_report','完整经营分析与证据引用')}</div><aside class="module-rail">${card('现金流观察',seriesChart(annualSeries('Free Cash Flow','cashflow',true).map(p=>({...p,value:p.value==null?null:p.value/1e8})),{unit:'亿 '+currency,label:'年度自由现金流',compact:true})+'<p class="note">自由现金流与资本开支结合观察，具体变化原因以报告证据为准。</p>')}${card('业务拆分覆盖','<p class="note">本次接口未提供按产品、业务分部或地区拆分的数据。待接入公司披露后展示构成图。</p>')}<p class="note">年度数据与 TTM 分开阅读。图表展示报告期变化，不单独证明经营原因。</p></aside></div>`;
}
function financialView(){
  const p=job.data.profile||{},t=job.data.statements?.[frequency]?.[statement],currency=p.financialCurrency||'币种未提供';
  const names=[['income','利润表'],['balance','资产负债表'],['cashflow','现金流量表']],name=names.find(([v])=>v===statement)[1];
  const rows=prioritizedFinancialRows(t?.rows||[],statement),selectedRow=rows.find(r=>r.name===financialRow)||rows[0];
  const points=selectedRow?(t.periods||[]).map((period,i)=>({period,value:selectedRow.values[i]})).reverse():[];
  const perShare=/EPS|Per Share/i.test(selectedRow?.name||''),tax=/Tax Rate/i.test(selectedRow?.name||''),shares=/Shares/i.test(selectedRow?.name||'');
  const unit=tax?'百分比':perShare?currency+' / 股':shares?'百万股':'百万 '+currency;
  const chart=seriesChart(points.map(p=>({...p,value:p.value==null?null:tax||perShare?p.value:p.value/1e6})),{unit,percent:tax,digits:perShare?2:1,label:fieldName(selectedRow?.name||'')});
  return moduleHeader('财务报告','完整科目与报告期对照',`<span class="period-tag">财报币种 ${escape(currency)}</span>`)+`<section class="card statement-workspace"><div class="statement-controls"><div class="statement-switch" role="group" aria-label="选择报表">${names.map(([value,label])=>`<button data-statement="${value}" aria-pressed="${statement===value}" class="${statement===value?'active':''}">${label}</button>`).join('')}</div><div class="toolbar"><select id="frequency" aria-label="报表频率"><option value="annual" ${frequency==='annual'?'selected':''}>年度报告</option><option value="quarterly" ${frequency==='quarterly'?'selected':''}>季度报告</option></select><select id="statement" aria-label="财务报表">${names.map(([v,label])=>`<option value="${v}" ${statement===v?'selected':''}>${label}</option>`).join('')}</select></div></div><div class="statement-chart"><div><span class="eyebrow">科目趋势 · ${frequency==='annual'?'年度':'季度'}</span><h3>${escape(fieldName(selectedRow?.name||'未提供数据'))}</h3><label class="field compact-field">查看科目<select id="financial-row" aria-label="查看科目趋势">${rows.map(r=>`<option value="${escape(r.name)}" ${r===selectedRow?'selected':''}>${escape(fieldName(r.name))}</option>`).join('')}</select></label></div><div>${chart}${frequency==='quarterly'&&statement==='cashflow'?'<p class="note">季度现金流可能为累计口径，请核对原始披露。</p>':''}</div></div><div class="table-heading"><h3>财务报表 · ${name}</h3><label class="row-search">${icon('search',15)}<input id="financial-search" type="search" value="${escape(financialQuery)}" placeholder="搜索中英文科目" aria-label="搜索财务科目"></label></div><p class="scroll-guidance">金额：百万 ${escape(currency)} · <span id="financial-row-count">共 ${rows.length} 项科目</span> · 表内上下滚动，左右滑动看报告期 ↔</p>${rows.length?table(['科目 · 百万 '+currency,...t.periods],rows.map(row=>[fieldName(row.name)===row.name?row.name:fieldName(row.name)+' / '+row.name,...row.values.map(v=>financialValue(row.name,v))])).replace('class="table-wrap"','class="table-wrap financial-scroll" tabindex="0" aria-label="可滚动财务报表"'):'<p class="note">数据源未提供此报表。</p>'}<p id="financial-no-results" class="note" hidden>没有匹配科目，请尝试其他名称或清空搜索。</p><p class="note table-footnote">每股项目保留原值，股数以百万股计，税率以百分比展示。科目按供应商原名保留，优先列出核心科目。</p></section>`;
}
function filterFinancialRows(){
  const query=financialQuery.trim().toLocaleLowerCase(),rows=[...$('#content').querySelectorAll('.financial-scroll tbody tr')];
  let count=0;rows.forEach(row=>{row.hidden=!(row.querySelector('td')?.textContent||'').toLocaleLowerCase().includes(query);if(!row.hidden)count++;});
  $('#financial-row-count').textContent=query?`匹配 ${count} / ${rows.length} 项科目`:`共 ${rows.length} 项科目`;
  $('#financial-no-results').hidden=!query||count>0;
}
function metricsView(){
  const a=job.research_snapshot?.analytics||{},defs=a.definitions||{},history=a.history||[],latest=history[0],p=job.data.profile||{};
  const groups=[['盈利能力',['gross_margin','operating_margin','net_margin','roe','roa']],['偿债能力',['current_ratio','debt_ratio']],['现金创造',['free_cashflow']]];
  const keys=Object.keys(defs);if(!keys.includes(metricKey))metricKey=keys[0]||'net_margin';
  const definition=defs[metricKey],unit=definition?.unit==='currency'?'亿 '+(p.financialCurrency||'币种未提供'):definition?.unit==='ratio'?'%':'倍';
  const points=[...history].reverse().map(h=>({period:h.period,value:h.values?.[metricKey]==null?null:definition?.unit==='currency'?h.values[metricKey]/1e8:h.values[metricKey]}));
  return moduleHeader('财务指标','统一公式计算 · 年度历史对照',`<span class="period-tag">${escape(latest?.period||'报告期未提供')} · 年度</span>`)+`<div class="module-columns"><div class="content-main">${card('财务质量概览',`<div class="quality-groups">${groups.map(([label,keys])=>`<div class="quality-group"><h4>${label}</h4>${keys.map(k=>`<div class="quality-row"><span>${escape(defs[k]?.label||({gross_margin:'毛利率',operating_margin:'经营利润率',net_margin:'净利率',roe:'ROE',roa:'ROA',current_ratio:'流动比率',debt_ratio:'资产负债率',free_cashflow:'自由现金流'}[k]))}</span><strong>${escape(metricDisplay(latest?.values?.[k],defs[k]))}</strong></div>`).join('')}</div>`).join('')}</div><p class="note">基于年度报表计算；ROE、ROA 使用平均余额。自由现金流币种：${escape(p.financialCurrency||'未提供')}。</p>`)}${analystDigest('metrics_report','独立财务指标结论')}${card('历史指标趋势',`<div class="toolbar"><select id="metric-key" aria-label="选择历史财务指标">${keys.map(k=>`<option value="${escape(k)}" ${metricKey===k?'selected':''}>${escape(defs[k].label)}</option>`).join('')}</select><span class="mini-label">${escape(unit)} · 年度</span></div>${seriesChart(points,{unit,percent:definition?.unit==='ratio',label:definition?.label||'财务指标'})}<p class="note formula-note">计算公式：${escape(readableFormula(definition?.formula))}</p>`)}<details class="card"><summary>统一公式、历史数据与 TTM 明细</summary>${calculatedMetrics()}</details>${fullAnalyst('metrics_report','完整指标分析与证据引用')}</div><aside class="module-rail">${card('市场估值',`<p class="eyebrow">供应商当前归档口径</p><div class="valuation-main"><span>市盈率 · TTM</span><strong>${num(p.trailingPE)}<small> 倍</small></strong></div>${table(['指标','数值'],[['预期市盈率',num(p.forwardPE)],['市净率 PB',num(p.priceToBook)],['EPS · TTM',num(p.trailingEps)],['市值',money(p.marketCap)+' '+(p.currency||'')]])}<p class="note">交易币种 ${escape(p.currency||'未提供')}。估值与年度计算值期间可能不同。</p>`)}${card('供应商指标',`<details><summary>查看归档原值</summary>${table(['指标','数值'],[['经营利润率',pct(p.operatingMargins)],['ROE',pct(p.returnOnEquity)],['毛利率',pct(p.grossMargins)],['净利率',pct(p.profitMargins)],['ROA',pct(p.returnOnAssets)],['收入增长率',pct(p.revenueGrowth)],['利润增长率',pct(p.earningsGrowth)],['流动比率',num(p.currentRatio)],['债务 / 股东权益',p.debtToEquity==null?'—':num(p.debtToEquity)+'%'],['自由现金流',money(p.freeCashflow)+' '+(p.financialCurrency||'')]])}</details><p class="note">供应商当前指标与统一公式历史值分开展示；不得直接视为同一期间的变化。</p>`)}</aside></div>`;
}
function newsSources(){
  const root=document.createElement('div');root.innerHTML=job.report_html?.news_report||'';
  const seen=new Set();return [...root.querySelectorAll('a[href]')].filter(a=>{const url=a.getAttribute('href');if(!/^https?:\/\//i.test(url)||seen.has(url))return false;seen.add(url);return true;}).map(a=>({url:a.getAttribute('href'),title:a.textContent}));
}
function macroReadings(){
  const reports=(job.macro_context||job.macro_update)?.reports||[];
  return reports.map(text=>{const title=text.match(/^##\s+(.+)/m)?.[1],reading=text.match(/最新读数[：:]\s*([\d.,+-]+).*?观察日期[：:]\s*([\d-]+)/),unit=text.match(/- 单位[：:]\s*([^；。\n]+)/)?.[1];return title&&reading?`<div class="macro-reading"><span>${escape(title.replace(/^FRED[：:]\s*/,''))}</span><strong>${escape(reading[1])}<small>${escape(unit||'')}</small></strong><small>观察日期 ${escape(reading[2])}</small></div>`:'';}).join('');
}
function newsView(){
  const body=job.report_html?.news_report,company=reportSection('news_report','公司层面')||reportSection('news_report','公司新闻')||reportSection('news_report','公司相关'),macro=reportSection('news_report','宏观环境')||reportSection('news_report','宏观背景'),transmission=reportSection('news_report','传导机制'),links=newsSources();
  const context=job.macro_context||job.macro_update,readings=macroReadings(),items=job.news_context?.items||[];
  const macroStatus=context?`<section class="macro-status"><h3>${icon('layers',15)} 宏观数据参与研究</h3><p class="note">${job.macro_consumed?'本版本已将 FRED 数据传给原新闻分析师，并重新运行综合判断。财务数据和财务分析沿用原版本。':'当前宏观数据仅作补充，尚未参与原新闻结论与综合判断。'}</p><button id="reanalyze-macro" class="text-button" type="button">用这份宏观数据重新分析新闻与综合判断 ${icon('arrow',14)}</button></section>`:'';
  return moduleHeader('新闻分析','公司事件 · 宏观传导 · 原始来源',`<span class="period-tag">研究日期 ${escape(job.date)}</span>`)+`<div class="module-columns news-columns"><div class="content-main">${card('新闻事件与风险',`<div class="card-heading"><span class="eyebrow">原新闻分析智能体 · 本次归档结论</span>${reportButton('news_report','新闻分析','完整报告')}</div><p class="news-lede">${summaryMarkup(reportParagraph('news_report',430,'核心结论')||pendingReportText())}</p>`)}${company?card('公司事件',`<div class="prose event-sections">${compactNewsHTML(company)}</div>`):body?card('新闻分析正文',`<div class="prose">${report('news_report')}</div>`):specialistReport('news_report','新闻分析 · 事件、影响与风险')}${transmission?card('宏观如何影响公司',`<div class="prose">${transmission}</div>`):''}${macro?`<details class="card"><summary>宏观环境与分析依据</summary><div class="prose">${macro}</div></details>`:''}${company?fullAnalyst('news_report','完整新闻分析与全部来源'):''}${items.length?`<details class="card"><summary>采集新闻资料 · ${items.length} 条</summary><p class="note">采集列表不等同于已验证事件；具体判断以新闻智能体报告为准。</p>${items.map(item=>`<p class="news-source"><span>${escape(item.source||'来源未提供')} · ${escape(item.published_at||'发布时间未提供')}</span>${/^https?:\/\//i.test(item.url||'')?`<a href="${escape(item.url)}" target="_blank" rel="noopener noreferrer">${escape(item.title)} ↗</a>`:escape(item.title)}</p>`).join('')}</details>`:''}</div><aside class="module-rail">${context?card('FRED 宏观读数',readings||'<p class="note">本次宏观资料没有可解析的读数，完整信息保留在报告中。</p>'):card('宏观资料','<p class="note">本次归档未包含独立的 FRED 数据。</p>')}${macroStatus}${card('报告引用来源',links.length?`<p class="note">共 ${links.length} 个独立链接，保留分析师原始引用。</p><div class="news-links">${links.map((link,i)=>`<a href="${escape(link.url)}" target="_blank" rel="noopener noreferrer"><span>${String(i+1).padStart(2,'0')}</span>${escape(link.title||'原始来源')} ↗</a>`).join('')}</div>`:'<p class="note">本次报告未包含可提取的外部链接，请在完整报告中核对来源说明。</p>')}</aside></div>`;
}
function setSidebar(open){
  document.body.classList.toggle('sidebar-open',open);
  $('#sidebar-shade').hidden=!open;
  $('#toggle-sidebar').setAttribute('aria-expanded',String(open));
}
function updateTabHint(){
  const nav=$('#tabs');$('#tab-next').hidden=nav.scrollWidth-nav.clientWidth-nav.scrollLeft<10;
}
function showDetail(title,html){
  $('#detail-title').textContent=title;$('#detail-content').innerHTML=html;
  $('#detail-content').scrollTop=0;
  if(!$('#detail-dialog').open)$('#detail-dialog').showModal();
}
function showSources(){
  if(!job){toast('请先选择一份研究，或新建公司研究。');return;}
  showDetail('本次研究的资料与口径',source()+`<p class="note">交易币种：${escape(job.data?.profile?.currency||'未提供')}；财报币种：${escape(job.data?.profile?.financialCurrency||'未提供')}。TTM、年度与季度分别展示，不自动进行币种换算。财务关系与业务变化原因需要分别核验。</p>`+filingPanel());
}
function openResearch(){
  error('');setSidebar(false);
  if(!$('#research-dialog').open)$('#research-dialog').showModal();
  $('#ticker').focus();
}
function toast(message){
  clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;
  toastTimer=setTimeout(()=>$('#toast').hidden=true,4000);
}
const environmentStates={strong:'强势',lean_strong:'偏强',range:'震荡',lean_weak:'偏弱',weak:'弱势'};
const environmentPhases={ice:'冰点',ignite:'启动',rally:'主升',climax:'高潮',ebb:'退潮',repair:'修复'};
const environmentNumber=v=>typeof v==='number'&&Number.isFinite(v);
function environmentMetricSpecs(c){
  return c.market==='CN_A'||!c.market?{
    score:{label:'环境分',score:true},
    index_pct:{label:'指数涨跌幅',percent:true},
    seal_rate:{label:'封板率',percent:true},
  }:{
    score:{label:'环境分',score:true},
    return_20d:{label:'20 日收益',percent:true},
    realized_vol_20d:{label:'年化波动率',percent:true},
  };
}
function environmentChartValue(value,spec){return environmentNumber(value)?(spec.percent?pct(value):num(value,0)):'未提供';}
function environmentTimeline(c){
  const specs=environmentMetricSpecs(c),key=specs[environmentMetric]?environmentMetric:'score',spec=specs[key];
  const rows=(c.history||[]).slice(-(environmentRange===10?10:30));
  const valid=row=>environmentNumber(row[key])&&(!spec.score||(row[key]>=0&&row[key]<=100));
  const controls=`<div class="env-chart-controls"><div class="env-segments" role="group" aria-label="选择趋势指标">${Object.entries(specs).map(([id,item])=>`<button type="button" data-env-metric="${id}" aria-pressed="${id===key}">${item.label}</button>`).join('')}</div><div class="env-segments" role="group" aria-label="选择趋势范围">${[10,30].map(n=>`<button type="button" data-env-range="${n}" aria-pressed="${environmentRange===n}">${n} 日</button>`).join('')}</div></div>`;
  const heading=`<div class="env-chart-heading"><div><h3>市场环境趋势</h3><p>同一份研究归档 · ${rows.length} 个交易日</p></div><span class="env-chart-tag">${escape(spec.label)}</span></div>`;
  const values=rows.filter(valid).map(r=>r[key]);
  if(!values.length)return `<section class="card env-trend-card">${heading}${controls}<p class="env-chart-empty">本次归档没有可绘制的${escape(spec.label)}序列。缺失值不会补成零。</p></section>`;
  const lo=spec.score?0:Math.min(0,...values),max=spec.score?100:Math.max(0,...values),hi=max===lo?lo+1:max,span=hi-lo;
  const x=i=>rows.length===1?306:48+i*510/(rows.length-1),y=v=>18+(hi-v)/span*174;
  const bands=spec.score?[[0,30,'弱势','#faefec'],[30,45,'偏弱','#faf5ec'],[45,55,'震荡','#f1f3ee'],[55,70,'偏强','#eef4e9'],[70,100,'强势','#e4efe7']].map(([low,high,label,color])=>`<rect x="48" y="${y(high)}" width="510" height="${y(low)-y(high)}" fill="${color}"/><text class="env-axis-text env-band-label" x="581" y="${y((low+high)/2)+4}">${label}</text>`).join(''):'';
  const grid=[lo,lo+span/2,hi].map(v=>`<line x1="48" x2="558" y1="${y(v)}" y2="${y(v)}" stroke="#dbe3da" stroke-dasharray="3 4"/><text class="env-axis-text" x="39" y="${y(v)+4}" text-anchor="end">${escape(environmentChartValue(v,spec))}</text>`).join('');
  const segments=[];let run=[];
  rows.forEach((row,i)=>{if(valid(row))run.push(`${x(i).toFixed(2)},${y(row[key]).toFixed(2)}`);else if(run.length){segments.push(run);run=[];}});if(run.length)segments.push(run);
  const lines=segments.map(points=>`<polyline class="env-trend-line" points="${points.join(' ')}"/>`).join('');
  const dots=rows.map((row,i)=>valid(row)?`<g class="env-chart-point" data-env-point="${i}" tabindex="0" role="img" aria-label="${escape(row.date+'，'+spec.label+' '+environmentChartValue(row[key],spec))}"><title>${escape(row.date+' · '+spec.label+' '+environmentChartValue(row[key],spec))}</title><circle cx="${x(i)}" cy="${y(row[key])}" r="4"/></g>`:'').join('');
  const ticks=[...new Set([0,Math.floor((rows.length-1)/4),Math.floor((rows.length-1)/2),Math.floor((rows.length-1)*3/4),rows.length-1])].map(i=>`<text class="env-axis-text" x="${x(i)}" y="220" text-anchor="middle">${escape(String(rows[i].date).slice(5))}</text>`).join('');
  const last=rows.at(-1),zero=lo<0&&hi>0?`<line x1="48" x2="558" y1="${y(0)}" y2="${y(0)}" stroke="#8a9a8e"/>`:'';
  return `<section class="card env-trend-card">${heading}${controls}<p class="env-chart-readout" id="environment-chart-readout" aria-live="polite">${escape(last.date+' · '+spec.label+' '+environmentChartValue(last[key],spec))}</p><div class="env-trend-plot"><svg viewBox="0 0 650 240" role="img" aria-label="${escape(spec.label+'历史趋势')}"><title>${escape(spec.label)}历史趋势</title><desc>日期按归档交易日排列；缺失值不绘制、不连接。使用键盘聚焦数据点或将鼠标移到数据点查看读数。</desc>${bands}${grid}${zero}${lines}${dots}${ticks}</svg></div><p class="env-chart-foot">${spec.score?'背景色对应五档市场强弱；规则分不是上涨概率。':spec.percent?'纵轴使用百分比，保留正负方向。':''} 悬停或聚焦圆点查看当日数值。</p></section>`;
}
function environmentDrivers(c){
  const r=c.latest;
  const fields=c.market==='CN_A'||!c.market?[['赚钱效应','profit_score'],['投机活跃','speculation_score'],['抗跌能力','resilience_score'],['趋势强度','trend_score']]:[['趋势','trend_score'],['20 日动量','momentum_score'],['参考资产广度','breadth_score'],['波动稳定度','volatility_score']];
  return `<section class="card env-drivers-card"><div class="env-chart-heading"><div><h3>四维驱动分</h3><p>${escape(r.date)} · 每个维度满分 100</p></div></div><div class="env-driver-list">${fields.map(([label,key])=>{
    const value=r[key],usable=environmentNumber(value)&&value>=0&&value<=100;
    return `<div class="env-driver-row"><div><span>${label}</span><strong>${usable?num(value,0):'—'}<small>${usable?' / 100':''}</small></strong></div><div class="env-driver-track" ${usable?`role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}"`:`aria-label="${label}未提供"`}>${usable?`<span style="width:${value}%"></span>`:'<span class="env-missing-bar"></span>'}</div></div>`;
  }).join('')}</div><p class="env-chart-foot">各条表示维度分，不是对综合分的贡献。${c.market==='CN_A'||!c.market?'综合权重：赚钱 35%、投机 25%、抗跌 20%、趋势 20%。':'综合权重：趋势 40%、动量 30%、广度 20%、波动 10%。波动稳定度分越高，已实现波动越低。'}</p></section>`;
}
function environmentReferenceBars(c){
  const rows=(c.references||[]).filter(r=>r.date===c.latest.date),valid=rows.filter(r=>environmentNumber(r.return_20d));
  const extent=Math.max(.01,...valid.map(r=>Math.abs(r.return_20d)));
  return `<section class="card env-return-card"><div class="env-chart-heading"><div><h3>参考资产 · 20 日收益</h3><p>${escape(c.latest.date)} · 同一观察日 · 前复权口径</p></div><span class="env-chart-tag">零轴对齐</span></div>${rows.length?`<div class="env-return-list">${rows.map(r=>{
    const value=r.return_20d,usable=environmentNumber(value),width=usable?Math.abs(value)/extent*50:0;
    return `<div class="env-return-row"><div class="env-return-label"><strong>${escape(r.name)}</strong><span>${escape(r.symbol)}</span></div><div class="env-return-track" role="img" aria-label="${escape(r.name+'，20 日收益 '+(usable?pct(value):'未提供'))}">${usable?`<span class="env-return-bar ${value<0?'negative':'positive'}" style="left:${value<0?50-width:50}%;width:${width}%"></span>`:''}<i class="env-zero-axis"></i></div><strong class="env-return-value ${usable&&value<0?'negative':''}">${usable?(value>0?'+':'')+pct(value):'—'}</strong></div>`;
  }).join('')}</div><p class="env-chart-foot">横条只比较已归档的 20 日收益，不代表行业排名；未存储的资产历史走势不会补画。</p>`:'<p class="note">当前观察日没有可对齐的参考资产数据。</p>'}</section>`;
}
function environmentMainlineBars(rows){
  if(!rows.length)return '<p class="note">当前观察日暂无主线排名。</p>';
  return `<div class="env-mainline-bars">${rows.slice(0,5).map(r=>`<div class="env-driver-row"><div><span>${escape(r.member)}</span><strong>${num(r.score,1)}</strong></div><div class="env-driver-track">${environmentNumber(r.score)?`<span style="width:${Math.max(0,Math.min(100,r.score))}%"></span>`:''}</div><small>排名 ${num(r.rank,0)} · 涨停 ${num(r.limit_up_count,0)} 家 · 最高 ${num(r.max_boards,0)} 板</small></div>`).join('')}</div>`;
}
function environmentDetails(title,html){return `<details class="card environment-details"><summary>${escape(title)}</summary>${html}</details>`;}

function environmentView(){
  const supplemented=Boolean(job.market_environment_update);
  const c=job.market_environment_update||job.market_environment;
  const market=c?.market||(/\.(SS|SH|SZ)$/.test(job.ticker)?'CN_A':/\.HK$/.test(job.ticker)?'HK':'US');
  const subtitle=market==='CN_A'?'A 股市场强弱 · 情绪周期 · 行业与概念主线':`${market==='HK'?'港股':'美股'}市场趋势 · 动量 · 波动 · 参考资产广度`;
  const header=moduleHeader('市场环境',subtitle,`<span class="period-tag">研究日期 ${escape(job.date)}</span>`);
  if(!c){
    const applicable=/\.(SS|SH|SZ|HK)$/.test(job.ticker)||/^[A-Z][A-Z0-9]*(?:-[A-Z]|\.[AB])?$/.test(job.ticker);
    return header+card('市场环境资料',`<p class="note">${applicable?(['queued','running'].includes(job.status)?'正在准备本次市场环境资料。':'本次研究未归档市场环境；历史报告不会自动补取新数据。'):'当前证券类型尚无对应的市场环境数据源。'}</p>`);
  }
  const coverage=c.coverage||{},r=c.latest;
  const labels={available:'资料可用',partial:'资料部分可用',stale:'资料已过期',unavailable:'资料不可用'};
  const received=!supplemented&&job.market_environment_consumed;
  const flow=supplemented?'已按原研究日期补算市场环境；原研究、交易计划和风险结论未重新生成。':received?(r&&['available','partial'].includes(coverage.status)?'已将本次归档数据传入研究讨论、交易计划和风险评估流程。':'已将资料缺失或过期说明传入研究与风险评估；不作为当前市场判断依据。'):['queued','running'].includes(job.status)?'已冻结资料，研究与风险评估尚未全部完成。':'本次研究未完成市场环境传递核验，不宣称已参与综合判断。';
  let html=header+`<div class="environment-provenance">`+card('研究与风险评估依据',`<p>${escape(flow)}</p><p class="note">${escape(labels[coverage.status]||'状态未知')} · 采集时间 ${escape(c.retrieved_at)} · 截止 ${escape(c.cutoff_date)}</p>${c.refresh_note?`<p class="note">${escape(c.refresh_note)}</p>`:''}${coverage.reason?`<p class="note warn">${escape(coverage.reason)}</p>`:''}${(coverage.warnings||[]).map(w=>`<p class="note">${escape(w)}</p>`).join('')}`)+`</div>`;
  if(!r)return html+card('资料接入',`<p class="note">${market==='CN_A'?'请检查本机面板服务、API Token 权限，以及面板中的日线数据和市场环境计算状态。':'请检查富途官方 AppKey、签名私钥文件和对应市场的行情权限。'}新建研究后采集；本页不会重新取数或改写历史结论。</p>`);
  if(market!=='CN_A')return html+internationalEnvironmentBody(c);
  const facts=[['市场状态',environmentStates[r.state]||'—','五档市场强弱'],['情绪分',num(r.score,0)+' / 100','规则评分'],['情绪阶段',environmentPhases[r.phase]||'未提供','六阶段情绪周期'],['观察日期',r.date,'最新完整交易日']];
  html+=`<div class="environment-facts">${facts.map(([label,value,note])=>stat(label,escape(value),note)).join('')}</div>`;
  html+=`<div class="environment-chart-grid">${environmentTimeline(c)}${environmentDrivers(c)}</div>`;
  html+=environmentDetails('查看市场指标明细',table(['涨停家数','跌停家数','连板高度','封板率','晋级率','指数涨跌幅'],[[num(r.limit_up,0),num(r.limit_down,0),num(r.max_consecutive,0),pct(r.seal_rate),pct(r.promo_rate),pct(r.index_pct)]]));
  html+=environmentDetails('查看历史数据明细',table(['日期','市场状态','情绪分','情绪阶段'],(c.history||[]).slice(-10).reverse().map(x=>[x.date,environmentStates[x.state]||'—',num(x.score,0),environmentPhases[x.phase]||'未提供'])));
  html+='<div class="environment-mainlines">'+[['concept','概念主线'],['industry','行业主线']].map(([kind,label])=>{
    const rows=(c.mainlines?.[kind]||[]).filter(x=>x.date===r.date);
    return card(label,environmentMainlineBars(rows));
  }).join('')+'</div>';
  return html+card('数据口径',`<p class="note">来源：tick-stock-panel。市场状态和情绪阶段为规则计算，评分不代表上涨概率，也不直接映射买卖评级或仓位。盘中研究使用此前的完整交易日环境，交易日收盘后仍需等待面板计算完成。缺失值显示为 —。</p><p class="note">${escape(c.membership_note)}</p><p class="note">主线排名不证明当前个股属于该主线；历史序列是当前修订值，不是严格的历史时点数据。页面与本次研究使用同一份冻结资料。</p>`);
}
function internationalEnvironmentBody(c){
  const r=c.latest,showScore=v=>v==null?'—':num(v,0)+' / 100';
  const facts=[['市场状态',environmentStates[r.state]||'未提供','五档环境强弱'],['环境分',showScore(r.score),'规则评分'],['年化已实现波动率',pct(r.realized_vol_20d),'最近 20 个日收益'],['观察日期',r.date,'最新完整交易日']];
  let html=`<div class="environment-facts">${facts.map(([label,value,note])=>stat(label,escape(value),note)).join('')}</div>`;
  html+=`<div class="environment-chart-grid">${environmentTimeline(c)}${environmentDrivers(c)}</div>`;
  html+=environmentDetails('查看市场指标明细',table(['基准','当日收益','20 日收益','60 日高点回撤','样本中高于 MA20 的比例','可对齐参考资产'],[[c.primary_symbol,pct(r.return_1d),pct(r.return_20d),pct(r.drawdown_60d),pct(r.breadth_ratio),`${r.breadth_sample_size} / ${r.breadth_expected_size}`]]));
  html+=environmentReferenceBars(c);
  html+=environmentDetails('查看参考资产明细',table(['参考资产','代码','观察日期',`前复权价格 · ${c.currency}`,'20 日均线','60 日均线','当日收益','20 日收益'],(c.references||[]).map(x=>[x.name,x.symbol,x.date,num(x.close),num(x.ma20),num(x.ma60),pct(x.return_1d),pct(x.return_20d)])));
  html+=environmentDetails('查看历史数据明细',table(['日期','市场状态','环境分','20 日收益','年化已实现波动率','参考资产数量'],(c.history||[]).slice(-10).reverse().map(x=>[x.date,environmentStates[x.state]||'未提供',showScore(x.score),pct(x.return_20d),pct(x.realized_vol_20d),`${x.breadth_sample_size} / ${x.breadth_expected_size}`])));
  html+=card('数据口径',`<p class="note">来源：${escape(c.source)}。${escape(c.membership_note)}港美股不使用 A 股涨停、封板率或连板情绪阶段。</p><p class="note">只使用 ${escape(c.market_timezone)} 时区的完整日线，并预留收盘确认时间。前复权价格用于计算日收益和趋势；已实现波动率为最近 20 个日收益的样本标准差乘 √252，不是隐含波动率。</p><p class="note">评分是透明规则，不代表上涨概率，不直接决定评级或仓位。缺失参考资产不会填零；少于两只可对齐资产时不计算广度和环境分。页面与研究使用同一份归档，历史日线为当前修订值，不代表严格的历史时点数据。</p><details><summary>评分规则与参考样本</summary><p class="note">趋势：价格高于 MA20 计 60 分，高于 MA60 计 40 分；动量：50 + 500 × 20 日收益，限制在 0–100；广度：参考样本高于 MA20 的比例 × 100；波动：100 − 200 × 年化已实现波动率，限制在 0–100。综合权重：趋势 40%、动量 30%、广度 20%、波动 10%。</p><p class="note">分档：≥70 强势、≥55 偏强、≥45 震荡、≥30 偏弱、其余弱势。样本：${escape((c.reference_universe||[]).map(x=>x.symbol).join('、'))}。</p></details>`);
  return html;
}
function bindContent(){
  $('#content').querySelectorAll('[data-env-metric]').forEach(b=>b.addEventListener('click',()=>{environmentMetric=b.dataset.envMetric;draw();}));
  $('#content').querySelectorAll('[data-env-range]').forEach(b=>b.addEventListener('click',()=>{environmentRange=Number(b.dataset.envRange);draw();}));
  $('#content').querySelectorAll('[data-env-point]').forEach(point=>{
    const show=()=>{
      const c=job.market_environment_update||job.market_environment;if(!c)return;
      const specs=environmentMetricSpecs(c),key=specs[environmentMetric]?environmentMetric:'score',row=(c.history||[]).slice(-(environmentRange===10?10:30))[Number(point.dataset.envPoint)];
      const readout=$('#environment-chart-readout');if(row&&readout)readout.textContent=row.date+' · '+specs[key].label+' '+environmentChartValue(row[key],specs[key]);
    };
    point.addEventListener('pointerenter',show);point.addEventListener('focus',show);
  });

  $('#tabs').querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=Number(b.dataset.tab);draw();});
  $('#pause-research')?.addEventListener('click',async e=>{
    const id=job.id;e.currentTarget.disabled=true;
    try{await api('/api/jobs/'+encodeURIComponent(id)+'/pause',{method:'POST'});await select(id);await refresh();}catch(err){error(err.message);draw();}
  });
  $('#resume-research')?.addEventListener('click',async e=>{
    const id=job.id;e.currentTarget.disabled=true;
    try{await api('/api/jobs/'+encodeURIComponent(id)+'/resume',{method:'POST'});await select(id);await refresh();}catch(err){error(err.message);draw();}
  });
  $('#retry-research')?.addEventListener('click',async e=>{
    const id=job.id;e.currentTarget.disabled=true;
    try{await api('/api/jobs/'+encodeURIComponent(id)+'/retry',{method:'POST'});await select(id);await refresh();}
    catch(e){error(e.message);}finally{const b=$('#retry-research');if(b)b.disabled=false;}
  });
  $('#reanalyze-macro')?.addEventListener('click',async e=>{
    const id=job.id;e.currentTarget.disabled=true;
    try{const next=await api('/api/jobs/'+encodeURIComponent(id)+'/reanalyze-macro',{method:'POST'});selected=next.id;tab=0;await select(next.id);await refresh();}
    catch(e){error(e.message);}finally{const b=$('#reanalyze-macro');if(b)b.disabled=false;}
  });
  $('#content').querySelectorAll('[data-statement]').forEach(b=>b.addEventListener('click',()=>{statement=b.dataset.statement;financialRow='';draw();}));
  $('#financial-row')?.addEventListener('change',e=>{financialRow=e.target.value;draw();});
  $('#financial-search')?.addEventListener('input',e=>{financialQuery=e.target.value;filterFinancialRows();});
  $('#metric-key')?.addEventListener('change',e=>{metricKey=e.target.value;draw();});
  if(tab===3)filterFinancialRows();
  $('#frequency')?.addEventListener('change',e=>{frequency=e.target.value;financialRow='';draw();});
  $('#statement')?.addEventListener('change',e=>{statement=e.target.value;financialRow='';draw();});
}
function draw(){
  if(!job)return;
  $('#empty').hidden=true;$('#workspace').hidden=false;$('#workspace').setAttribute('data-tab',String(tab));
  const p=job.data?.profile||{},exchange={NMS:'NASDAQ',NGM:'NASDAQ',NCM:'NASDAQ',NYQ:'NYSE',HKG:'HKEX',SHH:'SSE',SHZ:'SZSE'}[p.exchange]||p.exchange||'';
  const market=job.data?.market?.label||(job.ticker.endsWith('.HK')?'港股':/\.(SS|SZ)$/.test(job.ticker)?'沪深 A 股':['NASDAQ','NYSE'].includes(exchange)?'美股':'');
  $('#company-symbol').textContent=monogram(p.longName||job.ticker);
  $('#company-ticker').innerHTML=`<strong>${escape(job.ticker)}</strong><span>${escape(market)}</span><span>${escape(exchange)}</span>`;
  $('#company-name').textContent=p.longName||job.ticker;
  $('#company-meta').textContent=[p.sector,p.industry].filter(Boolean).join(' · ');
  $('#archive-date').textContent='研究日期 '+job.date.replaceAll('-','.');
  $('#archive-price').textContent=p.currentPrice==null?'':'归档价格 '+fixed(p.currentPrice,2)+' '+(p.currency||'');
  $('#research-status').className='status-tag '+job.status;
  $('#research-status').innerHTML=`<span class="status-dot"></span>${escape(statusLabel(job.status))}`;
  $('#progress').hidden=job.status==='completed';$('#progress').classList.toggle('failed',job.status==='failed');
  $('#progress').textContent=((tab===8&&job.status==='completed'&&job.automatic_valuation?.phase?job.automatic_valuation.phase:job.phase)||'')+(job.error?' · '+job.error:'');
  $('#export-report').href='/api/jobs/'+encodeURIComponent(job.id)+'/export';
  $('#export-report').hidden=job.status!=='completed'||!Object.keys(job.report_html||{}).length;
  $('#tabs').innerHTML=tabs.map((label,i)=>`<button class="${tab===i?'active':''}" aria-pressed="${tab===i}" data-tab="${i}">${label}${i===5&&(job.macro_update||job.macro_context)?'<span class="tab-indicator"></span>':''}</button>`).join('');
  $('#overview').hidden=!job.data;$('#overview').innerHTML=job.data?financialOverview():'';
  $('#footer-source').textContent='资料来源：'+(job.data?.source||'待获取')+(job.macro_context||job.macro_update?' · FRED':'')+((job.market_environment_update||job.market_environment)?' · '+(job.market_environment_update||job.market_environment).source:'')+' · '+job.date;
  let html='';
  if(tab===0)html=briefView();
  else if(tab===8){html=valuationView();
  }else if(tab===7){html=environmentView();
  }else if(tab===5){html=newsView();
  }else if(!job.data){
    html=empty(job.data_status==='loading'?'正在加载公司资料和财务表格，请稍候…':'尚无结构化数据。发起新分析可同时获取公司资料、财务报表和指标。');
  }else if(tab===1){
    const fields=[['公司名称',p.longName],['行业',p.industry],['板块',p.sector],['国家 / 地区',p.country],['城市',p.city],['员工数',num(p.fullTimeEmployees,0)],['交易币种',p.currency],['财报币种',p.financialCurrency]];
    html=`<div class="content-grid"><div class="content-main">`+card('公司档案',`<div class="profile-grid">${fields.map(([k,v])=>`<div class="profile-item"><span>${k}</span><b>${escape(v||'—')}</b></div>`).join('')}</div>`)+card('业务简介',`<p class="prose">${escape(p.longBusinessSummary||'数据源未提供业务简介。')}</p>`)+companyExtras()+`</div>${researchRail()}</div>`;
  }else if(tab===2){
    html=operatingView();
  }else if(tab===3)html=financialView();
  else if(tab===4){html=metricsView();
  }else if(tab===6){
    const peers=job.peers_data||[];
    html=alignedComparison()+card('同行比较',peers.length?table(['公司','交易币种','PE · TTM','PB','ROE','净利率','收入增长率'],[{symbol:job.ticker,...p},...peers].map(x=>[x.symbol+(x.error?'（不可用）':''),x.currency||'—',num(x.trailingPE),num(x.priceToBook),pct(x.returnOnEquity),pct(x.profitMargins),pct(x.revenueGrowth)])):'<p class="note">本次未选择同行。发起研究时，在“比较公司”中输入股票代码，使用逗号分隔。</p>')+`<p class="note warn">同行由你选择，建议使用业务模式和会计口径接近的公司。不混合不同币种的金额，不自动生成行业排名。</p>`+trend('Total Revenue','自身历史比较 · 营收');
  }
  const paused=job.status==='paused'||(tab===8&&job.automatic_valuation?.status==='paused');
  const active=['running','queued'].includes(job.status)||(tab===8&&['running','queued'].includes(job.automatic_valuation?.status));
  if(paused)html=card('已暂停',`<p class="note">已取得的资料与报告保留。继续时复用这些内容，未完成的模型步骤可能重新执行。</p><button class="button" id="resume-research" type="button">继续分析</button>`)+html;
  else if(active)html=card(job.pause_requested?'正在暂停':job.status==='completed'?'估值处理中':'分析进行中',`<p class="note">${escape(job.pause_requested?'等待当前模型调用或并行批次结束后暂停，已完成结果会保留。':(job.status==='completed'?job.automatic_valuation?.phase:job.phase)||'等待执行')}</p><button class="button" id="pause-research" type="button" ${job.pause_requested?'disabled':''}>${job.pause_requested?'正在暂停…':'暂停'}</button>`)+html;
  if(job.status==='failed')html=`<div class="card"><p class="note">研究未完成，已完成的分析保留在各栏目中。</p><button class="button" id="retry-research" type="button">重试这份研究（复用财务数据）</button></div>`+html;
  const openDetails=[...$('#content').querySelectorAll('details[open]')].map(d=>d.querySelector('summary')?.textContent);
  $('#content').innerHTML=html+evidencePanel()+(tab===3?filingPanel():'')+source();
  $('#content').querySelectorAll('details').forEach(d=>{if(openDetails.includes(d.querySelector('summary')?.textContent))d.open=true;});
  bindContent();if(tab===8){try{getValuationPanel().bind();}catch(err){console.error('Valuation controls failed',err);}}requestAnimationFrame(updateTabHint);
}
function chooseStudy(id){
  tab=0;chartMode='revenue';environmentMetric='score';environmentRange=30;financialQuery='';financialRow='';setSidebar(false);$('#tabs').scrollLeft=0;$('#detail-dialog').close();$('#research-dialog').close();
  window.scrollTo({top:0,behavior:'smooth'});return select(id);
}
async function select(id){
  if(job?.id!==id&&$('#detail-dialog').open)$('#detail-dialog').close();
  selected=id;const revision=++selectionRevision;renderHistory();
  $('#detail-loading').hidden=job?.id===id;
  $('#workspace').setAttribute('aria-busy','true');
  try{
    const result=await api('/api/jobs/'+encodeURIComponent(id));
    if(revision!==selectionRevision||selected!==id)return;
    if(result.automatic_valuation?.status==='completed'&&(result.automatic_valuation?.run_id!==jobCache.get(id)?.automatic_valuation?.run_id||jobCache.get(id)?.automatic_valuation?.status!=='completed'))valuationPanel?.invalidate(id);job=result;jobCache.set(id,result);if(window.history?.replaceState)window.history.replaceState(null,'','/?job='+encodeURIComponent(id)+(tab===8?'#valuation':tab===7?'#market-environment':''));draw();renderHistory();$('#error').hidden=true;
  }catch(e){if(revision===selectionRevision)error(e.message);}
  finally{if(revision===selectionRevision){$('#detail-loading').hidden=true;$('#workspace').setAttribute('aria-busy','false');}}
}
function jobName(j){return j.company_name||jobCache.get(j.id)?.data?.profile?.longName||'';}
function historyTime(j){
  const date=(j.date||'').slice(5).replace('-','/');
  const duplicate=jobs.some(other=>other.id!==j.id&&other.ticker===j.ticker&&other.date===j.date);
  const time=j.created_at?new Date(j.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}):'';
  return date+(duplicate&&time?' · '+time:' · '+({completed:'已完成',running:'分析中',queued:'等待中',failed:'未完成'}[j.status]||''));
}
let libraryScope='archived';
async function showLibrary(scope='archived'){
  libraryScope=scope;
  showDetail('资料库', '<p>正在读取…</p>');
  try{
    const data=await api('/api/jobs?scope='+scope);
    if(!$('#detail-dialog').open||libraryScope!==scope)return;
    $('#detail-content').innerHTML=`<div class="dialog-actions"><button class="button" data-library-scope="archived">已归档</button><button class="button" data-library-scope="trash">回收站</button></div><p class="note">${scope==='trash'?'删除的研究放入回收站，可恢复；不永久清除原始资料。':'已归档研究收纳在资料库，不显示在最近研究中，可随时查看或恢复。'}</p><div class="library-list">${data.jobs.length?data.jobs.map(j=>`<article class="library-row"><div><strong>${escape(j.ticker)}</strong><p>${escape(j.date)} · ${escape(jobName(j)||j.company_name||'公司研究')}</p></div><div><button class="button" data-library-open="${escape(j.id)}">查看</button><button class="button" data-library-action="active" data-job-id="${escape(j.id)}">恢复到最近研究</button>${scope==='archived'?`<button class="button" data-library-action="trash" data-job-id="${escape(j.id)}">删除</button>`:''}</div></article>`).join(''):'<p>暂无记录。</p>'}</div>`;
  }catch(e){$('#detail-content').textContent=e.message;}
}
async function changeLibraryState(id,state){
  if(state==='trash'&&!window.confirm('删除这份研究？将移入回收站，从最近研究和归档列表移除，可在资料库恢复。'))return;
  try{
    await api('/api/jobs/'+encodeURIComponent(id)+'/library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state})});
    if(selected===id&&state!=='active'){
      selected=null;job=null;selectionRevision++;window.history.replaceState(null,'','/');
    }
    await refresh();
    if($('#detail-dialog').open)await showLibrary(libraryScope);
  }catch(e){error(e.message);}
}
$('#show-library')?.addEventListener('click',()=>showLibrary());
document.addEventListener('click',e=>{
  const scope=e.target.closest('[data-library-scope]');if(scope)showLibrary(scope.dataset.libraryScope);
  const action=e.target.closest('[data-library-action]');if(action)changeLibraryState(action.dataset.jobId,action.dataset.libraryAction);
  const open=e.target.closest('[data-library-open]');if(open){$('#detail-dialog').close();chooseStudy(open.dataset.libraryOpen);}
});
function renderHistory(){
  $('#history-count').textContent=String(jobs.length).padStart(2,'0');
  $('#history').innerHTML=jobs.length?jobs.slice(0,15).map(j=>`<div class="history-row"><button data-id="${escape(j.id)}" class="history-item ${selected===j.id?'selected':''}" aria-current="${selected===j.id?'true':'false'}" title="${escape(j.ticker+' · '+j.date+' · '+statusLabel(j.status))}"><span class="history-monogram">${monogram(jobName(j)||j.ticker)}</span><span class="history-text"><strong>${escape(j.ticker)}</strong><small>${escape(historyTime(j))}</small></span><span class="history-check" aria-label="${escape(statusLabel(j.status))}">${j.status==='completed'?icon('check',14):j.status==='failed'?'!':icon('clock',14)}</span></button><details class="history-menu"><summary aria-label="整理 ${escape(j.ticker)} ${escape(j.date)}">⋯</summary><div><button data-library-action="archived" data-job-id="${escape(j.id)}">归档</button><button data-library-action="trash" data-job-id="${escape(j.id)}">删除</button></div></details></div>`).join(''):'<p class="history-empty">还没有研究记录。</p>';
  $('#history').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>chooseStudy(b.dataset.id));
}
function renderSearch(){
  const text=$('#search-input').value.trim().toLocaleLowerCase();
  const matches=jobs.filter(j=>(j.ticker+' '+jobName(j)).toLocaleLowerCase().includes(text));
  $('#search-results').innerHTML=matches.length?matches.slice(0,20).map(j=>`<button class="search-result" data-result-id="${escape(j.id)}"><span><b>${escape(j.ticker)}</b><span class="result-name">${escape(jobName(j)||'公司研究')}</span></span><small>${escape(j.date)} · ${escape(statusLabel(j.status))}</small></button>`).join(''):'<p class="note">没有找到已有研究，可通过“新建研究”建立新的分析。</p>';
}
async function refresh(){
  if(loading)return;loading=true;
  try{
    const data=await api('/api/jobs');jobs=data.jobs;renderHistory();
    $('#connection-state').innerHTML='<i></i> 研究工作台';$('#connection-state').className='';
    if(selected)await select(selected);
    else if(jobs.length)await select(jobs[0].id);
    else{$('#empty').hidden=false;$('#workspace').hidden=true;}
    if($('#search-dialog').open)renderSearch();
  }catch(e){
    error('连接本地服务失败：'+e.message);
    $('#connection-state').innerHTML='<i></i> 本地服务暂不可用';$('#connection-state').className='disconnected';
  }finally{loading=false;}
}
async function submitResearch(e){
  e.preventDefault();if(submitting)return;
  error('');submitting=true;$('#submit').disabled=true;$('#submit').textContent='正在创建研究…';
  try{
    const result=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:$('#ticker').value.trim(),market:$('#market').value,peers:$('#peers').value.split(/[,，\s]+/).filter(Boolean)})});
    $('#research-dialog').close();selected=result.id;tab=0;chartMode='revenue';
    // Select the accepted task even if another list refresh was already in flight.
    await chooseStudy(result.id);await refresh();toast('研究已创建，进度和完成的报告会自动更新。');
  }catch(e){error(e.message);}
  finally{submitting=false;$('#submit').disabled=false;$('#submit').textContent='发起分析 ↗';}
}
function openSearch(){
  $('#search-input').value='';renderSearch();
  if(!$('#search-dialog').open)$('#search-dialog').showModal();
  $('#search-input').focus();
}
$('#research-form').onsubmit=submitResearch;
$('#load-peers').onclick=async()=>{
  try{
    const ticker=$('#ticker').value.trim();
    if(!ticker){error('请先输入股票代码，再载入同行池。');$('#ticker').focus();return;}
    const r=await api('/api/peers/'+encodeURIComponent(ticker)+'?market='+encodeURIComponent($('#market').value));
    $('#peers').value=r.peers.join(', ');
    error(r.peers.length?'':'这家公司尚无保存的同行池；输入同行后发起研究即可保存。');
  }catch(e){error(e.message);}
};
$('#research-home').innerHTML=icon('grid')+'公司研究';
$('#research-home').onclick=()=>{tab=0;setSidebar(false);$('#tabs').scrollLeft=0;window.scrollTo({top:0,behavior:'smooth'});if(job)draw();};
$('#show-sources').innerHTML=icon('layers')+'资料与来源';
$('#show-sources').onclick=()=>{setSidebar(false);showSources();};
$('#open-search').innerHTML=icon('search',16)+'<span>查找研究</span><kbd>⌘ K</kbd>';
$('#open-search').onclick=openSearch;
$('#search-input').addEventListener('input',renderSearch);
$('#show-versions').onclick=()=>showDetail('研究归档',versionsPanel()||'<p class="note">本次归档暂无版本记录。</p>');
$('#toggle-sidebar').onclick=()=>setSidebar(!document.body.classList.contains('sidebar-open'));
$('#sidebar-shade').onclick=()=>setSidebar(false);
$('#tab-next').onclick=()=>$('#tabs').scrollBy({left:185,behavior:'smooth'});
$('#tabs').addEventListener('scroll',updateTabHint);
window.addEventListener('resize',updateTabHint);
$('#mobile-actions').onclick=()=>{
  const exportLink=job?.status==='completed'?`<a class="button" href="/api/jobs/${encodeURIComponent(job.id)}/export" target="_blank" rel="noopener">${icon('download',15)} 导出报告</a>`:'';
  showDetail('研究操作',`<div class="source-list">${exportLink}<button class="button" data-show-versions>查看研究归档</button><button class="button" data-show-sources>资料与来源</button></div>`);
};
document.addEventListener('click',e=>{
  if(e.target.closest('[data-new-research]'))openResearch();
  const close=e.target.closest('[data-close-dialog]');
  if(close)document.getElementById(close.dataset.closeDialog)?.close();
  const reportLink=e.target.closest('[data-report]');
  if(reportLink&&job)showDetail(reportLink.dataset.reportTitle||'完整研究报告',`<div class="prose">${report(reportLink.dataset.report)}</div>`);
  const chart=e.target.closest('[data-chart]');
  if(chart){chartMode=chart.dataset.chart;$('#trend-chart').innerHTML=trendChart();}
  if(e.target.closest('[data-show-sources]'))showSources();
  if(e.target.closest('[data-show-versions]'))$('#show-versions').click();
  const result=e.target.closest('[data-result-id]');
  if(result){$('#search-dialog').close();chooseStudy(result.dataset.resultId);}
  const evidence=e.target.closest('a[href^="#evidence-"]');
  if(evidence){
    const id=evidence.getAttribute('href').slice(1),panel=$('#evidence-panel');
    if(panel){e.preventDefault();$('#detail-dialog').close();panel.open=true;requestAnimationFrame(()=>{document.querySelector('.evidence-highlight')?.classList.remove('evidence-highlight');const row=document.getElementById(id);row?.classList.add('evidence-highlight');row?.scrollIntoView({block:'center',behavior:'smooth'});});}
  }
});
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openSearch();}
  if(e.key==='Escape')setSidebar(false);
});
refresh();
setInterval(()=>{if(jobs.some(j=>['running','queued'].includes(j.status)||['running','queued'].includes(j.automatic_valuation?.status))||job?.data_status==='loading'||['running','queued'].includes(job?.status)||['running','queued'].includes(job?.automatic_valuation?.status))refresh();},4000);


let valuationPanel;
function getValuationPanel(){
  if(!valuationPanel){
    if(typeof window.createValuationPanel!=='function')throw Error('估值模块未加载');
    valuationPanel=window.createValuationPanel({$,api,select,refresh,draw,card,table,escape,money,num,pct,fixed,empty,showDetail,getJob:()=>job});
  }
  return valuationPanel;
}
function valuationView(){
  try{return getValuationPanel().render();}
  catch(err){console.error('Valuation panel failed',err);return card('内在价值暂不可用','<p>请刷新后重试，其他研究板块仍可查看。</p>');}
}
