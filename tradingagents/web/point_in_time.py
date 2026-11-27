"""Frozen historical evidence for the regular research graph and automatic DCF.

This is a retrospective reconstruction, never an assertion that an AI forecast
existed on the observation date. No contemporary profile, peers or news enter.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

from tradingagents.dataflows.point_in_time import validate_snapshot
from tradingagents.web.automatic_valuation import collect, digest, excerpts, save

VERSION = 'company-pit-1.0'
NOTICE = ('历史重建研究：今天按观察日前已公开文件生成，非当年留存预测。'
          '不使用事后新闻、今日公司资料或研究记忆；模型自身知识的事后偏差无法完全排除。'
          '财报披露次日起使用；参考价为观察日收盘价，不能用于当日收盘前成交。')



def statements(ticker, day):
    from tradingagents.dataflows.vendors import sec_edgar as sec
    cik = sec.cik_for(ticker)
    raw = sec._cached_json(sec._FACTS_URL.format(cik=cik), f'CIK{cik}.json')
    facts = raw.get('facts', {}).get('us-gaap', {})
    # Keep the original filing dates; never consume today's restated column.
    ledger, result = [], {}
    aliases = {'Revenue': 'Total Revenue', 'Cash and Equivalents': 'Cash And Cash Equivalents',
               'Cost of Revenue': 'Cost Of Revenue'}
    for freq, spans in [('annual', ((300, 400),)), ('quarterly', ((60, 115),)),
                        ('year_to_date', ((150, 200), (240, 290)))]:
        result[freq] = {}
        for kind, rows in sec._STATEMENTS.items():
            series = []
            for label, tags in rows:
                chosen = {}
                for tag in tags:
                    units = facts.get(tag, {}).get('units', {})
                    values = units.get('USD/shares' if label == 'Diluted EPS' else 'USD', [])
                    candidates = {}
                    for fact in values:
                        if (fact.get('filed', '9999') >= day or fact['end'] > day
                                or fact.get('form') not in ('10-K', '10-Q', '10-K/A', '10-Q/A')):
                            continue
                        if freq == 'annual' and not fact['form'].startswith('10-K'):
                            continue
                        if 'start' in fact:
                            days = (date.fromisoformat(fact['end']) - date.fromisoformat(fact['start'])).days
                            if not any(lo <= days <= hi for lo, hi in spans):
                                continue
                        elif freq == 'year_to_date':
                            continue
                        key = fact['end'] + (f" ({fact['start']})" if freq == 'year_to_date' else '')
                        if key not in candidates or candidates[key]['filed'] < fact['filed']:
                            candidates[key] = {**fact, 'tag': tag}
                    for key, value in candidates.items():
                        chosen.setdefault(key, value)
                series.append((aliases.get(label, label), chosen))
            periods = sorted({p for _, values in series for p in values}, reverse=True)[:6]
            table = {'periods': periods, 'rows': []}
            for label, values in series:
                table['rows'].append({'name': label, 'values': [
                    (-abs(values[p]['val']) if label == 'Capital Expenditure' else values[p]['val'])
                    if p in values else None for p in periods]})
                ledger.extend(values[p] for p in periods if p in values)
            result[freq][{'income_statement': 'income', 'balance_sheet': 'balance'}.get(kind, kind)] = table
    return result, ledger


def historical_quote(ticker, day):
    import yfinance as yf

    from tradingagents.dataflows.vendors.yahoo.transport import session_options
    from tradingagents.web.valuation import numeric
    as_of = date.fromisoformat(day)
    instrument = yf.Ticker(ticker, **session_options())
    # Yahoo Close includes later split adjustments even with auto_adjust=False.
    # Future split factors are used ONLY to restore historical units, never sent
    # to the research agents as business/forecast evidence.
    history = instrument.history(start=(as_of - timedelta(days=900)).isoformat(),
                                 end=(date.today() + timedelta(days=1)).isoformat(),
                                 auto_adjust=False, actions=True, raise_errors=True)
    available = history[[idx.date().isoformat() <= day for idx in history.index]]
    if available.empty:
        raise ValueError('观察日前没有可用收盘价')
    quote_day = available.index[-1].date().isoformat()
    if (as_of - date.fromisoformat(quote_day)).days > 14:
        raise ValueError('观察日前没有足够近期的有效收盘价')
    factor = 1.0
    for idx, row in history.iterrows():
        if idx.date().isoformat() > quote_day and row.get('Stock Splits', 0) > 0:
            factor *= float(row['Stock Splits'])
    price = numeric(float(available.iloc[-1]['Close']) * factor, '历史收盘价', .000001, 1e9)
    currency = instrument.history_metadata.get('currency')
    if not currency:
        raise ValueError('无法核实历史报价币种')
    known_splits = [{'date': idx.date().isoformat(), 'ratio': float(row['Stock Splits'])}
                    for idx, row in history.iterrows()
                    if idx.date().isoformat() <= day and row.get('Stock Splits', 0) > 0]
    return {'price': price, 'date': quote_day, 'currency': currency, 'known_splits': known_splits,
            'source': 'Yahoo historical close, restored to observation-date share units',
            'retrieved_at': datetime.now().astimezone().isoformat()}


def build_snapshot(job, folder):
    if job['ticker'].endswith(('.HK', '.SS', '.SZ')):
        raise ValueError('该市场尚未接入完整历史原始披露，不能用行业默认参数代替自动研究')
    documents, failures = collect(job, folder)
    if not documents:
        raise ValueError('无法取得观察日前原始披露：' + '；'.join(failures))
    quote = historical_quote(job['ticker'], job['date'])
    tables, ledger = statements(job['ticker'], job['date'])
    # Bounded text excerpts from downloaded originals, with source IDs and dates.
    visible = []
    for d in documents:
        passages = [excerpts(d, '', 0)]
        for query in ('Management', 'segment', 'revenue', 'operating income', 'cash flow', 'risk factors'):
            passages.extend(excerpts(d, query)[:3])
        visible.append({**{k: v for k, v in d.items() if k != 'text'}, 'excerpts': passages, 'text_digest': digest(d['text'])})
    company = {'symbol': job['ticker'], 'source': 'SEC point-in-time original filings',
               'retrieved_at': datetime.now().astimezone().isoformat(), 'period_note': NOTICE,
               'profile': {'longName': job['ticker'], 'currency': quote['currency'],
                           'financialCurrency': 'USD', 'currentPrice': quote['price']},
               'statements': tables, 'filings': {'documents': visible, 'note': '原文摘录已归档；非实时财务'},
               'errors': failures}
    snapshot = {'id': job['id'], 'schema_version': 2, 'as_of_date': job['date'],
                'company': company, 'peers': [], 'point_in_time': {
                    'version': VERSION, 'evidence_digest': digest(visible), 'facts': ledger,
                    'quote': quote, 'notice': NOTICE}}
    from tradingagents.dataflows.research_analytics import enrich_snapshot
    enrich_snapshot(snapshot)
    validate_snapshot(snapshot, job['ticker'], job['date'])
    save(folder / 'snapshot.json', snapshot)
    return snapshot


def archived_documents(job):
    snapshot = job['research_snapshot']
    validate_snapshot(snapshot, job['ticker'], job['date'])
    # The immutable full originals are carried in a separate server-owned path.
    import json
    from pathlib import Path
    folder = Path(job['pit_source_folder'])
    documents = [json.loads(p.read_text()) for p in sorted(folder.glob('S*.json'))]
    expected = {d['id']: d['sha256'] for d in snapshot['company']['filings']['documents']}
    if {d['id']: d['sha256'] for d in documents} != expected:
        raise ValueError('历史研究与自动估值原文不一致')
    text_hashes = {d['id']: d['text_digest'] for d in snapshot['company']['filings']['documents']}
    for doc in documents:
        if digest(doc['text']) != text_hashes[doc['id']]:
            raise ValueError('历史原文文本校验失败')
        import hashlib
        if hashlib.sha256((folder / (doc['id'] + '.html')).read_bytes()).hexdigest() != doc['sha256']:
            raise ValueError('历史原文归档校验失败')
    return deepcopy(documents), list(snapshot['company'].get('errors', []))
