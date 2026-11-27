import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tradingagents.web import server


@pytest.fixture
def library(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'STORE', tmp_path)
    monkeypatch.setattr(server, 'JOBS', {
        'sample': {'id': 'sample', 'ticker': 'MSFT', 'date': '2020-03-23',
                   'created_at': '2026-11-27', 'status': 'completed',
                   'reports': {'test': 'original'}, 'valuation': {'inputs': {'shares': 10}}}
    })
    return tmp_path


def test_archive_trash_restore_persists_without_loss(library, monkeypatch):
    original = json.loads(json.dumps(server.JOBS['sample']))
    for state in ('archived', 'trash', 'active'):
        server.set_library_state('sample', state)
        saved = json.loads((library / 'sample.json').read_text())
        assert saved['library_state'] == state
        assert saved['reports'] == original['reports']
        assert saved['valuation'] == original['valuation']
    server.set_library_state('sample', 'archived')
    monkeypatch.setattr(server, 'JOBS', {})
    monkeypatch.setattr(server, 'build_default_config', lambda: {'results_dir': str(library)})
    server.load_history()
    assert server.JOBS['sample']['library_state'] == 'archived'


def test_running_and_invalid_changes_are_rejected(library):
    for status in ('running', 'queued'):
        server.JOBS['sample']['status'] = status
        with pytest.raises(ValueError, match='正在运行'):
            server.set_library_state('sample', 'trash')
    with pytest.raises(ValueError):
        server.set_library_state('sample', 'invalid')
    with pytest.raises(ValueError):
        server.set_library_state('missing', 'archived')


def test_disk_failure_keeps_memory_state(library, monkeypatch):
    def fail(job):
        raise OSError('disk full')
    monkeypatch.setattr(server, 'persist', fail)
    with pytest.raises(OSError):
        server.set_library_state('sample', 'archived')
    assert server.JOBS['sample'].get('library_state', 'active') == 'active'


@pytest.mark.integration
def test_api_lists_and_actions(library):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    def get(path):
        with urllib.request.urlopen(base + path) as response:
            return json.load(response)
    try:
        assert len(get('/api/jobs')['jobs']) == 1
        for state in ('archived', 'trash', 'active'):
            request = urllib.request.Request(base + '/api/jobs/sample/library',
                data=json.dumps({'state': state}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request) as response:
                assert json.load(response)['library_state'] == state
            assert len(get('/api/jobs?scope=' + state)['jobs']) == 1
            assert len(get('/api/jobs')['jobs']) == (1 if state == 'active' else 0)
        with pytest.raises(urllib.error.HTTPError) as error:
            get('/api/jobs?scope=invalid')
        assert error.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
