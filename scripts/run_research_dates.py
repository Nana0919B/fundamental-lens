"""Submit company research at multiple dates through the normal application API.

Example: .venv/bin/python scripts/run_research_dates.py META AMZN --dates 2022-12-30 2024-12-31 2026-11-28
Existing active studies at the same date are reused; failed research is reported,
never silently retried or converted into default valuation assumptions.
"""
import argparse
import json
import time
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base, path, payload=None):
    req = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=60) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tickers', nargs='+')
    parser.add_argument('--dates', nargs='+', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    parser.add_argument('--wait', action='store_true', help='Wait for research and automatic valuation to reach terminal states')
    args = parser.parse_args()
    for day in args.dates:
        date.fromisoformat(day)
    ids = []
    for ticker in dict.fromkeys(t.upper() for t in args.tickers):
        for day in dict.fromkeys(args.dates):
            while True:
                jobs = request(args.base_url, '/api/jobs')['jobs']
                existing = next((j for j in jobs if j['ticker'] == ticker and j['date'] == day
                                 and not j.get('historical_context')), None)
                if existing:
                    result = {'id': existing['id'], 'reused': True}
                    break
                if sum(j['status'] in ('queued', 'running') for j in jobs) >= 3:
                    time.sleep(10)
                    continue
                try:
                    result = request(args.base_url, '/api/jobs', {'ticker': ticker, 'date': day, 'peers': []})
                    break
                except HTTPError as exc:
                    message = exc.read().decode()
                    if exc.code != 400 or '已有 3 个任务' not in message:
                        raise RuntimeError(message) from exc
                    time.sleep(10)
            ids.append(result['id'])
            print(json.dumps({'ticker': ticker, 'date': day, **result}, ensure_ascii=False), flush=True)
    if args.wait:
        pending = set(ids)
        while pending:
            for job_id in list(pending):
                j = request(args.base_url, '/api/jobs/' + job_id)
                a = j.get('automatic_valuation', {})
                if j['status'] not in ('queued', 'running') and (j['status'] != 'completed' or a.get('status') not in ('pending', 'queued', 'running')):
                    print(json.dumps({'id': job_id, 'research_status': j['status'], 'valuation_status': a.get('status'), 'gaps': a.get('gaps', [])}, ensure_ascii=False), flush=True)
                    pending.remove(job_id)
            if pending:
                time.sleep(10)


if __name__ == '__main__':
    main()
