// Render contracts in Node: no browser or provider/model calls.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
function harness(fetchImpl=()=>new Promise(()=>{})) {
 const nodes=new Map(),events={};
 function node(){
  const classes=new Set();
  return {value:'AAPL',hidden:false,innerHTML:'',textContent:'',open:false,scrollLeft:0,scrollWidth:800,clientWidth:400,
   classList:{add(c){classes.add(c)},remove(c){classes.delete(c)},contains(c){return classes.has(c)},toggle(c,on){const enabled=on===undefined?!classes.has(c):on;enabled?classes.add(c):classes.delete(c);return enabled}},
   addEventListener(name,fn){this['on'+name]=fn},querySelectorAll(){return []},querySelector(){return null},
   setAttribute(k,v){this[k]=v},getAttribute(k){return this[k]},focus(){},showModal(){this.open=true},close(){this.open=false},
   scrollBy({left}){this.scrollLeft+=left},click(){this.onclick?.({currentTarget:this})}};
 }
 const document={body:node(),addEventListener(name,fn){events[name]=fn},createElement:node,
  querySelector(s){if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)},getElementById(id){return this.querySelector('#'+id)}};
 const context=vm.createContext({document,window:{addEventListener(){},scrollTo(){}},fetch:fetchImpl,setInterval(){},setTimeout(){return 1},clearTimeout(){},requestAnimationFrame:fn=>fn(),console,Date,encodeURIComponent,URLSearchParams,structuredClone});
 vm.runInContext(fs.readFileSync('tradingagents/web/static/valuation.js','utf8'),context);
 vm.runInContext(fs.readFileSync('tradingagents/web/static/app.js','utf8'),context);
 return {context,nodes,events};
}
const {context,nodes}=harness();
const fixture={id:'version-one',ticker:'AAPL',date:'2026-11-13',status:'completed',phase:'分析完成',
 report_html:{operating_report:'<p>独立经营结论</p>',metrics_report:'<p>独立指标结论</p>',company_brief:'<p>公司简报</p>',fundamentals_report:'<p>基本面报告</p>',final_trade_decision:'<p>综合结论</p>',news_report:'<p>新闻事件与风险</p>'},
 versions:[{id:'version-one',as_of_date:'2026-11-13',retrieved_at:'2026-11-13T10:00:00Z',has_report:1}],
 data:{symbol:'AAPL',profile:{longName:'Apple',financialCurrency:'USD',companyOfficers:[{name:'Officer',title:'CEO'}]},
 statements:{annual:{income:{periods:['2025-12-31'],rows:[{name:'Total Revenue',values:[200]}]}}},
 source:'Yahoo',retrieved_at:'2026-11-13T10:00:00Z',filings:{documents:[{form:'10-K',period:'2025-12-31',filed:'2026-01-01',url:'https://www.sec.gov/report.htm'}],note:'原文附注'}},
 research_snapshot:{id:'version-one',evidence:[{id:'profile.longName',field:'Company',period:'current',value:'<script>alert(1)</script>'}],
 analytics:{formula_version:'1.0',definitions:{net_margin:{label:'净利率',unit:'ratio',formula:'net_income / revenue'}},history:[{period:'2025-12-31',values:{net_margin:0.1}}],ttm:{values:{revenue:180}},growth:[],limitations:[]},
 comparison:{rows:[],distributions:[],basis:'所选样本',currency_policy:'不混合币种'}}};
fixture.display_evidence=[{id:'profile.longName',label:'公司名称',period:'当前资料',display_value:'<script>alert(1)</script>',explanation:'识别公司',source:'Yahoo'}];
context.fixture=fixture;
const expected=['研究版本','管理层与股权概览','独立经营结论','原始报告','统一公式','新闻事件与风险','所选样本'];
for(let i=0;i<7;i++){
 vm.runInContext(`job=fixture;tab=${i};draw()`,context);
 const html=nodes.get('#content').innerHTML;
 assert(html.includes(expected[i]),`Missing content for tab ${i}`);
 assert(!html.includes('<script>'),'Evidence values must be escaped');
 assert(html.includes('&lt;script&gt;'),'Evidence table is escaped on every tab');
 const visible=html.replace(/<[^>]*>/g,'');
 assert(!visible.includes('version-one'),'Trace IDs must not be visible');
 assert(!visible.includes('证据 ID'),'Technical evidence identifiers are hidden');
 if(i===0){assert(html.indexOf('基本面研究')<html.indexOf('综合判断'));assert(html.indexOf('综合判断')<html.indexOf('公司研究简报'));for(const name of ['fundamentals','balance_sheet','cashflow','income_statement','insider_transactions'])assert(html.includes(name));}
 if(i===3)assert(html.indexOf('原始报告')>html.indexOf('财务报表'),'Original disclosures must be last');
}
fixture.macro_context={reports:['FRED observation']};
vm.runInContext('job=fixture;tab=5;draw()',context);
assert(nodes.get('#content').innerHTML.includes('尚未参与原新闻结论与综合判断'));
assert(nodes.get('#content').innerHTML.includes('id="reanalyze-macro"'));
fixture.macro_consumed=true;
vm.runInContext('draw()',context);
assert(nodes.get('#content').innerHTML.includes('已将 FRED 数据传给原新闻分析师'));
assert(!nodes.get('#content').innerHTML.includes('尚未参与原新闻结论与综合判断'));
async function interactionTests(){
 const requests=[];let accepted=false,fail=true,completed=false;
 const response=(data,ok=true)=>({ok,json:async()=>data});
 const state=()=>({...fixture,id:'created',status:completed?'completed':'running',phase:completed?'分析完成':'分析中',report_html:completed?fixture.report_html:{}});
 const h=harness(async(url,options)=>{
  requests.push({url,options});
  if(url==='/api/jobs'&&options?.method==='POST'){
   if(fail)return response({error:'模型服务不可用'},false);
   accepted=true;return response({id:'created',status:'queued'});
  }
  if(url==='/api/jobs')return response({jobs:accepted?[state()]:[]});
  if(url==='/api/jobs/created')return response(state());
  throw Error('Unexpected request: '+url);
 });
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 await flush();
 vm.runInContext('openResearch()',h.context);
 h.context.document.querySelector('#ticker').value=' AAPL ';h.context.document.querySelector('#market').value='us';h.context.document.querySelector('#peers').value='MSFT, GOOGL AMZN，NVDA';
 await vm.runInContext('submitResearch({preventDefault(){}})',h.context);
 assert(h.nodes.get('#research-dialog').open,'Failed submission keeps the form open');
 assert(h.nodes.get('#form-error').textContent.includes('模型服务不可用'));
 assert.equal(h.nodes.get('#ticker').value,' AAPL ');assert.equal(h.nodes.get('#submit').disabled,false);
 fail=false;await vm.runInContext('submitResearch({preventDefault(){}})',h.context);
 const payload=JSON.parse(requests.filter(r=>r.options?.method==='POST').at(-1).options.body);
 assert.deepEqual(payload,{ticker:'AAPL',market:'us',peers:['MSFT','GOOGL','AMZN','NVDA']});
 assert.equal(h.nodes.get('#research-dialog').open,false);assert.equal(h.nodes.get('#progress').hidden,false);
 completed=true;await vm.runInContext('refresh()',h.context);
 assert.equal(h.nodes.get('#progress').hidden,true);assert(h.nodes.get('#research-status').innerHTML.includes('已完成研究'));
 const pending=new Map();
 const race=harness(url=>url==='/api/jobs'?Promise.resolve(response({jobs:[]})):new Promise(resolve=>pending.set(url,resolve)));
 await flush();
 const first=vm.runInContext('select("older")',race.context),second=vm.runInContext('select("newer")',race.context);
 pending.get('/api/jobs/newer')(response({...fixture,id:'newer',ticker:'NEW'}));await second;
 pending.get('/api/jobs/older')(response({...fixture,id:'older',ticker:'OLD'}));await first;
 assert.equal(vm.runInContext('selected',race.context),'newer');
 assert.equal(vm.runInContext('job.id',race.context),'newer','A late response cannot replace the current selection');
 assert.equal(vm.runInContext('financialValue("Diluted EPS",4.5)',context),'4.5');
 assert.equal(vm.runInContext('financialValue("Total Revenue",123456789)',context),'123.5');
 assert.equal(vm.runInContext('financialValue("Tax Rate For Calcs",0.21)',context),'21%');
 assert.equal(vm.runInContext('financialValue("Total Revenue",null)',context),'—');
 context.sourceRows=[{name:'Tax Effect Of Unusual Items',values:[1]},{name:'Total Revenue',values:[100]},{name:'Net Income',values:[20]}];
 const sorted=vm.runInContext('prioritizedFinancialRows(sourceRows,"income")',context);
 assert.deepEqual(Array.from(sorted,row=>row.name),['Total Revenue','Net Income','Tax Effect Of Unusual Items']);
 assert.equal(context.sourceRows[0].name,'Tax Effect Of Unusual Items','Presentation ordering must not mutate archived row indices');
 context.refs=[{id:'profile.city',field:'city'},{id:'annual.income.1.0',field:'Total Revenue'}];
 vm.runInContext('tab=3;frequency="annual";statement="income"',context);
 assert.equal(vm.runInContext('prioritizedEvidence(refs)[0].id',context),'annual.income.1.0');
 assert.equal(context.refs[0].id,'profile.city');
 context.supplier={revenueGrowth:.117,mostRecentQuarter:1790380800};
 const growth=vm.runInContext('supplierGrowthNote(supplier)',context);
 assert(growth.includes('11.7%')&&growth.includes('2026-09-26')&&growth.includes('非 TTM'));
 // Chart presentation must preserve signed values and must never turn missing data into zero.
 context.chartPoints=[{period:'2024-06-30',value:-10},{period:'2025-06-30',value:null},{period:'2026-06-30',value:0}];
 const chart=vm.runInContext('seriesChart(chartPoints,{unit:"亿 USD",compact:true})',context);
 assert(chart.includes('-10 亿 USD')&&chart.includes('0 亿 USD'));
 assert(!chart.includes('2025-06-30:')&&!chart.includes('NaN')&&!chart.includes('Infinity'));
 assert.equal((chart.match(/<circle /g)||[]).length,2);
 assert.equal((chart.match(/<polyline /g)||[]).length,2,'A missing period must break the trend line');
 assert(chart.includes('>2025<')&&chart.includes('缺失值不绘制'));
 assert.equal(vm.runInContext('readableSummary("snapshot未提供 operating_margin")',context),'本次资料未提供 经营利润率（比率原值）');
 assert.equal(vm.runInContext('readableFormula("net_income / revenue")',context),'净利润 / 营业收入');
 assert(vm.runInContext('seriesChart([{period:"2026-06-30",value:null}])',context).includes('没有可用的历史序列'));
 assert(vm.runInContext('seriesChart([{period:"2026-06-30",value:4.95}],{digits:2})',context).includes('4.95'));
 assert.equal(vm.runInContext('metricDisplay(.21,{unit:"ratio"})',context),'21%');
 assert.equal(vm.runInContext('metricDisplay(null,{unit:"ratio"})',context),'—');
 fixture.macro_context={reports:['## FRED：美国 CPI（CPIAUCSL）\n- 单位：指数，1982–1984=100；查询期间：2026。\n**最新读数：337.476（观察日期：2026-10-01）**']};
 const macro=vm.runInContext('macroReadings()',context);
 assert(macro.includes('337.476')&&macro.includes('指数，1982–1984=100')&&macro.includes('2026-10-01'));
 assert(!macro.includes('337.476%'),'A price index must keep its actual unit');
 const archived=JSON.stringify(fixture.data.statements);
 vm.runInContext('job=fixture;financialRow="Total Revenue";financialView()',context);
 assert.equal(JSON.stringify(fixture.data.statements),archived,'Chart selection must not mutate statement data');
 const fakeRows=['营业收入 / Total Revenue','净利润 / Net Income'].map(text=>({hidden:false,querySelector(){return {textContent:text}}}));
 nodes.get('#content').querySelectorAll=()=>fakeRows;
 vm.runInContext('financialQuery="营业收入";filterFinancialRows()',context);
 assert.equal(fakeRows[0].hidden,false);assert.equal(fakeRows[1].hidden,true);
 vm.runInContext('financialQuery="no matching field";filterFinancialRows()',context);
 assert.equal(nodes.get('#financial-no-results').hidden,false);
 vm.runInContext('financialQuery="";filterFinancialRows()',context);
 assert(fakeRows.every(r=>!r.hidden));assert.equal(nodes.get('#financial-no-results').hidden,true);
 nodes.get('#content').querySelectorAll=()=>[];
 console.log('Seven panels, escaped evidence, financial units, submission failure/recovery, live completion and selection races passed.');
}

// A-share environment is the exact archived dataset, with safe values and honest consumption status.
context.environmentFixture={...fixture,ticker:'600519.SS',market_environment_consumed:true,
 market_environment:{retrieved_at:'2026-11-20T17:00:00+08:00',cutoff_date:'2026-11-20',
 coverage:{status:'partial',reason:'',warnings:['行业主线暂无数据']},membership_note:'当前快照回看历史',
 latest:{date:'2026-11-20',state:'lean_strong',score:63,phase:'ignite',seal_rate:.68,promo_rate:null,index_pct:.012},
 history:[{date:'2026-11-20',state:'lean_strong',score:63,phase:'ignite'}],
 mainlines:{concept:[{date:'2026-11-20',rank:1,member:'<script>bad</script>',score:80}],industry:[]}}};
vm.runInContext('job=environmentFixture;tab=7;draw()',context);
let environmentHTML=nodes.get('#content').innerHTML;
assert(environmentHTML.includes('已将本次归档数据传入研究讨论'));
assert(environmentHTML.includes('偏强')&&environmentHTML.includes('启动')&&environmentHTML.includes('63 / 100'));
assert(environmentHTML.includes('68%')&&environmentHTML.includes('1.2%'));
assert(environmentHTML.includes('&lt;script&gt;bad&lt;/script&gt;')&&!environmentHTML.includes('<script>'));
assert(environmentHTML.includes('当前快照回看历史'));
vm.runInContext('job.market_environment.coverage.status="stale";draw()',context);
assert(nodes.get('#content').innerHTML.includes('不作为当前市场判断依据'));
vm.runInContext('job.market_environment.latest=null;job.market_environment.coverage.status="unavailable";draw()',context);
assert(nodes.get('#content').innerHTML.includes('资料接入'));
vm.runInContext('job.market_environment_consumed=false;draw()',context);
assert(nodes.get('#content').innerHTML.includes('未完成市场环境传递核验'));
vm.runInContext('delete job.market_environment;draw()',context);
assert(nodes.get('#content').innerHTML.includes('历史报告不会自动补取新数据'));
vm.runInContext('job.ticker="AAPL";draw()',context);
assert(nodes.get('#content').innerHTML.includes('历史报告不会自动补取新数据'));
vm.runInContext('job.ticker="BTC-USD";draw()',context);
assert(nodes.get('#content').innerHTML.includes('当前证券类型尚无对应的市场环境数据源'));

// Futu US/HK view uses its own metrics, not A-share sentiment stages.
context.globalEnvironment={market:'US',source:'Futu OpenAPI',market_timezone:'America/New_York',currency:'USD',primary_symbol:'US.SPY',retrieved_at:'2026-11-23T12:00:00+08:00',cutoff_date:'2026-11-22',coverage:{status:'available',warnings:[]},membership_note:'固定参考样本，不是全市场广度',reference_universe:[{symbol:'US.SPY'}],
 latest:{date:'2026-11-20',state:'lean_strong',score:66,realized_vol_20d:.18,return_1d:.01,return_20d:.04,drawdown_60d:-.02,breadth_ratio:.75,breadth_sample_size:3,breadth_expected_size:4,trend_score:60,momentum_score:70,breadth_score:75,volatility_score:64},
 references:[{symbol:'US.SPY',name:'标普 <500> ETF',date:'2026-11-20',close:600,ma20:590,ma60:580,return_1d:.01,return_20d:.04}],history:[]};
vm.runInContext('job={...fixture,ticker:"AAPL",market_environment:globalEnvironment,market_environment_consumed:true};tab=7;draw()',context);
let globalHTML=nodes.get('#content').innerHTML;
assert(globalHTML.includes('美股市场趋势')&&globalHTML.includes('18%')&&globalHTML.includes('66 / 100'));
assert(globalHTML.includes('标普 &lt;500&gt; ETF'));
assert(!globalHTML.includes('<th>涨停家数</th>')&&!globalHTML.includes('<small>情绪阶段</small>'));
vm.runInContext('job.market_environment.latest.score=null;job.market_environment.latest.breadth_ratio=null;job.market_environment.market="HK";job.market_environment.market_timezone="Asia/Hong_Kong";job.ticker="0700.HK";draw()',context);
assert(nodes.get('#content').innerHTML.includes('港股市场趋势'));
assert(!nodes.get('#content').innerHTML.includes('null / 100'));
vm.runInContext('job.market_environment.latest=null;job.market_environment.coverage={status:"unavailable",warnings:[],reason:"请配置 FUTU_API_KEY"};draw()',context);
assert(nodes.get('#content').innerHTML.includes('富途官方 AppKey'));
vm.runInContext('job.market_environment_update={...globalEnvironment,refresh_note:"本次补算，原结论未重新生成。",latest:null,coverage:{status:"unavailable",reason:"测试",warnings:[]}};draw()',context);
assert(nodes.get('#content').innerHTML.includes('已按原研究日期补算市场环境'));
assert(nodes.get('#content').innerHTML.includes('原研究、交易计划和风险结论未重新生成'));
assert(!nodes.get('#content').innerHTML.includes('已将本次归档数据传入研究讨论'));
// Market charts preserve missing periods, signed returns and immutable archive values.
context.chartEnvironment={market:'US',latest:{date:'2026-11-03'},history:[{date:'2026-11-01',score:0},{date:'2026-11-02',score:null},{date:'2026-11-03',score:100}],references:[{name:'Down <ETF>',symbol:'US.DOWN',date:'2026-11-03',return_20d:-.02},{name:'Up ETF',symbol:'US.UP',date:'2026-11-03',return_20d:.04},{name:'Missing',symbol:'US.NA',date:'2026-11-03',return_20d:null},{name:'Wrong date',symbol:'US.OLD',date:'2026-11-02',return_20d:.99}]};
const chartArchive=JSON.stringify(context.chartEnvironment);
const environmentTrend=vm.runInContext('environmentMetric="score";environmentRange=30;environmentTimeline(chartEnvironment)',context);
assert.equal((environmentTrend.match(/class="env-trend-line"/g)||[]).length,2,'Missing days break the score line');
assert.equal((environmentTrend.match(/data-env-point=/g)||[]).length,2,'Zero is valid; null is not plotted');
assert(environmentTrend.includes('选择趋势指标')&&environmentTrend.includes('选择趋势范围'));
const returnBars=vm.runInContext('environmentReferenceBars(chartEnvironment)',context);
assert(returnBars.includes('-2%')&&returnBars.includes('+4%')&&returnBars.includes('env-zero-axis'));
assert(returnBars.includes('Down &lt;ETF&gt;')&&!returnBars.includes('Wrong date'));
assert(!returnBars.includes('NaN')&&!returnBars.includes('Infinity'));
assert.equal(JSON.stringify(context.chartEnvironment),chartArchive,'Charts must not mutate research evidence');
vm.runInContext('environmentMetric="score";environmentRange=30',context);
vm.runInContext("job=fixture;tab=5",context);
// A saved valuation date must win over the older research archive and form drafts.
context.dateResult={inputs:{valuation_date:'2026-11-26',market_price_date:'2026-11-25T21:00:02+00:00',market_price:164.53}};
context.dateStudy={date:'2026-11-24'};
let comparison=vm.runInContext('getValuationPanel().comparison(dateResult,dateStudy)',context);
assert.equal(comparison.date,'2026-11-26');assert.equal(comparison.price,164.53);
assert.equal(comparison.quoteDate,'2026-11-25T21:00:02+00:00');
context.dateResult={inputs:{market_price:135.98},historical_context:{as_of_date:'2020-03-23'}};
comparison=vm.runInContext('getValuationPanel().comparison(dateResult,dateStudy)',context);
assert.equal(comparison.date,'2020-03-23');assert.equal(comparison.quoteDate,'2020-03-23');
context.dateResult={inputs:{market_price:100}};
comparison=vm.runInContext('getValuationPanel().comparison(dateResult,dateStudy)',context);
assert.equal(comparison.quoteDate,null,'Do not invent an historical quote date from the study date');
interactionTests().catch(error=>{console.error(error);process.exitCode=1});

const autoContext=harness().context;
// Automated estimates must not reuse industry-policy explanations or historical dates.
autoContext.autoStudy={...fixture,automatic_valuation:{status:'running',phase:'核实附注'},valuation_setup:{missing:['待核实'],inputs:{}}};
assert(!vm.runInContext('job=autoStudy;valuationView()',autoContext).includes('id="valuation-form"')); // unresolved evidence cannot become an editable default DCF
assert(vm.runInContext('getValuationPanel().status()',autoContext).includes('正在处理'));
autoContext.autoResult={inputs:{automatic_research:{},scenarios:{base:{growth:[.1]},bull:{growth:[.2]}}},policy_audit:{}};
assert(vm.runInContext('getValuationPanel().dateChooser(autoResult)',autoContext).includes('切换日期将运行公司研究与自动估值'));
assert.strictEqual(vm.runInContext("job={ticker:'MSFT'};getValuationPanel().explanation(autoResult)",autoContext),'');
autoContext.contingentResult={currency:'USD',inputs:{automatic_research:{contingent_claims:[{label:'Guarantee <test>',maximum_exposure:200,trigger:'Conditional default',treatment:'Incremental net loss only',basis:'Not probability',source_id:'S1',quote:'Original evidence <script>',stress:[{loss_fraction:.25,per_share_reduction:5}]}]}},scenarios:{bear:{price:70},base:{price:100},bull:{price:140}}};
const contingentBefore=JSON.stringify(autoContext.contingentResult);
const contingentHTML=vm.runInContext('getValuationPanel().contingent(contingentResult)',autoContext);
assert(contingentHTML.includes('条件估值')&&contingentHTML.includes('不是发生概率'));
assert(contingentHTML.includes('95.00')&&contingentHTML.includes('65.00')&&contingentHTML.includes('135.00'));
assert(contingentHTML.includes('&lt;script&gt;')&&!contingentHTML.includes('<script>'));
assert.equal(JSON.stringify(autoContext.contingentResult),contingentBefore);

// Results are the primary content; editing and technical explanations stay opt-in.
const compactInputs={method:'operating_fcff',assumption_policy:'history-sector-2.0',years:5,forecast_start:2021,revenue:100,cash:10,debt:5,shares:10,operating_cash:1,other_claims:0,dilution:1,market_price:20,rationale:'Company forecast assumptions',scenarios:{}};
for(const name of ['bear','base','bull'])compactInputs.scenarios[name]={growth:[.1,.05],margins:[.2,.2],da_ratio:.03,capex_ratio:.04,nwc_ratio:.1,tax_rate:.25,wacc:.1,terminal_growth:.02};
const compactResult={inputs:compactInputs,currency:'USD',version:'test',input_digest:'test-digest',scenarios:Object.fromEntries(['bear','base','bull'].map((name,i)=>[name,{price:10+i*5,upside:-.5+i*.25,equity_value:100+i*50,enterprise_value:95+i*50,rows:[],terminal_fcff:10,terminal_pv:70,terminal_share:.7}])),sensitivity:{growths:[.02],waccs:[.1],prices:[[15]]},reverse:{status:'no_solution',reason:'Not solved within range'},warnings:[]};
autoContext.compactJob={...fixture,valuation:compactResult,valuation_setup:{inputs:compactInputs,saved:compactResult,missing:[],currency:'USD',evidence:{},warnings:[]}};
const compactHTML=vm.runInContext('job=compactJob;valuationView()',autoContext);
assert.equal((compactHTML.match(/<article class="valuation-price /g)||[]).length,3);
assert(compactHTML.indexOf('valuation-price-grid')<compactHTML.indexOf('id="valuation-form"'));
assert(/<details class="card valuation-fold" ><summary>调整估值假设/.test(compactHTML));
assert(!vm.runInContext('getValuationPanel().dateChooser(autoResult)',autoContext).includes('href="/backtest'));
assert(compactHTML.includes('观察日期')&&compactHTML.includes('更新估值'));

// An unavailable/broken valuation module cannot stop the original research tabs.
const isolation=harness().context;
isolation.fixture=structuredClone(fixture);
vm.runInContext('window.createValuationPanel=undefined;valuationPanel=undefined;job=fixture;tab=1;draw()',isolation);
assert(vm.runInContext('document.querySelector("#content").innerHTML.length>0',isolation));
vm.runInContext('console={...console,error(){}};tab=8;draw()',isolation);
assert(vm.runInContext('document.querySelector("#content").innerHTML.includes("内在价值暂不可用")',isolation));
vm.runInContext('tab=2;draw()',isolation);
assert(!vm.runInContext('document.querySelector("#content").innerHTML.includes("内在价值暂不可用")',isolation));
// Valuation progress never replaces controls on completed company-analysis tabs.
isolation.fixture.automatic_valuation={status:'running',phase:'valuation-only-progress'};
vm.runInContext('tab=1;draw()',isolation);
assert(!vm.runInContext('document.querySelector("#content").innerHTML.includes("valuation-only-progress")',isolation));
assert.notEqual(vm.runInContext('document.querySelector("#progress").textContent',isolation),'valuation-only-progress');
// Date comparison must preserve each date/quote and exclude edited/manual results.
{
 const h=harness();
 const p={market_price:100,market_price_date:'2022-12-29',automatic_research:{plan_digest:'test'},scenarios:{base:{growth:[.1,.03],margins:[.3,.32],wacc:.09}}};
 h.context.comparisonStudies=[
  {id:'historical',date:'2022-12-30',automatic_valuation:{status:'completed',prepared:{inputs:structuredClone(p)}},valuation:{currency:'USD',input_digest:'x',inputs:structuredClone(p),scenarios:{bear:{price:80},base:{price:120,upside:.2},bull:{price:160}}}},
  {id:'pending',date:'2024-12-31',automatic_valuation:{status:'running'},valuation_reference_quote:{date:'2024-12-31',currency:'USD',price:88.88}}
 ];
 let html=vm.runInContext('getValuationPanel().datedComparison(comparisonStudies)',h.context);
 assert(html.includes('2022-12-29')&&html.includes('120.00')&&html.includes('核实中')&&html.includes('88.88'));
 h.context.comparisonStudies[0].valuation.inputs.market_price=999;
 html=vm.runInContext('getValuationPanel().datedComparison(comparisonStudies)',h.context);
 assert(!html.includes('120.00')&&!html.includes('999.00'));
}
