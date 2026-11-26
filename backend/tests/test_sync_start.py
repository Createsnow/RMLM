"""金蝶领料单首次同步起始日期：首次 / 全量只拉起始日期之后的单据，之后按水位增量（.env K3_SYNC_START_DATE / MONTHS）。"""
from datetime import date, datetime

import pytest

from app import clock
from app import db as dbmod
from app.config import settings
from app.errors import BizError
from app.services import doc_sync


class Reader:
    def __init__(self, rows=None):
        self.rows, self.calls = rows or [], []

    def fetch_docs(self, since=None):
        self.calls.append(since)
        return self.rows


def _sync(reader, full=False):
    with dbmod.SessionLocal() as db:
        out = doc_sync.sync_docs(db, full=full, reader=reader)
        db.commit()
    return out


def test_months_ago_clamps_month_end():
    assert doc_sync._months_ago(date(2026, 11, 26), 3) == date(2026, 8, 26)
    assert doc_sync._months_ago(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert doc_sync._months_ago(date(2024, 5, 31), 3) == date(2024, 2, 29)
    assert doc_sync._months_ago(date(2026, 2, 15), 3) == date(2025, 11, 15)


def test_first_sync_defaults_to_three_months_then_incremental(org, monkeypatch):
    monkeypatch.setattr(settings, "k3_sync_start_date", "")
    monkeypatch.setattr(settings, "k3_sync_start_months", 3)
    want = datetime.combine(doc_sync._months_ago(clock.today(), 3), datetime.min.time())
    r = Reader()
    st = _sync(r)
    assert r.calls == [want] and st["mode"] == "full" and st["since"] == want.isoformat(sep=" ")
    # 第二次：已有水位，按水位增量，不再用起始日期
    _sync(r)
    assert r.calls[1] > want


def test_fixed_start_date_wins_and_full_sync_uses_it(org, monkeypatch):
    monkeypatch.setattr(settings, "k3_sync_start_date", "2026-06-01")
    r = Reader()
    _sync(r)
    _sync(r, full=True)
    assert r.calls == [datetime(2026, 6, 1), datetime(2026, 6, 1)]


def test_bad_start_date_reports_clear_error(org, monkeypatch):
    monkeypatch.setattr(settings, "k3_sync_start_date", "2026/06/01")
    with pytest.raises(BizError, match="K3_SYNC_START_DATE"):
        _sync(Reader())
