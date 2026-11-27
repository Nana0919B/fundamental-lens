from types import SimpleNamespace

import pytest

from tradingagents.web import server


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {})
    calls = []
    monkeypatch.setattr(server, 'POOL', SimpleNamespace(submit=lambda *args: calls.append(args)))
    monkeypatch.setattr(server, 'VALUATION_POOL', server.POOL)
    return calls


def test_queued_pause_invalidates_old_worker_and_resumes_once(setup):
    server.JOBS['a'] = {'id': 'a', 'status': 'queued', 'date': server.today(), 'ticker': 'MSFT'}
    server.submit_work(server.run_job, 'a')
    old = setup[0]
    assert server.pause_job('a')['status'] == 'paused'
    server.resume_job('a')
    assert len(setup) == 2
    # Stale queued worker must do nothing, even after the user resumes.
    old[0](*old[1:])
    assert server.JOBS['a']['status'] == 'queued'
    with pytest.raises(ValueError):
        server.resume_job('a')


def test_running_pause_waits_for_boundary_and_preserves_reports(setup):
    server.JOBS['a'] = {'id': 'a', 'status': 'running', 'reports': {'fundamentals_report': 'saved'}}
    assert server.pause_job('a')['status'] == 'pausing'
    assert server.JOBS['a']['status'] == 'running'
    with pytest.raises(server.PauseRequested):
        server.check_pause('a')
    with pytest.raises(ValueError):
        server.resume_job('a')
    assert server.JOBS['a']['reports']['fundamentals_report'] == 'saved'


def test_valuation_pause_and_resume_does_not_restart_research(setup):
    server.JOBS['a'] = {'id': 'a', 'status': 'completed', 'automatic_valuation': {'status': 'queued'}, 'reports': {'fundamentals_report': 'saved'}}
    assert server.pause_job('a')['status'] == 'paused'
    assert server.JOBS['a']['status'] == 'completed'
    server.resume_job('a')
    assert setup[-1][1] is server.run_automatic_valuation
    assert not server.JOBS['a']['pause_requested']
    assert server.JOBS['a']['reports']['fundamentals_report'] == 'saved'


def test_automatic_pause_is_not_reported_as_failure(setup):
    server.JOBS['a'] = {'id': 'a', 'ticker': 'MSFT', 'date': server.today(), 'status': 'completed', 'pause_requested': True}
    server.run_automatic_valuation('a')
    assert server.JOBS['a']['automatic_valuation']['status'] == 'paused'
    assert server.JOBS['a']['status'] == 'completed'
