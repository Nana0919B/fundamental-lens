from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.dataflows.vendors import sec_edgar as sec
from tradingagents.web import point_in_time as pit, server


def test_archive_index_and_same_day_excluded(monkeypatch):
    calls = []
    def fetch(url, name):
        calls.append(name)
        if name.startswith('submissions-'):
            return {'filings': {'recent': {'form': ['10-K'], 'filingDate': ['2026-01-01']},
                                'files': [{'name': 'CIK0000000001-submissions-001.json', 'filingFrom': '2010-01-01'}]}}
        return {'form': ['10-Q', '10-K', '10-K/A'], 'filingDate': ['2020-03-20', '2019-08-01', '2021-01-01'],
                'reportDate': ['2019-12-31', '2019-06-30', '2019-06-30'],
                'accessionNumber': ['a', 'b', 'c'], 'primaryDocument': ['a.htm', 'b.htm', 'c.htm']}
    monkeypatch.setattr(sec, 'cik_for', lambda _: '0000000001')
    monkeypatch.setattr(sec, '_cached_json', fetch)
    docs = sec.get_filing_documents('TEST', '2020-03-20')['documents']
    assert [d['accession'] for d in docs] == ['b']
    assert len(calls) == 2


def test_facts_reject_future_restatements_and_separate_ytd(monkeypatch):
    facts = [{'val': 100, 'start': '2019-01-01', 'end': '2019-12-31', 'filed': '2020-02-01', 'form': '10-K'},
             {'val': 999, 'start': '2019-01-01', 'end': '2019-12-31', 'filed': '2021-02-01', 'form': '10-K'},
             {'val': 30, 'start': '2020-01-01', 'end': '2020-03-31', 'filed': '2020-05-01', 'form': '10-Q'},
             {'val': 65, 'start': '2020-01-01', 'end': '2020-06-30', 'filed': '2020-08-01', 'form': '10-Q'}]
    monkeypatch.setattr(sec, 'cik_for', lambda _: '1')
    monkeypatch.setattr(sec, '_cached_json', lambda *a: {'facts': {'us-gaap': {'Revenues': {'units': {'USD': facts}}}}})
    tables, ledger = pit.statements('TEST', '2020-09-01')
    assert tables['annual']['income']['rows'][0]['values'] == [100]
    assert tables['quarterly']['income']['rows'][0]['values'] == [30]
    assert tables['year_to_date']['income']['rows'][0]['values'] == [65]
    assert all(f['filed'] < '2020-09-01' for f in ledger)
    _, ledger = pit.statements('TEST', '2020-05-01')
    assert all(f['val'] == 100 for f in ledger)


def test_native_quote_does_not_expose_future_splits(monkeypatch):
    import yfinance
    history = pd.DataFrame({'Close': [5., 6., 80.], 'Stock Splits': [0., 4., 10.]},
                           index=pd.to_datetime(['2020-03-20', '2021-07-20', '2024-06-10']))
    instrument = SimpleNamespace(history=lambda **kw: history, history_metadata={'currency': 'USD'})
    monkeypatch.setattr(yfinance, 'Ticker', lambda *a, **kw: instrument)
    quote = pit.historical_quote('NVDA', '2020-03-22')
    assert quote['price'] == 200
    assert quote['date'] == '2020-03-20'
    assert quote['known_splits'] == []


def test_snapshot_boundary_and_integrity():
    docs = [{'filed': '2020-02-01', 'period': '2019-12-31'}]
    snapshot = {'as_of_date': '2020-03-20', 'company': {'symbol': 'TEST', 'filings': {'documents': docs}},
                'point_in_time': {'version': pit.VERSION, 'evidence_digest': pit.digest(docs), 'facts': []}}
    assert pit.validate_snapshot(snapshot, 'TEST', '2020-03-20')
    broken = deepcopy(snapshot)
    broken['company']['filings']['documents'][0]['filed'] = '2021-01-01'
    with pytest.raises(ValueError):
        pit.validate_snapshot(broken, 'TEST', '2020-03-20')
    with pytest.raises(ValueError):
        pit.validate_snapshot(snapshot, 'OTHER', '2020-03-20')


def test_date_route_uses_new_research_and_does_not_reuse_old_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {'old': {'id': 'old', 'ticker': 'MSFT', 'date': '2020-03-20', 'status': 'completed', 'historical_context': {}}})
    queued = []
    monkeypatch.setattr(server, 'POOL', SimpleNamespace(submit=lambda *args: queued.append(args)))
    result = server.queue_research('MSFT', [], '2020-03-20')
    assert result['id'] != 'old'
    job = server.JOBS[result['id']]
    assert job['automatic_valuation']['status'] == 'pending'
    assert job['research_mode'] == 'point_in_time'
    assert queued[0][:3] == (server.run_scheduled, server.run_job, result['id'])
    assert server.queue_research('MSFT', [], '2020-03-20')['id'] == result['id']
    assert len(queued) == 1
    with pytest.raises(ValueError):
        server.queue_research('MSFT', [], '2999-01-01')


def test_historical_memory_is_neither_read_nor_settled():
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = object.__new__(TradingAgentsGraph)
    graph.research_snapshot = {'point_in_time': {'version': pit.VERSION}}
    assert graph._memory_step({})['past_context'] == ''
    graph._log_state = lambda *args: None
    # No memory_log attribute: this fails if any decision/lesson is stored.
    graph.record_decision('TEST', '2020-03-20', {'final_trade_decision': 'Hold'})
