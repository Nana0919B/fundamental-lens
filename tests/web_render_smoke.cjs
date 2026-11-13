// Render contracts in Node: no browser or provider/model calls.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();
function node(){return {value:'AAPL',hidden:false,innerHTML:'',textContent:'',addEventListener(){},querySelectorAll(){return []}};}
const context=vm.createContext({document:{querySelector:s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)}},fetch:()=>new Promise(()=>{}),setInterval(){},console,Date,encodeURIComponent});
vm.runInContext(fs.readFileSync('tradingagents/web/static/app.js','utf8'),context);
const fixture={id:'version-one',ticker:'AAPL',date:'2026-11-13',status:'completed',phase:'分析完成',
 report_html:{operating_report:'<p>独立经营结论</p>',metrics_report:'<p>独立指标结论</p>',company_brief:'<p>公司简报</p>'},
 versions:[{id:'version-one',as_of_date:'2026-11-13',retrieved_at:'2026-11-13T10:00:00Z',has_report:1}],
 data:{symbol:'AAPL',profile:{longName:'Apple',financialCurrency:'USD',companyOfficers:[{name:'Officer',title:'CEO'}]},
 statements:{annual:{income:{periods:['2025-12-31'],rows:[{name:'Total Revenue',values:[200]}]}}},
 source:'Yahoo',retrieved_at:'2026-11-13T10:00:00Z',filings:{documents:[{form:'10-K',period:'2025-12-31',filed:'2026-01-01',url:'https://www.sec.gov/report.htm'}],note:'原文附注'}},
 research_snapshot:{id:'version-one',evidence:[{id:'profile.longName',field:'Company',period:'current',value:'<script>alert(1)</script>'}],
 analytics:{formula_version:'1.0',definitions:{net_margin:{label:'净利率',unit:'ratio',formula:'net_income / revenue'}},history:[{period:'2025-12-31',values:{net_margin:0.1}}],ttm:{values:{revenue:180}},growth:[],limitations:[]},
 comparison:{rows:[],distributions:[],basis:'所选样本',currency_policy:'不混合币种'}}};
context.fixture=fixture;
const expected=['研究版本','管理层与股权概览','独立经营结论','原始报告','统一公式','所选样本'];
for(let i=0;i<6;i++){
 vm.runInContext(`job=fixture;tab=${i};draw()`,context);
 const html=nodes.get('#content').innerHTML;
 assert(html.includes(expected[i]),`Missing content for tab ${i}`);
 assert(!html.includes('<script>'),'Evidence values must be escaped');
 assert(html.includes('&lt;script&gt;'),'Evidence ledger is present on every tab');
}
console.log('All six Web panels render with archived data, evidence, formulas and source links.');
