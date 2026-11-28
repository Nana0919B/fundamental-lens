"""Valuation failures and slow model calls must not take research down with them."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest

from tradingagents.web import server, valuation
from tradingagents.web.valuation_service import ValuationService


def test_prepare_failure_cannot_mutate_or_hide_research(monkeypatch):
    job = {'id': 'research', 'status': 'completed', 'phase': '分析完成',
           'reports': {'operating_report': 'Original analysis'}, 'valuation': {'id': 'saved'}}
    before = deepcopy(job)
    def broken_prepare(candidate):
        candidate['reports']['operating_report'] = 'accidental overwrite'
        raise ValueError('Bad valuation evidence')
    monkeypatch.setattr(valuation, 'prepare', broken_prepare)
    setup = ValuationService.prepare(job)
    assert setup['module_error']
    assert setup['saved']['id'] == 'saved'
    assert job == before


def test_service_rejects_changes_to_research_state():
    service = ValuationService(store=None, jobs={}, lock=None, persist=None, update=None,
                               check_pause=None, pause_exception=RuntimeError)
    for field in ('reports', 'status', 'phase', 'research_snapshot', 'rating'):
        with pytest.raises(ValueError, match='cannot modify research'):
            service.update('x', **{field: 'replacement'})


def test_slow_valuation_does_not_block_research(monkeypatch, tmp_path):
    entered, release, research_done = Event(), Event(), Event()
    research_pool, valuation_pool = ThreadPoolExecutor(1), ThreadPoolExecutor(1)
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {
        'v': {'id': 'v', 'status': 'completed', 'automatic_valuation': {'status': 'queued'}},
        'r': {'id': 'r', 'status': 'queued'},
    })
    monkeypatch.setattr(server, 'POOL', research_pool)
    monkeypatch.setattr(server, 'VALUATION_POOL', valuation_pool)
    def slow(_):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(server, 'run_automatic_valuation', slow)
    monkeypatch.setattr(server, 'run_job', lambda _: research_done.set())
    try:
        future = server.submit_work(server.run_automatic_valuation, 'v')
        assert entered.wait(2)
        server.submit_work(server.run_job, 'r').result(timeout=2)
        assert research_done.is_set()
        assert not future.done()
    finally:
        release.set()
        research_pool.shutdown(wait=True)
        valuation_pool.shutdown(wait=True)


def test_pause_valuation_does_not_change_research_phase(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {'v': {'id': 'v', 'status': 'completed', 'phase': '分析完成',
                                            'automatic_valuation': {'status': 'running'}}})
    server.pause_job('v')
    assert server.JOBS['v']['phase'] == '分析完成'
    assert server.JOBS['v']['status'] == 'completed'
    assert '正在暂停' in server.JOBS['v']['automatic_valuation']['phase']


def test_research_detail_api_survives_valuation_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {'one': {'id': 'one', 'ticker': 'AAPL', 'status': 'completed',
                                               'reports': {'operating_report': 'Preserved business analysis'}}})
    def fail(_):
        raise RuntimeError('Simulated valuation failure')
    monkeypatch.setattr(valuation, 'prepare', fail)
    responses = []
    handler = object.__new__(server.Handler)
    handler.path = '/api/jobs/one'
    handler.headers = {'Host': '127.0.0.1:8765'}
    handler.server = SimpleNamespace(server_port=8765)
    handler.send = lambda payload, status=200: responses.append((status, payload))
    handler.do_GET()
    status, data = responses[0]
    assert status == 200
    assert data['valuation_setup']['module_error']
    assert 'Preserved business analysis' in data['report_html']['operating_report']
    assert data['status'] == 'completed'


@pytest.mark.parametrize('price,day,valid', [(100, '2024-12-30', True),
                                          (100, '2025-01-01', False), (True, '2024-12-30', False)])
def test_reference_price_available_without_valuation_but_never_future(tmp_path, price, day, valid):
    import json
    job = {'id': 'a' * 32, 'date': '2024-12-31', 'reports': {'operating_report': 'unchanged'},
           'automatic_valuation': {'status': 'needs_review', 'run_id': 'b' * 32}}
    original = deepcopy(job)
    folder = tmp_path / 'valuation-research' / job['id'] / ('b' * 32)
    folder.mkdir(parents=True)
    (folder / 'quote.json').write_text(json.dumps({'price': price, 'date': day, 'currency': 'USD', 'source': 'Archived close'}))
    service = ValuationService(store=tmp_path, jobs={}, lock=None, persist=None, update=None,
                               check_pause=None, pause_exception=RuntimeError)
    assert bool(service.reference_quote(job)) is valid
    assert job == original
