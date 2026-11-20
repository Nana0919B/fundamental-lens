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
 const context=vm.createContext({document,window:{addEventListener(){},scrollTo(){}},fetch:fetchImpl,setInterval(){},setTimeout(){return 1},clearTimeout(){},requestAnimationFrame:fn=>fn(),console,Date,encodeURIComponent});
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
 console.log('Seven panels, escaped evidence, financial units, submission failure/recovery, live completion and selection races passed.');
}
interactionTests().catch(error=>{console.error(error);process.exitCode=1});
