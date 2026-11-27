"""Validate frozen historical research without importing web or valuation code."""
import hashlib
import json

VERSION = "company-pit-1.0"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_snapshot(snapshot, ticker, day):
    guard = snapshot.get('point_in_time') or {}
    documents = snapshot.get('company', {}).get('filings', {}).get('documents', [])
    if (guard.get('version') != VERSION or snapshot.get('as_of_date') != day
            or snapshot.get('company', {}).get('symbol') != ticker or not documents
            or snapshot.get('peers') or guard.get('evidence_digest') != digest(documents)):
        raise ValueError('历史研究快照的日期、主体或原文绑定无效')
    for doc in documents:
        if not doc.get('filed') or doc['filed'] >= day or not doc.get('period') or doc['period'] > day:
            raise ValueError('历史研究包含尚未公开的原文')
    quote = guard.get('quote')
    if quote and (quote['date'] > day or quote['price'] <= 0):
        raise ValueError('历史参考股价日期或数值无效')
    for tables in snapshot.get('company', {}).get('statements', {}).values():
        for table in tables.values():
            if any(period[:10] > day for period in table.get('periods', [])):
                raise ValueError('历史报表含未来期间')
    for fact in guard.get('facts', []):
        if fact['filed'] >= day or fact['end'] > day:
            raise ValueError('历史研究包含尚未公开的数值')
    return True

