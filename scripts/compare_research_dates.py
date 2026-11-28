"""Summarize automatic company valuations across observation dates (no model calls)."""
import argparse
import csv
import io
import json
from urllib.request import urlopen


def read(base, path):
    with urlopen(base + path, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tickers', nargs='+')
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    args = parser.parse_args()
    jobs = {j['id']: j for scope in ('active', 'archived')
            for j in read(args.base_url, '/api/jobs?scope=' + scope)['jobs']
            if j['ticker'] in args.tickers}
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ticker', 'observation_date', 'job_id', 'valuation_status', 'quote_date', 'currency', 'quote',
                     'bear', 'base', 'bull', 'base_upside', 'first_growth', 'last_growth',
                     'first_margin', 'last_margin', 'wacc', 'cash', 'debt', 'other_claims', 'shares', 'limitations', 'gaps'])
    for job in sorted(jobs.values(), key=lambda j: (j['ticker'], j['date'], j['id'])):
        j = read(args.base_url, '/api/jobs/' + job['id'])
        auto = j.get('automatic_valuation', {})
        prepared = auto.get('prepared', {})
        v = j.get('valuation') or {}
        p = v.get('inputs', {})
        valid = auto.get('status') == 'completed' and p == prepared.get('inputs') and p.get('automatic_research')
        row = [j['ticker'], j['date'], j['id'], auto.get('status', 'pending')]
        if valid:
            base = p['scenarios']['base']
            row += [p.get('market_price_date'), v['currency'], p['market_price'],
                    *[v['scenarios'][k]['price'] for k in ('bear', 'base', 'bull')],
                    v['scenarios']['base']['upside'], base['growth'][0], base['growth'][-1],
                    base['margins'][0], base['margins'][-1], base['wacc'],
                    p['cash'], p['debt'], p['other_claims'], p['shares'], p['automatic_research']['limitations'], '']
        if not valid:
            quote = j.get('valuation_reference_quote') or {}
            row += [quote.get('date', ''), quote.get('currency', ''), quote.get('price', '')]
            row += [''] * 14 + ['; '.join(auto.get('gaps', []))]
        writer.writerow(row)
    print(output.getvalue(), end='')


if __name__ == '__main__':
    main()
