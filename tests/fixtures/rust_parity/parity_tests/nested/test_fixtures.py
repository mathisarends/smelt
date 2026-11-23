import pytest

from parity.test_support.service import Service, Worker


def test_overridden_fixture(annotated):
    annotated._step()
    assert annotated._state


def test_inherited(quoted, inferred, yielded, iterated, generated):
    assert quoted._state
    assert inferred._state
    assert yielded._hidden()
    iterated._step()
    assert generated._state


async def test_async_fixture(async_iterated):
    assert async_iterated._state


def test_tuples(paired, inferred_pair):
    service, worker = paired
    other, partner = inferred_pair
    assert service._state
    worker._step()
    assert other._state
    partner._step()


def test_unknown_fixtures(conflicting, unfinished, delegated, wraps_parent):
    assert conflicting._state
    assert unfinished._state
    assert delegated._state
    assert wraps_parent._state


def test_renamed(renamed):
    assert renamed._state


def test_shadowed_by_annotation(annotated: Service):
    assert annotated._state


def test_public_read_only(quoted):
    assert quoted.public
    assert quoted.plain()


def test_write_is_not_a_read(quoted):
    quoted._state = 1
    assert quoted.public


class TestInClass:
    @pytest.fixture
    def local(self) -> Worker:
        return Worker()

    @pytest.fixture
    def scoped(self) -> Service:
        return Service()

    def test_local(self, local, scoped):
        local._step()
        assert scoped._state


class Helper:
    def test_not_collected(self, quoted):
        assert quoted._state
