/* Intrinsic value panel. Shared research UI is accessed only through this host. */
window.createValuationPanel=function(host){
'use strict';
const {$,api,select,refresh,draw,card,table,escape,money,num,pct,fixed,empty}=host;
// Resolve the current study at use time, including after an asynchronous selection.
const job=new Proxy({}, {get:(_,key)=>host.getJob()?.[key], set:(_,key,value)=>{host.getJob()[key]=value;return true;}});
// Valuation drafts survive tab switches and are scoped to the selected research.
const valuationDrafts=new Map();
const valuationOpenSections=new Map();
const valuationNames={bear:'悲观',base:'基准',bull:'乐观'};
// Dates and prices below describe the saved result, never an unsaved form draft.
function valuationComparisonContext(result, study){
  const inputs=result?.inputs||{}, historical=result?.historical_context||study.historical_context;
  return {
    date:inputs.valuation_date||historical?.as_of_date||study.date,
    quoteDate:inputs.market_price_date||(historical?.as_of_date)||null,
    price:inputs.market_price,
    financialDate:inputs.balance_date||historical?.source_ledger?.map(s=>s.end).sort().at(-1)||null,
    filed:historical?.source_ledger?.map(s=>s.filed).sort().at(-1)||null,
    historical:!!(historical||study.research_snapshot?.point_in_time),
    explicitDate:!!(inputs.valuation_date||historical),
  };
}
function valuationFold(title,body,open=false){
  return `<details class="card valuation-fold" ${open?'open':''}><summary>${escape(title)}</summary><div class="valuation-fold-body">${body}</div></details>`;
}
function valuationScenarioCards(r){
  return `<div class="valuation-price-grid">${Object.entries(r.scenarios).map(([k,v])=>`<article class="valuation-price ${k==='base'?'is-base':''}"><span>${valuationNames[k]}情景</span><strong>${v.price>0?fixed(v.price,2):'残值≤0'}</strong><small>较参考股价 ${pct(v.upside)}</small><span class="valuation-equity">普通股价值 ${money(v.equity_value)}</span></article>`).join('')}</div>`;
}
function valuationDateSummary(result){
  const c=valuationComparisonContext(result,job),requested=new URLSearchParams(globalThis.location?.search||'').get('valuation_requested_date');
  return `<div class="valuation-comparison"><div><span>${escape(job.ticker)} · ${escape(c.date||job.date)}</span><h3>每股内在价值</h3></div><div class="valuation-reference"><span>当时参考股价 · ${escape(c.quoteDate?String(c.quoteDate).slice(0,10):'日期未记录')}</span><strong>${fixed(c.price,2)} <small>${escape(result.currency||job.valuation_setup.currency)}</small></strong></div></div><p class="note">${requested&&requested!==c.date?'所选 '+escape(requested)+' 为非交易日或无报价日，采用 '+escape(c.date)+'。 ':''}财务基准 ${escape(c.financialDate||'见来源')}${c.filed?' · 披露于 '+escape(c.filed):''} · ${c.historical?'仅用当时公开资料':'归档估值，非实时价格'}</p>`;
}
function valuationDateChooser(result){
  const c=valuationComparisonContext(result,job);
  const tickers=[...new Set([job.ticker,'AAPL','MSFT','NVDA','BA','0700.HK','0941.HK','0291.HK','0883.HK','2899.HK','0669.HK','0002.HK','1211.HK','1177.HK','1308.HK'])];
  return `<section class="card valuation-date-bar"><form id="valuation-date-form"><label class="valuation-field">股票<select id="valuation-date-ticker">${tickers.map(t=>`<option ${t===job.ticker?'selected':''}>${escape(t)}</option>`).join('')}</select></label><label class="valuation-field">观察日期<input id="valuation-date-day" type="date" required min="2013-02-05" value="${escape(c.date||job.date)}"></label><button class="button" type="submit">更新估值</button><p role="status" id="valuation-date-status" aria-live="polite"></p></form><p class="note">在本页查看所选日期的估值与股价。切换日期将运行公司研究与自动估值，仅用观察日前披露；已有同日研究可复用。</p></section>`;
}
function valuationModelExplanation(result){
  if(result?.inputs?.automatic_research)return '';
  if(!['MSFT','NVDA'].includes(job.ticker))return '';
  const sc=result?.inputs?.scenarios,policy=result?.policy_audit;
  if(!sc?.base?.growth||!policy)return '';
  const b=sc.base,u=sc.bull;
  const reason=job.ticker==='MSFT'?'模型将软件业务历史增长向行业长期增速收敛，利润率也向历史水平回归；没有单独预测 Azure、订阅业务占比及经营杠杆。持续增长或利润率扩张强于该路径时，会低估价值。':'模型把半导体视为周期行业，历史增长仅占40%权重，并向行业长期增速回落；没有单独预测数据中心、AI需求、产品结构和竞争优势持续期。结构性增长可能超出当前乐观情景。';
  return card('为何估值可能明显偏离股价？',`<p>${reason}</p><p>本次基准增长 ${pct(b.growth[0])} → ${pct(b.growth.at(-1))}，营业利润率 ${pct(b.margins[0])} → ${pct(b.margins.at(-1))}；乐观末年增长 ${pct(u.growth.at(-1))}、利润率 ${pct(u.margins.at(-1))}。</p><p class="note">这些是历史经营数据与行业规则生成的情景，不是完整的公司分部研究，也不是统计置信区间。历史适配仍主要使用年报，未补齐季度及过渡期，财年和预测年对齐需进一步完善。价格差异本身不能证明模型或市场哪方正确；应检验后来实际收入、利润率与预测的偏差。买入规则额外要求悲观估值八折，只影响交易，不影响这里的估值。没有为贴近股价调整参数。</p>`);
}
async function openDatedValuation(event){
  event.preventDefault();
  const button=event.currentTarget.querySelector('button'),status=$('#valuation-date-status');
  const ticker=$('#valuation-date-ticker').value,day=$('#valuation-date-day').value;
  button.disabled=true;status.textContent='正在创建该日期的公司研究与自动估值…';
  try{
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day))throw Error('请选择有效日期');
    const result=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker,date:day,peers:[]})});
    window.history.replaceState(null,'','/?job='+result.id+'#valuation');
    await refresh();await select(result.id);window.scrollTo({top:0});
  }catch(err){status.textContent=err.message;}
  finally{button.disabled=false;}
}

function valuationInput(label,path,value,scale=1){
  const auto=(job?.valuation||job?.valuation_setup?.saved)?.inputs?.automatic_research||job?.valuation_setup?.inputs?.automatic_research;
  const fact=['revenue','cash','debt','shares','market_price','forecast_start','non_operating_assets',...(auto?['other_claims','dilution']:[])].includes(path);
  const shown=value==null?'':Number((value/scale).toFixed(scale===.01?2:scale===1e6?3:4));
  const missing=path==='other_claims'&&value==null;
  return `<label class="valuation-field ${missing?'valuation-missing':''}"><span>${escape(label)}</span><input type="number" step="any" ${fact&&(job?.historical_context||(job?.valuation||job?.valuation_setup?.saved)?.inputs?.method!=='company_fcf_bridge')?'readonly data-v-fact="true"':''} ${missing?'required placeholder="待核实，不能自动填 0"':''} data-v-path="${escape(path)}" data-v-scale="${scale}" value="${escape(shown)}">${missing?'<small>核实优先股、少数股东等后填写；确认无此项才填 0。</small>':''}</label>`;
}
function companyResearchView(p){
  const b=p.research_basis;
  return card('人工核实的公司方案 · Alphabet',`<p>本版本由研究者核实并编写结构化方案，系统计算；不是自动生成版本。由本份经营研究提出的核查问题出发，补充 SEC 财报及公司业绩会，生成分部预测和股权价值调整。原研究文字保留，补充核查不自动改变原投资评级。</p>`+table(['研究结论 / 问题','补充核实','如何进入估值'],b.links.map(x=>[escape(x.finding),escape(x.evidence),escape(x.implementation)]))+`<p class="note">2026 前三季度为披露事实；Q4与2027–2036为研究情景。2032年起逐步向稳定期收敛。参数不是管理层定量指引，也不是为匹配股价反推。</p><details><summary>股权调整与来源</summary>`+table(['项目','处理'],[['普通股数','A+B+C三类合计；不能只用GOOGL A类股数'],['现金与投资','权益投资单列，SpaceX受限股份不视为可用现金'],['优先股','最大转股比例摊薄＋剩余股息现值；不扣本金，capped call收益未计入'],['其他权益要求','少数股东账面代理＋净递延税负＋已形成的长期税款'],['租赁与承诺','融资租赁计入债务；经营租赁费用留在利润内；采购承诺不重复整笔扣减']])+`<ul>${b.judgments.map(t=>`<li>${escape(t)}</li>`).join('')}</ul><ol>${b.sources.map(x=>`<li><a href="${escape(x.url)}" target="_blank" rel="noopener">${escape(x.id)} · ${escape(x.filed)}</a></li>`).join('')}</ol></details><details><summary>预测生成依据与逐年分部结果</summary>${Object.entries(p.scenarios).map(([k,v])=>`<h4>${valuationNames[k]}</h4>`+table(['年度','Services收入（十亿）','Cloud收入（十亿）','其他收入（十亿）','汇总利润率','资本开支率'],v.segment_forecasts.map((d,i)=>[d.year,fixed(d.services_revenue/1e9,2),fixed(d.cloud_revenue/1e9,2),fixed(d.other_revenue/1e9,2),pct(v.margins[i]),pct(v.capex_ratios[i])]))).join('')}<p class="note">分部表记录方案生成时的路径。下方手动编辑汇总增长或利润率后属于独立压力测试，分部原方案不会反向重写。</p></details><button class="button" id="valuation-default-policy" type="button">重新载入本公司研究预测</button>`);
}
function automaticValuationStatus(){
  if(job.historical_context)return '';
  const a=job.automatic_valuation, busy=['queued','running','pending'].includes(a?.status);
  const labels={pending:'等待研究完成',queued:'等待生成',running:'自动核实中',completed:'公司估值已自动生成',needs_review:'缺少关键证据',not_applicable:'当前模型不适用',failed:'自动核实未完成',paused:'已暂停'};
  const body=`<p><strong>${escape(labels[a?.status]||'尚未生成公司预测')}</strong>${a?.phase?' · '+escape(a.phase):''}</p>${a?.gaps?.length?'<ul>'+a.gaps.map(x=>`<li>${escape(x)}</li>`).join(''):'<p class="note">自动核实原始披露并生成公司预测；保留旧版本。</p>'}${job.status==='completed'&&a?.status!=='paused'?`<button class="button" id="generate-company-valuation" type="button" ${busy?'disabled':''}>${busy?'正在处理…':a?.status==='completed'?'重新核实并生成':'生成公司估值'}</button>`:''}<p role="status" id="auto-valuation-status"></p>`;
  return valuationFold('公司预测 · '+(labels[a?.status]||'尚未生成'),body,busy||!!a?.gaps?.length||!job.valuation);
}
function capitalStructureView(p){
  const b=p.capital_structure;if(!b)return '';
  const termLabels={depositary_shares:'存托份额',receipts_per_preferred:'每优先股对应存托份额',liquidation_per_preferred:'每优先股清算额',coupon_percent:'年股息率',conversion_date:'强制转股日期',first_dividend_date:'首次付息日期',payment_schedule:'付息时间表',equal_allocation:'系列分配依据'};
  return card('自动核实 · 优先股与普通股权益分配',`<p>${escape(b.assumptions)}</p>`+table(['系列','优先股数量（百万）','每优先股转股区间','采用转股股数（百万）','强制转股日'],b.series.map(x=>[escape(x.series),fixed(x.preferred_shares/1e6,4),fixed(x.conversion_min,4)+' – '+fixed(x.conversion_max,4),fixed(x.conversion_shares/1e6,4),escape(x.conversion_date)]))+`<p>最大转股新增 <strong>${fixed(b.conversion_shares/1e6,4)} 百万普通股</strong>；财务基准日至估值日的计划股息扣减 ${fixed(b.elapsed_dividends/1e6,3)} 百万 ${escape(job.valuation_setup.currency)}；估值日后剩余股息现值 ${fixed(b.future_dividend_pv/1e6,3)} 百万。</p><p class="note">采用完全摊薄的静态保守分配，不是精确的优先股期权定价。优先股本金不再从债务或其他权益要求中重复扣除。已过付息日按正常现金付息假设计入，不表示获得银行支付凭证；未来股息折现率独立于经营WACC。</p><details><summary>逐期股息与条款来源</summary>`+table(['发行组','付息日','计划金额（百万）','现值 / 已过金额（百万）','期间'],b.dividends.map(x=>[escape(x.group),escape(x.date),fixed(x.amount/1e6,3),fixed(x.pv/1e6,3),x.status==='future'?'估值日之后':'财务基准日之后、估值日前']))+b.terms.groups.map(g=>`<h4>${escape(g.name)}</h4><p>股息折现率 ${pct(g.dividend_discount_rate)} · ${escape(g.discount_reason)}</p>`+table(['条款','原文'],Object.entries(g).filter(([k,v])=>v&&typeof v==='object'&&v.quote).map(([k,v])=>[escape(termLabels[k]||k),escape(v.source_id+' · '+v.quote)]))).join('')+'</details>');
}
function contingentClaimsView(r){
  const risks=r?.inputs?.automatic_research?.contingent_claims||[];
  if(!risks.length)return '';
  return card('或有担保 · 条件估值压力测试',`<p>上方DCF未扣除尚无法可靠估计的额外担保损失，属于条件估值。下列比例是压力测试，不是发生概率；不改变增长率、利润率或其他经营预测。</p>`+risks.map(x=>`<h4>${escape(x.label)} · 披露上限 ${money(x.maximum_exposure)} ${escape(r.currency)}</h4><p>${escape(x.trigger)}</p><p>${escape(x.treatment)}</p>`+table(['假设额外净损失 / 上限','每股扣减','压力后悲观','压力后基准','压力后乐观'],x.stress.map(s=>[pct(s.loss_fraction),fixed(s.per_share_reduction,2),...['bear','base','bull'].map(k=>fixed(r.scenarios[k].price-s.per_share_reduction,2))]))+`<p class="note">${escape(x.basis)} 多项风险分别测试，不代表同时发生。负数表示权益残值，不是负股价。</p><details><summary>原文依据</summary><p>${escape(x.source_id+' · '+x.quote)}</p></details>`).join(''));
}
function automaticResearchView(p){
  const b=p.automatic_research;
  return card('公司专属预测 · '+escape(job.ticker),`<p>${escape(b.business_model)}</p><h4>研究如何转化为预测</h4><p>${escape(b.forecast_reasoning)}</p><h4>股东价值调整</h4><p>${escape(b.equity_reasoning)}</p><p class="note">${escape(b.limitations)}</p><details><summary>自动核实的原文与处理方式</summary>`+table(['核查项目','处理方式','原文依据'],b.checks.map(x=>[escape(x.topic),escape(x.treatment),escape(x.source_id+' · '+x.quote)]))+table(['输入','分项','原文数值 × 单位','披露期间'],Object.entries(b.facts).flatMap(([k,rows])=>rows.map(x=>[escape(k),escape(x.label),escape(x.sign+' × '+x.amount+' × '+x.scale),escape(x.period_end)])))+`<ol>${b.sources.map(x=>`<li><a href="${escape(x.url)}" target="_blank" rel="noopener">${escape(x.id+' · '+x.form+' · '+x.filed)}</a></li>`).join('')}</ol></details><p class="note">预测为估值日起滚动十年，首年须衔接财务基期；年度标签表示预测期间终点的近似年份。财务事实与预测判断分别保存。修改下方预测属于手动压力测试，不改自动方案。</p><button class="button" id="valuation-default-policy" type="button">重新载入本公司自动生成预测</button>`);
}
function valuationGuide(setup,p){
  if(p.automatic_research)return automaticResearchView(p);
  if(p.research_basis)return companyResearchView(p);
  const mapped=!!setup.policy_audit;
  return card(mapped?'预测来源 · 历史经营数据＋行业规则':'预测来源 · 通用预填，尚未完成行业适配',`<div class="valuation-guide"><div><strong>财务基准：通常无需手调</strong><p>收入、现金、债务和股数来自已获取资料。仅在核对原始披露发现口径错误时修正；历史回测事实保持锁定。</p></div><div><strong>股东价值调整：核实后填写</strong><p>经营现金储备、其他权益要求和摊薄包含判断。空白表示待核实，不代表零；不要为得到理想估值而修改。</p></div><div><strong>经营预测：默认可试算，有依据再改</strong><p>增长率、利润率、再投资和折现率都是假设。可以依据公司指引、业务结构和竞争格局调整，并记录理由。</p></div></div><p class="note">${mapped?'“统一”指研究页与回测共用生成规则和计算引擎，并非所有行业用相同数值。规则结合该公司历史数据与行业预设，尚不等于分析师完成的公司专项预测。':'该股票尚未映射行业规则，目前使用通用增长与利润率预填。不能称为已完成的行业估值，也不是公司研究报告自动推导出的结论。'}</p><details><summary>版本与预填操作</summary><p class="note">默认：${escape(setup.inputs.assumption_policy)}；当前草稿标记：${escape(p.assumption_policy||'旧版')}。旧档案可能沿用旧标签；以上来源说明以实际行业适配状态为准。</p><button class="button" type="button" id="valuation-default-policy">重新载入默认预测</button><p class="note">仅替换预测情景和依据，不改财务金额、股数或保存结果。需点击计算并保存才生效。</p></details>`);
}
function valuationView(){
  const setup=job.valuation_setup;
  if(!setup)return empty('估值数据尚未加载。');
  if(setup.missing.length)return valuationDateChooser(job.valuation||setup.saved)+automaticValuationStatus()+card(['pending','queued','running'].includes(job.automatic_valuation?.status)?'内在价值 · 尚未完成核实':'内在价值 · 资料待补充',`<p>${escape(setup.missing.join('；'))}</p><p class="note">当前不生成估值，避免使用缺失数据。</p>`);
  if(!valuationDrafts.has(job.id))valuationDrafts.set(job.id,structuredClone(setup.saved?.inputs||setup.inputs));
  const p=valuationDrafts.get(job.id), r=job.valuation||setup.saved;
  if(p.method==='company_fcf_bridge')return valuationDateChooser(r)+reviewedValuationView(p,r,setup)+automaticValuationStatus();
  const overviewHeader=valuationDateChooser(r);
  let html=automaticValuationStatus();
  html+=valuationGuide(setup,p);
  html+=capitalStructureView(p);
  if(job.backtest_origin)html+=card('历史回测 · 同一份研究估值',`<p>该估值使用 ${escape(job.date)} 之前公开的财报。${r?.input_digest===job.backtest_origin.input_digest?'当前输入与原回测完全一致。':'当前为修改后的事后研究版本，与原回测输入不同。'}修改预测假设会保存事后研究版本，原回测保持不变。历史财报、股数与参考价格不可改写。</p><p>统一引擎 ${escape(r?.version||setup.version)} · 默认假设不是当年已归档的AI预测。</p><button class="button" type="button" id="backtest-research-assumptions">用已保存的预测假设回测后续区间</button><p class="note">仅传递增长、利润率、再投资、税率和折现率等预测假设；财报、股数和行情逐期更新。起点不会早于本研究日。请先保存表单修改。</p><p role="status" id="backtest-research-status"></p>`);
  const valuationNotes=html,valuationHeader=overviewHeader;html='';
  html+=`<form id="valuation-form"><section class="card valuation-basis"><h3>估值基准与股东价值调整</h3><p class="note">金额：百万 ${escape(setup.currency)}；股数：百万股。显示小数经过简化，未编辑的输入保留原始计算精度。</p><h4>已获取的财务基准</h4><p class="note">${p.automatic_research?'财务基准由原始披露分项汇总，股价使用已归档收盘价。更改事实请重新核实。':p.research_basis?'财务金额及2026预测基数由公司核实方案提供；更改事实需重新核实。各情景采用各自2026收入基数，以下收入显示基准值。':job.historical_context?'历史事实已锁定；更换日期请使用顶部日期选择。':'默认只读。核对来源发现差异时，才启用修正。'}</p>${job.historical_context||p.research_basis||p.automatic_research?'':'<label class="valuation-edit-toggle"><input type="checkbox" id="valuation-edit-facts"> 允许修正财务基准（需在下方说明依据）</label>'}<div class="valuation-grid">`;
  for(const [key,label,scale] of [['revenue','年度收入',1e6],['cash','现金与可用存款 / 证券',1e6],['debt','有息债务',1e6],['shares','发行在外股数',1e6],['market_price','参考股价',1],['forecast_start','预测首年',1],...(p.non_operating_assets!==undefined?[['non_operating_assets','非经营投资（折扣前）',1e6]]:[])])html+=valuationInput(label,key,p[key],scale);
  html+=`</div><h4>需要核实的价值调整</h4><div class="valuation-grid">`;
  for(const [key,label,scale] of [['operating_cash','经营必需 / 不可分配现金',1e6],['other_claims','优先股及其他权益要求',1e6],['dilution','摊薄倍数（至少 1）',1]])html+=valuationInput(label,key,p[key],scale);
  html+=`</div><p class="note">优先股、少数股东权益等按具体口径核实；使用转股后股数时，不重复扣同一优先股本金。已有数值仍可能是账面代理或研究假设，详见来源。</p></section><div class="valuation-section-title"><h3>逐年经营假设</h3><p>不要求逐项手调。展开情景检查默认路径，有公司证据时再修改；三情景不是统计置信区间。</p></div>`;
  for(const name of ['bear','base','bull']){
    const s=p.scenarios[name];
    html+=`<details class="card valuation-scenario"><summary>${valuationNames[name]}情景 <span class="valuation-scenario-summary">增长 ${pct(s.growth[0])} → ${pct(s.growth.at(-1))} · 利润率 ${pct(s.margins.at(-1))}</span></summary><div class="table-wrap"><table class="valuation-year-table"><thead><tr><th>预测年度</th><th>收入增长 %</th><th>营业利润率 %</th>${s.da_ratios?'<th>折旧 / 收入 %</th><th>资本开支 / 收入 %</th>':''}</tr></thead><tbody>`;
    s.growth.forEach((g,i)=>{html+=`<tr><td>${p.forecast_start+i}</td><td>${valuationInput('增长 '+(i+1),`scenarios.${name}.growth.${i}`,g,.01)}</td><td>${valuationInput('利润率 '+(i+1),`scenarios.${name}.margins.${i}`,s.margins[i],.01)}</td>${s.da_ratios?`<td>${valuationInput('折旧 '+(i+1),`scenarios.${name}.da_ratios.${i}`,s.da_ratios[i],.01)}</td><td>${valuationInput('资本开支 '+(i+1),`scenarios.${name}.capex_ratios.${i}`,s.capex_ratios[i],.01)}</td>`:''}</tr>`;});
    html+=`</tbody></table></div><div class="valuation-grid">`;
    for(const [key,label] of [['da_ratio','折旧摊销 / 收入 %'],['capex_ratio','资本开支 / 收入 %'],['nwc_ratio','新增营运资本 / 新增收入 %'],['tax_rate','正营业利润税率 %'],['wacc','WACC %'],['terminal_growth','永续增长率 %']])if(!(s.da_ratios&&['da_ratio','capex_ratio'].includes(key)))html+=valuationInput(label,`scenarios.${name}.${key}`,s[key],.01);
    if(s.terminal_roic!==undefined)html+=valuationInput('稳定期投入资本回报率 %',`scenarios.${name}.terminal_roic`,s.terminal_roic,.01);
    if(s.asset_haircut!==undefined)html+=valuationInput('非经营投资资产价值折扣 %',`scenarios.${name}.asset_haircut`,s.asset_haircut,.01);
    html+='</div></details>';
  }
  html+=`<div class="card"><label class="valuation-field">假设依据与调整说明（必填）<textarea id="valuation-rationale" rows="5" maxlength="6000" placeholder="说明经营恢复路径、WACC、现金可分配性、优先股/摊薄处理，以及与经营报告的关系">${escape(p.rationale)}</textarea></label><p class="note">计算会保存一个新的估值版本，原始研究快照不变。</p><button class="button" type="submit">计算并保存估值</button><p role="status" id="valuation-status"></p></div></form>`;
  const valuationForm=html;html=valuationHeader;
  if(r){
    const b=r.inputs.scenarios.base;
    html+=`<section class="card valuation-overview">${valuationDateSummary(r)}${valuationScenarioCards(r)}<div class="valuation-assumption-strip"><span>基准增长 <b>${pct(b.growth[0])} → ${pct(b.growth.at(-1))}</b></span><span>营业利润率 <b>${pct(b.margins[0])} → ${pct(b.margins.at(-1))}</b></span><span>WACC <b>${pct(b.wacc)}</b></span><span>预测期 <b>${r.inputs.years} 年</b></span></div><p class="note">${r.inputs.automatic_research?(job.research_snapshot?.point_in_time?'公司自动研究 · 历史重建，非当年留存预测':'公司研究自动预测'):job.historical_context?'历史规则情景 · 非当年生成的 AI 预测':'情景预测'} · 每股价值为估值日现值，不是未来目标价。${r.inputs.automatic_research?.contingent_claims?.length?' <strong>未扣未知额外担保损失，详见下方压力测试。</strong>':''}</p>${r.inputs.automatic_research?.limitations?`<details class="note"><summary>条件估值 · 使用前请查看估值局限</summary><p>${escape(r.inputs.automatic_research.limitations)}</p></details>`:''}<button type="button" class="button valuation-export" id="valuation-download">导出估值</button></section>`;
    const resultOverview=html;html='';
    html+=contingentClaimsView(r);
    if(r.inputs.capital_structure)html+=card('普通股股东价值桥',table(['情景','扣股息后权益池（百万）','完全摊薄股数（百万）','分配给现有普通股（百万）'],Object.entries(r.scenarios).map(([k,v])=>[valuationNames[k],fixed(v.equity_pool/1e6),fixed(v.effective_shares/1e6,4),fixed(v.equity_value/1e6)]))+`<p class="note">每股价值＝扣股息后权益池÷完全摊薄股数；现有普通股价值＝每股价值×现有普通股数。新增转股股东的权益不计入现有普通股总价值。</p>`);
    if(r.inputs.research_bridge)html+=card('过渡期与优先股权益分配',table(['情景','估算季末至估值日净现金变化（十亿）','估值日后剩余FCFF现值（十亿）','优先股剩余股息现值（十亿）','转股后总股数（百万）'],Object.entries(r.scenarios).map(([k,v])=>[valuationNames[k],fixed(v.research_transition.estimated_elapsed_cash/1e9,2),fixed(v.research_transition.remaining_pv/1e9,2),fixed(v.research_transition.dividend_pv/1e9,2),fixed(v.effective_shares/1e6,3)]))+`<p class="note">季度现金流按天数估计分配，未取得实际每日现金流。优先股按最大转股比例增加约54.458百万股；当前普通股价值为转股后权益池按现有普通股占比分配。终值占比超过100%意味着显式期现金流净现值为负，不能当作高确定性估值。</p>`);
    html+=valuationModelExplanation(r);
    if(r.policy_audit?.periods)html+=card('行业假设依据与股权价值桥',`<p>行业政策：${escape(r.policy_audit.sector)} · 使用期间 ${escape(r.policy_audit.periods.join('、'))}。营收增长观察值 ${r.policy_audit.observed_growth.map(pct).join('、')||'无同比观察，使用行业显式先验'}；利润率观察值 ${r.policy_audit.observed_margins.map(pct).join('、')}。</p>`+table(['项目','百万 '+r.currency],[['现金及可用存款/证券',fixed(r.inputs.cash/1e6)],['经营现金储备',fixed(r.inputs.operating_cash/1e6)],['非经营投资（折扣前）',fixed((r.inputs.non_operating_assets||0)/1e6)],['基准投资价值（折扣后）',fixed((r.scenarios.base.non_operating_asset_value||0)/1e6)],['债务',fixed(r.inputs.debt/1e6)],['其他权益要求',fixed(r.inputs.other_claims/1e6)]]));
    const sn=r.sensitivity;
    html+=card('敏感性 · 基准情景每股价值',`<p class="note">行：WACC；列：永续增长率。中间单元格等于基准估值；— 表示不满足模型条件。</p><div class="table-wrap"><table class="valuation-matrix"><thead><tr><th>WACC / g</th>${sn.growths.map(g=>`<th>${pct(g)}</th>`).join('')}</tr></thead><tbody>${sn.prices.map((row,i)=>`<tr><th>${pct(sn.waccs[i])}</th>${row.map((value,j)=>`<td class="${i===2&&j===2?'valuation-center':''}">${fixed(value,2)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    const rv=r.reverse;
    html+=card('反向 DCF · 估值日参考价要求怎样的盈利能力',rv.status==='solved'?`<p>保持基准情景其余条件不变，逐年营业利润率需要整体调整 <strong>${pct(rv.margin_shift)}</strong>（百分点口径），最后一年达到 <strong>${pct(rv.implied_terminal_margin)}</strong>。</p><p>反算价格：${fixed(rv.reproduced_price,2)}；归档参考价：${fixed(rv.target_price,2)} ${escape(r.currency)}。</p><p class="note">${escape(rv.basis)}</p>`:`<p>${escape(rv.reason)}</p>`);
    html+=`<details class="card"><summary>现金流预测与企业价值到每股价值</summary>`;
    for(const [name,v] of Object.entries(r.scenarios))html+=`<h4>${valuationNames[name]} · 金额百万 ${escape(r.currency)}</h4>`+table(['年度','收入','EBIT','税','折旧','资本开支','营运资本增加','FCFF','折现值'],v.rows.map(row=>[row.year,...['revenue','ebit','tax','da','capex','delta_nwc','fcff','pv'].map(k=>fixed(row[k]/1e6))]))+`<p class="note">${escape(v.warning||'')} 稳定期 FCFF：${fixed(v.terminal_fcff/1e6)}；终值现值：${fixed(v.terminal_pv/1e6)}。</p>`;
    html+=`<p>FCFF = EBIT − 正利润税额 + 折旧 − 资本开支 − 营运资本增加。普通股价值 = 企业价值 + 现金 − 经营必需现金 + 非经营资产×(1−情景折扣) − 债务 − 其他权益要求。采用稳定期ROIC时，终值FCFF=税后经营利润×(1−g/ROIC)，不重复扣资本开支和营运资本。每股价值 = 普通股价值 ÷（股数 × 摊薄倍数）。</p><p class="note">计算版本 ${escape(r.version)} · 输入校验 ${escape(r.input_digest.slice(0,16))}</p></details>`;
    html=resultOverview+valuationFold(r.inputs.automatic_research?.contingent_claims?.length?'风险压力、敏感性与反向 DCF':'敏感性、反向 DCF 与计算明细',html);
  }
  html+=valuationFold('调整估值假设',valuationForm,!r);
  html+=valuationFold('研究依据与股东价值调整',valuationNotes);
  html+=`<details class="card"><summary>来源、假设与模型边界</summary>`+table(['输入','历史值','来源字段','期间'],Object.entries(setup.evidence).map(([k,e])=>[k,num(e.value),e.field,e.period||'缺失']))+`<ul>${(r?.warnings||setup.warnings).map(w=>`<li>${escape(w)}</li>`).join('')}</ul></details>`;
  return html;
}
function bindValuation(){
  const sectionJob=job.id;
  $('#content').querySelectorAll('details.valuation-fold').forEach(el=>{
    const key=el.querySelector('summary').textContent;
    if(valuationOpenSections.get(sectionJob)?.has(key))el.open=true;
    el.addEventListener('toggle',()=>{
      if(!valuationOpenSections.has(sectionJob))valuationOpenSections.set(sectionJob,new Set());
      const opened=valuationOpenSections.get(sectionJob);if(el.open)opened.add(key);else opened.delete(key);
    });
  });
  $('#generate-company-valuation')?.addEventListener('click',async e=>{
    e.target.disabled=true;const id=job.id;
    try{await api('/api/jobs/'+encodeURIComponent(id)+'/automatic-valuation',{method:'POST'});valuationDrafts.delete(id);await select(id);}
    catch(err){$('#auto-valuation-status').textContent=err.message;e.target.disabled=false;}
  });
  $('#valuation-edit-facts')?.addEventListener('change',e=>{if(!job.historical_context)$('#valuation-form').querySelectorAll('[data-v-fact]').forEach(el=>el.readOnly=!e.target.checked);});
  $('#valuation-date-form')?.addEventListener('submit',openDatedValuation);
  $('#valuation-default-policy')?.addEventListener('click',()=>{
    const defaults=job.valuation_setup.inputs,draft=valuationDrafts.get(job.id);
    for(const key of ['scenarios','rationale','assumption_policy'])draft[key]=structuredClone(defaults[key]);draw();
  });
  $('#backtest-research-assumptions')?.addEventListener('click',async()=>{
    const button=$('#backtest-research-assumptions'),status=$('#backtest-research-status');button.disabled=true;
    try{
      const origin=job.backtest_origin.parameters;
      const payload={tickers:[job.ticker],start:job.date,end:origin.end,inspect_date:job.date,capital:origin.capital,safety_margin:origin.safety_margin,cost_bps:origin.cost_bps,research_job_id:job.id};
      const result=await api('/api/valuation-backtests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      status.innerHTML=`回测已创建。<a href="/backtest?id=${escape(result.id)}">查看策略回测</a>`;button.disabled=false;
    }catch(error){status.textContent=error.message;button.disabled=false;}
  });
  const form=$('#valuation-form');if(!form)return;
  form.querySelectorAll('[data-v-path]').forEach(el=>el.addEventListener('input',()=>{
    const p=valuationDrafts.get(job.id),parts=el.dataset.vPath.split('.');let target=p;
    for(const part of parts.slice(0,-1))target=target[part];
    target[parts.at(-1)]=el.value.trim()===''?null:Number(el.value)*Number(el.dataset.vScale);
  }));
  $('#valuation-rationale').addEventListener('input',e=>{valuationDrafts.get(job.id).rationale=e.target.value;});
  form.addEventListener('submit',async e=>{
    e.preventDefault();const id=job.id,button=form.querySelector('[type=submit]'),status=$('#valuation-status');button.disabled=true;status.textContent='正在计算并保存…';
    try{const result=await api('/api/jobs/'+encodeURIComponent(id)+'/valuation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(valuationDrafts.get(id))});
      if(job.id===id){job.valuation=result;job.valuation_setup.saved=result;draw();$('#valuation-status').textContent='已计算并保存。';}
    }catch(err){status.textContent=err.message;}finally{button.disabled=false;}
  });
  $('#valuation-download')?.addEventListener('click',()=>{
    const r=job.valuation||job.valuation_setup.saved,url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=job.ticker+'-valuation-'+r.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
}

function reviewedValuationView(p,r,setup){
  const pref=p.preferred,labels=valuationNames;
  let html=card(escape(job.ticker)+' 内在价值 · 已核实条款与过渡期',`<p>估值日 <strong>${escape(p.valuation_date)}</strong> · 参考收盘价 <strong>${fixed(p.market_price,2)} ${escape(setup.currency)}</strong>（${escape(p.market_price_date||'')}）。财务基准 ${escape(p.balance_date)}，预测至 ${p.forecast_start+p.years-1} 年。</p><p class="note">本页补充最新 SEC / 公司披露，不覆盖原研究归档。事实已核验，未来现金流仍是有依据的情景预测。每股价值是估值日现值，不是未来某日的目标价；下方金额以百万美元输入。</p>`);
  const bridgeIntro=html;html='';
  if(r){
    html+=`<section class="card valuation-overview">${valuationDateSummary(r)}${valuationScenarioCards(r)}<p class="note">已核实优先股条款 · 按所选转股比例计入稀释；未来现金流为情景预测。</p><button class="button valuation-export" id="valuation-download" type="button">导出估值</button></section>`;
    const bridgeOverview=html;html='';
    html+=card('已核实优先股 · 转股摊薄与剩余股息',`<p>${fixed(pref.count/1e6,2)} 百万股优先股，年股息 ${pct(pref.coupon)}，每股清算优先额 ${num(pref.liquidation_per_share)} 美元，${escape(pref.conversion_date)} 正常强制转换。每股优先股换 ${pref.min_ratio}–${pref.max_ratio} 股普通股，取决于转换前规定的 20 个交易日平均 VWAP：高于 171.5854 美元用 5.8280，低于 142.9797 美元用 6.9940，中间区间为 1000 ÷ VWAP。</p><p>主表按${pref.selected_ratio===pref.max_ratio?'最大':'所选'}转股比例 ${pref.selected_ratio} 计入 <strong>${fixed(r.scenarios.base.conversion_shares/1e6,4)} 百万新增普通股</strong>，合计 ${fixed(r.scenarios.base.effective_shares/1e6,4)} 百万股。优先股清算本金不再扣减。</p>`+table(['剩余支付日','现金股息（百万）','现值（百万）'],r.scenarios.base.dividend_schedule.map(d=>[d.date,fixed(d.amount/1e6,3),fixed(d.pv/1e6,3)]))+table(['情景','最大转股摊薄价格','最小转股摊薄价格'],Object.entries(r.scenarios).map(([k,v])=>[labels[k],fixed(v.conversion_price_range[0],2),fixed(v.conversion_price_range[1],2)]))+`<p class="note">股息按合同正常现金支付假设，尚未宣告的未来股息不标作已宣告。提前转换、基本面变更、反摊薄或股票支付股息需重新估值。</p>`);
    html+=`<details class="card"><summary>2026 过渡年度 · 已披露、估计与未来期间分开</summary>`+table(['情景','全年公司 FCF 预测','前三季度实绩','Q4 隐含 FCF','季末至估值日估计 FCF','估值日后剩余 FCFF','剩余 FCFF 现值'],Object.entries(r.scenarios).map(([k,v])=>[labels[k],...['full_year_company_fcf','ytd_company_fcf','q4_company_fcf','estimated_elapsed_fcf','remaining_fcff','remaining_pv'].map(f=>fixed(v.transition[f]/1e6,2))]))+`<p class="note">单位百万美元。${escape(r.scenarios.base.transition.note)} 10 月 15 日已付优先股股息 ${fixed(p.paid_preferred_dividends/1e6,2)} 百万美元从净现金滚动中扣除。债务归还同时减少现金和债务，不重复影响净现金。</p></details>`;
    const sn=r.sensitivity;
    html+=card('敏感性 · WACC 与永续增长率',`<div class="table-wrap"><table class="valuation-matrix"><thead><tr><th>WACC / g</th>${sn.growths.map(g=>`<th>${pct(g)}</th>`).join('')}</tr></thead><tbody>${sn.prices.map((row,i)=>`<tr><th>${pct(sn.waccs[i])}</th>${row.map((v,j)=>`<td class="${i===2&&j===2?'valuation-center':''}">${fixed(v,2)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="note">中间单元格为基准每股估值。WACC 为正常税盾下长期利率，显式期对尚未实现的税盾加回利差。优先股现金股息使用独立折现率。</p>`);
    const rv=r.reverse;
    html+=card('反向 DCF · 估值日参考股价隐含的现金流要求',rv.status==='solved'?`<p>要支持 ${fixed(rv.target_price,2)} 美元，未来公司 FCF 需要达到基准路径的 <strong>${fixed(rv.fcf_multiplier,2)} 倍</strong>。</p>`+table(['预测年度','隐含公司 FCF（亿美元）'],rv.implied_company_fcf.map((v,i)=>[p.forecast_start+i,fixed(v/1e8,2)]))+`<p class="note">${escape(rv.basis)} 反算股价 ${fixed(rv.reproduced_price,2)}。</p>`:`<p>${escape(rv.reason)}</p>`);
    html+=`<details class="card"><summary>逐年现金流与权益分配明细</summary>`;
    for(const [name,v] of Object.entries(r.scenarios))html+=`<h4>${labels[name]} · 百万美元</h4>`+table(['年度','公司 FCF','利息费用','利息收入','利息税率','员工股权成本','调整后 FCFF','现值'],v.rows.map(row=>[row.year,...['company_fcf','interest','interest_income'].map(k=>fixed(row[k]/1e6,2)),pct(row.interest_tax_rate),...['equity_comp','fcff','pv'].map(k=>fixed(row[k]/1e6,2))]))+`<p class="note">终值现值 ${fixed(v.terminal_pv/1e6,2)}，终值占比 ${pct(v.terminal_share)}；转换后权益池 ${fixed(v.equity_pool/1e6,2)}，其中现有普通股 ${fixed(v.equity_value/1e6,2)}、转换所得股份 ${fixed(v.converted_preferred_equity_value/1e6,2)}。</p>`;
    html+='</details>';
    html=bridgeOverview+valuationFold('优先股、敏感性与计算明细',html);
  }
  const bridgeResults=html;html='';
  html+=`<form id="valuation-form"><details class="card"><summary>编辑估值基准与转股参数</summary><div class="valuation-grid">`;
  for(const [key,label,scale] of [['cash','季末可用现金与投资（百万）',1e6],['debt','季末披露有息债务（百万）',1e6],['operating_cash','经营必需现金（百万）',1e6],['other_claims','其他权益要求，不含已转股本金（百万）',1e6],['shares','现有普通股（百万股）',1e6],['market_price','参考股价',1],['preferred.selected_ratio','采用的优先股转股比例',1]])html+=valuationInput(label,key,key==='preferred.selected_ratio'?pref.selected_ratio:p[key],scale);
  html+='</div></details>';
  for(const name of ['bear','base','bull']){
    const s=p.scenarios[name];html+=`<details class="card"><summary>编辑${labels[name]}现金流情景</summary><div class="table-wrap"><table><thead><tr><th>年度</th><th>公司 FCF</th><th>利息费用</th><th>利息收入</th><th>员工股权成本</th><th>利息税率 %</th></tr></thead><tbody>`;
    s.company_fcf.forEach((value,i)=>{html+=`<tr><td>${p.forecast_start+i}</td>`+['company_fcf','interest','interest_income','equity_comp','interest_tax_rate'].map(key=>`<td>${valuationInput(key+' '+(p.forecast_start+i),`scenarios.${name}.${key}.${i}`,s[key][i],key==='interest_tax_rate'?.01:1e6)}</td>`).join('')+'</tr>';});
    html+='</tbody></table></div><div class="valuation-grid">';
    for(const [key,label,scale] of [['fy_company_fcf','2026 全年公司 FCF（百万）',1e6],['stub_interest','Q4 利息费用（百万）',1e6],['stub_interest_income','Q4 利息收入（百万）',1e6],['stub_equity_comp','Q4 员工股权成本（百万）',1e6],['wacc','WACC %',.01],['terminal_growth','永续增长 %',.01],['terminal_normalization','终值前去除临时现金释放（百万）',1e6],['preferred_discount_rate','优先股股息折现率 %',.01]])html+=valuationInput(label,`scenarios.${name}.${key}`,s[key],scale);
    html+='</div></details>';
  }
  html+=`<div class="card"><label class="valuation-field">估值依据与情景解释<textarea id="valuation-rationale" rows="6" maxlength="6000">${escape(p.rationale)}</textarea></label><p class="note">每次保存新增版本；历史数据及前次估值保留。</p><button class="button" type="submit">计算并保存估值</button><p role="status" id="valuation-status"></p></div></form>`;
  html+=card('已核实来源与假设边界',table(['输入','数值','期间','性质'],(p.verified_inputs||[]).map(e=>[e.label,num(e.value),e.date,e.kind]))+`<p>${escape(p.discount_basis?.note||'')}</p><ol>${(p.sources||[]).map(source=>`<li><a href="${escape(source.url)}" target="_blank" rel="noopener">${escape(source.label)}</a> · ${escape(source.date)}</li>`).join('')}</ol><ul>${(r?.warnings||[]).map(w=>`<li>${escape(w)}</li>`).join('')}</ul>`);
  return bridgeResults+valuationFold('调整估值假设与来源',bridgeIntro+html,!r);
}

return Object.freeze({render:valuationView,bind:bindValuation,invalidate:id=>valuationDrafts.delete(id),
 comparison:valuationComparisonContext,status:automaticValuationStatus,dateChooser:valuationDateChooser,
 explanation:valuationModelExplanation,contingent:contingentClaimsView});
};
