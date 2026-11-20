const $=s=>document.querySelector(s), tabs=['研究简报','公司资料','经营分析','财务报告','财务指标','新闻分析','比较分析'];
let selected=null,job=null,tab=0,frequency='annual',statement='income',jobs=[],loading=false;
let selectionRevision=0,submitting=false,chartMode='revenue',toastTimer=null;
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
function source(){const d=job.data;return d?`<p class="note">来源：${escape(d.source)} · 获取时间 ${escape(new Date(d.retrieved_at).toLocaleString())}<br>${job.imported?'历史报告的数据可能与后来补充的资料不同。':'页面与智能体使用同一份研究数据。'}研究日期：${escape(job.date)}。<br>${escape(d.market?.label||'')} ${escape(d.market?.coverage_note||'')}<br>${escape(d.period_note)} ${escape((d.errors||[]).join('；'))}</p>`:'';}

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
function statusLabel(status){return {completed:'已完成研究',running:'分析中',queued:'等待分析',failed:'研究未完成'}[status]||'研究归档';}
function ratingLabel(value){return {Buy:'买入',Overweight:'增持',Hold:'持有',Underweight:'减持',Sell:'卖出'}[value]||value||'尚未生成';}
function annualSeries(rowName,kind='income'){
  const t=job.data?.statements?.annual?.[kind],row=t?.rows?.find(r=>r.name===rowName);
  if(!row)return [];
  return t.periods.map((period,i)=>({period,year:period.slice(0,4),value:row.values[i]}))
    .filter(x=>x.value!=null&&Number.isFinite(Number(x.value))).slice(0,5).reverse();
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
  const sources=[['Yahoo Finance',job.data?'财务与公司资料':'待获取'],...(macro?[['FRED','宏观指标']]:[]),...(job.news_context?[['TrendRadar','新闻资料']]:[])];
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
  const first=researchCard('基本面研究','01',`<h2 class="article-title">${researchHeadline()}</h2><p class="lede">围绕本次财务表现，结合经营分析、财务指标与风险评估梳理公司基本面。</p><div class="findings">${findings.map(([title,key,text])=>`<div class="finding"><span class="finding-icon">${icon(text?'check':'clock',17)}</span><div><div class="finding-heading"><h4>${title}</h4>${reportButton(key,title,'阅读全文')}</div><p>${summaryMarkup(text||pendingReportText())}</p>${title==='变化原因与待验证解释'?'':summaryFact(key)}</div></div>`).join('')}</div>`,`<span class="source-label">财务事实 · 经营分析 · 风险核验</span>${reportButton('fundamentals_report','基本面研究','阅读完整研究')}`);
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
function financialView(){
  const p=job.data.profile||{},t=job.data.statements?.[frequency]?.[statement];
  const name={income:'利润表',balance:'资产负债表',cashflow:'现金流量表'}[statement];
  const currency=p.financialCurrency||'币种未提供';
  const toolbar=`<div class="section-heading"><h2>财务报告</h2><span class="mini-label">金额单位：百万 ${escape(currency)}</span></div><div class="toolbar"><select id="frequency" aria-label="报表频率"><option value="annual" ${frequency==='annual'?'selected':''}>年度报告</option><option value="quarterly" ${frequency==='quarterly'?'selected':''}>季度报告</option></select><select id="statement" aria-label="财务报表">${[['income','利润表'],['balance','资产负债表'],['cashflow','现金流量表']].map(([v,label])=>`<option value="${v}" ${statement===v?'selected':''}>${label}</option>`).join('')}</select></div>`;
  const content=t?.rows?.length?`<p class="scroll-guidance">金额：百万 ${escape(currency)} · 共 ${t.rows.length} 项科目 · 表内上下滚动查看更多，左右滑动看报告期 ↔</p>`+table(['科目 · 百万 '+currency,...t.periods],prioritizedFinancialRows(t.rows,statement).map(row=>[fieldName(row.name)===row.name?row.name:fieldName(row.name)+' / '+row.name,...row.values.map(v=>financialValue(row.name,v))])).replace('class="table-wrap"','class="table-wrap financial-scroll" tabindex="0" aria-label="可滚动财务报表"')+`<p class="note" style="margin-top:15px">每股项目保留原值，股数以百万股计，税率以百分比展示。其余科目按供应商原名保留。</p>`:'<p class="note">数据源未提供此报表。</p>';
  return toolbar+card('财务报表 · '+name,content);
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
function bindContent(){
  $('#tabs').querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=Number(b.dataset.tab);draw();});
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
  $('#frequency')?.addEventListener('change',e=>{frequency=e.target.value;draw();});
  $('#statement')?.addEventListener('change',e=>{statement=e.target.value;draw();});
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
  $('#progress').textContent=(job.phase||'')+(job.error?' · '+job.error:'');
  $('#export-report').href='/api/jobs/'+encodeURIComponent(job.id)+'/export';
  $('#export-report').hidden=job.status!=='completed'||!Object.keys(job.report_html||{}).length;
  $('#tabs').innerHTML=tabs.map((label,i)=>`<button class="${tab===i?'active':''}" aria-pressed="${tab===i}" data-tab="${i}">${label}${i===5&&(job.macro_update||job.macro_context)?'<span class="tab-indicator"></span>':''}</button>`).join('');
  $('#overview').hidden=!job.data;$('#overview').innerHTML=job.data?financialOverview():'';
  $('#footer-source').textContent='资料来源：'+(job.data?.source||'待获取')+(job.macro_context||job.macro_update?' · FRED':'')+' · '+job.date;
  let html='';
  if(tab===0)html=briefView();
  else if(tab===5){
    html=(job.macro_update||job.macro_context?card('宏观数据参与研究',`<p class="note">${job.macro_consumed?'本版本已将 FRED 数据传给原新闻分析师，并重新运行综合判断。财务数据和财务分析沿用原版本。':'当前宏观数据仅作补充，尚未参与原新闻结论与综合判断。'}</p><button id="reanalyze-macro" class="button" type="button">用这份宏观数据重新分析新闻与综合判断</button>`):'')+specialistReport('news_report','新闻分析 · 事件、影响与风险');
  }else if(!job.data){
    html=empty(job.data_status==='loading'?'正在加载公司资料和财务表格，请稍候…':'尚无结构化数据。发起新分析可同时获取公司资料、财务报表和指标。');
  }else if(tab===1){
    const fields=[['公司名称',p.longName],['行业',p.industry],['板块',p.sector],['国家 / 地区',p.country],['城市',p.city],['员工数',num(p.fullTimeEmployees,0)],['交易币种',p.currency],['财报币种',p.financialCurrency]];
    html=`<div class="content-grid"><div class="content-main">`+card('公司档案',`<div class="profile-grid">${fields.map(([k,v])=>`<div class="profile-item"><span>${k}</span><b>${escape(v||'—')}</b></div>`).join('')}</div>`)+card('业务简介',`<p class="prose">${escape(p.longBusinessSummary||'数据源未提供业务简介。')}</p>`)+companyExtras()+`</div>${researchRail()}</div>`;
  }else if(tab===2){
    html=`<div class="content-grid"><div class="content-main">`+specialistReport('operating_report','AI 经营分析 · 结论与风险')+`<div class="grid">${stat('营业收入 · TTM',money(p.totalRevenue),p.financialCurrency)}${stat('净利润 · TTM',money(p.netIncomeToCommon),p.financialCurrency)}${stat('收入增长率',pct(p.revenueGrowth),'供应商最近报告期口径')}${stat('经营利润率',pct(p.operatingMargins),'当前归档资料')}</div><br>`+trend('Total Revenue','营收趋势')+trend('Net Income','净利润趋势')+`<p class="note warn">当前接口未提供按产品、业务分部或地区拆分的数据，因此暂不展示这些维度。趋势图仅比较报告期数据，不能单独解释业务变化原因。</p></div>${researchRail()}</div>`;
  }else if(tab===3)html=financialView();
  else if(tab===4){
    html=specialistReport('metrics_report','AI 财务指标分析 · 结论与风险')+calculatedMetrics()+card('财务指标',table(['维度','指标','数值'],[['估值','预期市盈率',num(p.forwardPE)],['估值','市净率 PB',num(p.priceToBook)],['现金','自由现金流（供应商）',money(p.freeCashflow)+' '+(p.financialCurrency||'')],['盈利','经营利润率',pct(p.operatingMargins)],['盈利','净资产收益率 ROE',pct(p.returnOnEquity)],['盈利','毛利率',pct(p.grossMargins)],['盈利','净利率',pct(p.profitMargins)],['盈利','ROA',pct(p.returnOnAssets)],['成长','收入增长率',pct(p.revenueGrowth)],['成长','利润增长率',pct(p.earningsGrowth)],['偿债','流动比率',num(p.currentRatio)],['偿债','债务 / 股东权益',p.debtToEquity==null?'—':num(p.debtToEquity)+'%'],['规模','市值',money(p.marketCap)+' '+(p.currency||'')],['每股','EPS · TTM',num(p.trailingEps)]]))+`<p class="note">供应商当前指标与本地统一公式历史值分开展示；报告期间、平均余额和币种口径可能不同。</p>`;
  }else if(tab===6){
    const peers=job.peers_data||[];
    html=alignedComparison()+card('同行比较',peers.length?table(['公司','交易币种','PE · TTM','PB','ROE','净利率','收入增长率'],[{symbol:job.ticker,...p},...peers].map(x=>[x.symbol+(x.error?'（不可用）':''),x.currency||'—',num(x.trailingPE),num(x.priceToBook),pct(x.returnOnEquity),pct(x.profitMargins),pct(x.revenueGrowth)])):'<p class="note">本次未选择同行。发起研究时，在“比较公司”中输入股票代码，使用逗号分隔。</p>')+`<p class="note warn">同行由你选择，建议使用业务模式和会计口径接近的公司。不混合不同币种的金额，不自动生成行业排名。</p>`+trend('Total Revenue','自身历史比较 · 营收');
  }
  if(job.status==='failed')html=`<div class="card"><p class="note">研究未完成，已完成的分析保留在各栏目中。</p><button class="button" id="retry-research" type="button">重试这份研究（复用财务数据）</button></div>`+html;
  const openDetails=[...$('#content').querySelectorAll('details[open]')].map(d=>d.querySelector('summary')?.textContent);
  $('#content').innerHTML=html+evidencePanel()+(tab===3?filingPanel():'')+source();
  $('#content').querySelectorAll('details').forEach(d=>{if(openDetails.includes(d.querySelector('summary')?.textContent))d.open=true;});
  bindContent();requestAnimationFrame(updateTabHint);
}
function chooseStudy(id){
  tab=0;chartMode='revenue';setSidebar(false);$('#tabs').scrollLeft=0;$('#detail-dialog').close();$('#research-dialog').close();
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
    job=result;jobCache.set(id,result);draw();renderHistory();$('#error').hidden=true;
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
function renderHistory(){
  $('#history-count').textContent=String(jobs.length).padStart(2,'0');
  $('#history').innerHTML=jobs.length?jobs.slice(0,15).map(j=>`<button data-id="${escape(j.id)}" class="history-item ${selected===j.id?'selected':''}" aria-current="${selected===j.id?'true':'false'}" title="${escape(j.ticker+' · '+j.date+' · '+statusLabel(j.status))}"><span class="history-monogram">${monogram(jobName(j)||j.ticker)}</span><span class="history-text"><strong>${escape(j.ticker)}</strong><small>${escape(historyTime(j))}</small></span><span class="history-check" aria-label="${escape(statusLabel(j.status))}">${j.status==='completed'?icon('check',14):j.status==='failed'?'!':icon('clock',14)}</span></button>`).join(''):'<p class="history-empty">还没有研究记录。</p>';
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
    $('#connection-state').innerHTML='<i></i> 本地研究工作台';$('#connection-state').className='';
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
setInterval(()=>{if(jobs.some(j=>['running','queued'].includes(j.status))||job?.data_status==='loading'||['running','queued'].includes(job?.status))refresh();},4000);
