"""Frozen A-share environment evidence, bounded HTTP reads and real graph propagation."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows.market_environment import (
    build_market_environment,
    validate_market_environment,
)

NOW = datetime(2026, 11, 20, 17, tzinfo=ZoneInfo('Asia/Shanghai'))
CONFIG = {'market_environment_enabled': True, 'tsp_base_url': 'http://127.0.0.1:3018',
          'market_environment_max_age_days': 7, 'market_environment_timeout': 3}
ROW = {'date': '2026-11-20', 'state': 'lean_strong', 'score': 63,
       'phase': 'ignite', 'seal_rate': .68, 'index_pct': .012,
       'profit_score': 65, 'promo_rate': None, 'max_consecutive': 6}


def build(monkeypatch, rows=None, now=NOW, fail_mainline=False, trade_date='2026-11-20'):
    import tradingagents.dataflows.market_environment as module
    monkeypatch.setenv('TSP_TOKEN', 'secret-token')
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs['headers']['Authorization'] == 'Bearer secret-token'
        assert kwargs['allow_redirects'] is False
        class Response:
            status_code = 403 if fail_mainline and url.endswith('/mainline') else 200
            def json(self):
                if url.endswith('/history'):
                    return {'rows': rows if rows is not None else [ROW]}
                return {'rows': [{'date': '2026-11-20', 'rank': 1, 'member': '<行业>',
                                  'score': 80, 'leader_symbol': '600000.SH'}],
                        'membership_note': '当前快照回看历史'}
        return Response()
    monkeypatch.setattr(module.requests, 'get', get)
    return build_market_environment(CONFIG, '600519.SS', trade_date, now=now), calls


def test_freezes_actual_rows_units_and_provenance(monkeypatch):
    context, calls = build(monkeypatch)
    validate_market_environment(context, '600519.SS', '2026-11-20')
    assert context['coverage']['status'] == 'available'
    assert context['latest'] == ROW
    assert context['latest']['promo_rate'] is None
    assert context['units']['seal_rate'] == 'ratio'
    assert context['units']['score'] == 'score_0_100'
    assert len(calls) == 3
    assert all(c[1]['params']['end'] == '2026-11-20' for c in calls)
    assert context['mainlines']['concept'][0]['member'] == '<行业>'
    assert 'secret-token' not in str(context)
    damaged = deepcopy(context)
    damaged['latest']['score'] = 99
    with pytest.raises(ValueError):
        validate_market_environment(damaged, '600519.SS', '2026-11-20')
    with pytest.raises(ValueError):
        validate_market_environment(context, '000001.SZ', '2026-11-20')


def test_intraday_uses_completed_previous_day_and_rejects_future_rows(monkeypatch):
    rows = [ROW, {**ROW, 'date': '2026-11-19'}, {**ROW, 'date': '2026-11-21'}]
    context, calls = build(monkeypatch, rows, NOW.replace(hour=10))
    assert context['latest']['date'] == '2026-11-19'
    assert calls[0][1]['params']['end'] == '2026-11-19'
    assert all(r['date'] <= '2026-11-19' for r in context['history'])
    assert context['mainlines']['concept'] == []


def test_stale_partial_and_missing_are_not_neutral_scores(monkeypatch):
    context, _ = build(monkeypatch, [{**ROW, 'date': '2026-11-01'}])
    assert context['coverage']['status'] == 'stale'
    context, _ = build(monkeypatch, fail_mainline=True)
    assert context['coverage']['status'] == 'partial'
    assert context['latest']['score'] == 63
    assert context['mainlines']['industry'] == []
    context, _ = build(monkeypatch, [])
    assert context['coverage']['status'] == 'unavailable'
    assert context['latest'] is None


def test_no_live_reads_for_other_markets_disabled_or_historical(monkeypatch):
    import tradingagents.dataflows.market_environment as module
    monkeypatch.setattr(module.requests, 'get', lambda *a, **kw: pytest.fail('Unexpected HTTP'))
    assert build_market_environment(CONFIG, 'BTC-USD', '2026-11-20', now=NOW) is None
    assert build_market_environment({**CONFIG, 'market_environment_enabled': False}, '600519.SS', '2026-11-20', now=NOW) is None
    context = build_market_environment(CONFIG, '600519.SS', '2026-11-19', now=NOW)
    assert context['coverage']['status'] == 'unavailable'
    assert context['latest'] is None


def test_auth_and_network_errors_are_sanitized(monkeypatch):
    import tradingagents.dataflows.market_environment as module
    monkeypatch.setenv('TSP_TOKEN', 'secret-token')
    def broken(*a, **kw):
        raise module.requests.ConnectionError('secret-token http://private-host')
    monkeypatch.setattr(module.requests, 'get', broken)
    context = build_market_environment(CONFIG, '600519.SS', '2026-11-20', now=NOW)
    assert context['coverage']['status'] == 'unavailable'
    assert 'secret-token' not in str(context) and 'private-host' not in str(context)


def test_real_graph_supplies_identical_evidence_to_research_and_all_risk_roles(monkeypatch, tmp_path):
    import json

    from tests.test_financial_snapshot import make_graph
    from tradingagents.dataflows.date_window import get_current_date
    from tradingagents.dataflows.research_store import ResearchStore
    from tradingagents.reporting import write_report_tree

    day = get_current_date()
    now = datetime.fromisoformat(day + 'T17:00:00+08:00')
    row = {**ROW, 'date': day}
    environment, _ = build(monkeypatch, [row], now, trade_date=day)
    snapshot = {'id': 'financial-cn', 'as_of_date': day,
                'company': {'symbol': '600519.SS', 'profile': {}, 'statements': {}}, 'peers': []}
    original = deepcopy(environment)
    graph, prompts = make_graph(monkeypatch, tmp_path, snapshot, market_environment=environment)
    environment['latest']['score'] = 99
    state, _ = graph.propagate('600519.SS', day)
    assert state['market_environment'] == original
    # Analyst, bull/bear, research manager, trader, three risk analysts and PM.
    assert len(prompts) == 9
    assert all(original['snapshot_id'] in prompt and '63' in prompt for prompt in prompts)
    assert all('drawdown' in prompt and 'NOT return probabilities' in prompt for prompt in prompts)
    assert 'market-environment assessment' in prompts[-1]
    path = next((tmp_path / 'results').rglob('full_states_log_*.json'))
    assert json.loads(path.read_text())['market_environment'] == original
    store = ResearchStore(tmp_path / 'cache' / 'environment.sqlite3')
    assert store.load_market_environment(original['snapshot_id']) == original
    write_report_tree(state, '600519.SS', tmp_path / 'export')
    assert '63/100' in (tmp_path / 'export/complete_report.md').read_text()
    assert '市场环境' in (tmp_path / 'export/complete_report.html').read_text()


def test_web_worker_freezes_once_and_retries_with_identical_evidence(monkeypatch, tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace

    from tradingagents.graph import trading_graph
    from tradingagents.web import server

    environment, _ = build(monkeypatch)
    snapshot = {'id': 'cn-run', 'as_of_date': '2026-11-20',
                'company': {'symbol': '600519.SS', 'profile': {}, 'statements': {}}, 'peers': []}
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    job = {'id': 'cn-run', 'ticker': '600519.SS', 'date': '2026-11-20', 'peers': [],
           'research_snapshot': snapshot, 'snapshot_digest': digest}
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {'cn-run': job})
    calls = []
    monkeypatch.setattr(server, 'build_market_environment', lambda *a, **kw: calls.append(kw) or deepcopy(environment))
    from tradingagents.dataflows.research_store import ResearchStore
    ResearchStore(tmp_path / 'research.sqlite3').archive_snapshot(snapshot)

    class Graph:
        def __init__(self, **kw):
            assert kw['market_environment'] == environment
            assert kw['selected_analysts'] == ['fundamentals', 'operating', 'metrics', 'news']
        def run_settings(self):
            return {}
        def propagate(self, *a):
            return {'market_environment': deepcopy(environment),
                    **dict.fromkeys(('fundamentals_report', 'operating_report', 'metrics_report', 'news_report'), 'saved'),
                    'final_trade_decision': 'Hold'}, 'Hold'
    monkeypatch.setattr(trading_graph, 'TradingAgentsGraph', Graph)
    monkeypatch.setattr(server, 'queue_automatic_valuation', lambda _: None)
    server.run_job('cn-run')
    assert job['status'] == 'completed' and job['market_environment_consumed'] is True
    assert len(calls) == 1
    assert json.loads((tmp_path / 'cn-run.json').read_text())['market_environment'] == environment
    job['status'] = 'failed'
    monkeypatch.setattr(server, 'today', lambda: '2026-11-21')
    monkeypatch.setattr(server, 'POOL', SimpleNamespace(submit=lambda *a: None))
    server.retry_job('cn-run')
    monkeypatch.setattr(server, 'build_market_environment', lambda *a, **kw: pytest.fail('Retry refetched environment'))
    server.run_job('cn-run')
    assert job['status'] == 'completed' and job['market_environment'] == environment
    assert ResearchStore(tmp_path / 'research.sqlite3').load_market_environment(environment['snapshot_id']) == environment
    # Corruption must be caught before queuing, not silently replaced by a new read.
    job['status'] = 'failed'
    job['market_environment']['latest']['score'] = 0
    with pytest.raises(ValueError):
        server.retry_job('cn-run')


def test_missing_token_and_unavailable_context_reach_prompts(monkeypatch):
    from tradingagents.agents.context import get_instrument_context_from_state
    monkeypatch.delenv('TSP_TOKEN', raising=False)
    context = build_market_environment(CONFIG, '600519.SS', '2026-11-20', now=NOW)
    assert context['coverage']['status'] == 'unavailable' and context['latest'] is None
    text = get_instrument_context_from_state({'company_of_interest': '600519.SS', 'market_environment': context})
    assert 'TSP_TOKEN' in text and 'never invent a neutral score' in text
