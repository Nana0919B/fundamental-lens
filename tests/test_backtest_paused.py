"""Strategy simulations pause independently of archives and dated company research."""
from types import SimpleNamespace

from tradingagents.web import server


def handler(path):
    h = object.__new__(server.Handler)
    h.path = path
    h.headers = {'Host': '127.0.0.1:8766'}
    h.server = SimpleNamespace(server_port=8766)
    responses = []
    h.send = lambda data, status=200: responses.append((status, data))
    return h, responses


def test_new_simulations_are_paused_without_starting_work(monkeypatch):
    def unexpected(*args):
        raise AssertionError('must not create a strategy simulation')
    monkeypatch.setattr(server.backtest_api, 'create', unexpected)
    h, responses = handler('/api/valuation-backtests')
    h.do_POST()
    assert responses[0][0] == 409
    assert '暂时停用' in responses[0][1]['error']


def test_existing_backtest_records_remain_readable(monkeypatch):
    records = [{'id': 'existing', 'status': 'completed'}]
    monkeypatch.setattr(server.backtest_api, 'records', lambda _: records)
    h, responses = handler('/api/valuation-backtests')
    h.do_GET()
    assert responses == [(200, records)]
