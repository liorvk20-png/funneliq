"""
Regression test for the quietest bug in the project: PostgREST caps a response
at 1,000 rows by default and says nothing about it. The first version of the
summary averaged 1,000 of 3,500 records and produced entirely plausible numbers
— avg LTV 21.8 against a true 22.0. Nothing about the output invited suspicion.
"""
from app.main import _fetch_all


class FakeTable:
    """Stands in for PostgREST, including its silent 1,000-row ceiling."""

    CEILING = 1000

    def __init__(self, rows):
        self.rows, self._range = rows, (0, self.CEILING - 1)
        self.calls = 0

    def select(self, _columns):
        return self

    def range(self, start, end):
        self._range = (start, min(end, start + self.CEILING - 1))
        return self

    def execute(self):
        self.calls += 1
        start, end = self._range
        return type("R", (), {"data": self.rows[start:end + 1]})()


class FakeClient:
    def __init__(self, rows):
        self.table_obj = FakeTable(rows)

    def table(self, _name):
        return self.table_obj


def test_fetch_all_pages_past_the_ceiling():
    rows = [{"id": i} for i in range(3500)]
    client = FakeClient(rows)
    got = _fetch_all(client, "id")
    assert len(got) == 3500
    assert got[0]["id"] == 0 and got[-1]["id"] == 3499
    assert client.table_obj.calls == 4          # 1000 + 1000 + 1000 + 500


def test_a_single_page_still_terminates():
    client = FakeClient([{"id": i} for i in range(42)])
    assert len(_fetch_all(client, "id")) == 42
    assert client.table_obj.calls == 1


def test_an_exact_multiple_does_not_loop_forever():
    client = FakeClient([{"id": i} for i in range(2000)])
    assert len(_fetch_all(client, "id")) == 2000
    assert client.table_obj.calls == 3          # the third page returns empty


def test_a_server_ignoring_range_raises_instead_of_hanging():
    """
    Found by mutation: removing the range() call made the suite hang rather than
    fail. An endless request is the worst failure mode of the three — it burns a
    worker and reports nothing.
    """
    import pytest

    class StuckTable(FakeTable):
        def range(self, _start, _end):
            return self                       # ignores the range, always page one

    class StuckClient:
        def __init__(self, rows):
            self.table_obj = StuckTable(rows)

        def table(self, _name):
            return self.table_obj

    with pytest.raises(RuntimeError, match="ignoring the range parameter"):
        _fetch_all(StuckClient([{"id": i} for i in range(3500)]), "id")
