import parity.test_support._internal
import parity.test_support._internal as internal
from parity.test_support import (
    Service,
    service as service_module,
)
from parity.test_support._internal import value
from parity.test_support.service import (
    _CONSTANT,
    _normalize as normalized,
    normalize,
)
from os import _exit


def test_imported_private():
    assert normalized("A") == normalize("a")
    assert _CONSTANT == value()


def test_module_attributes():
    assert service_module._normalize("A")
    assert parity.test_support._internal.value()
    assert internal.value()
    assert service_module.__name__
    _exit


def test_instances():
    made = Service()
    annotated: Service = Service()
    assert made._state
    assert annotated._hidden()
    assert Service()._state
    assert made.__class__


def test_rebinding(flag):
    made = Service()
    if flag:
        made = object()
    assert made._state


def test_branch_binding(flag):
    if flag:
        made = Service()
        assert made._state
    else:
        made = None
    assert made


def test_loop_binding(values):
    made = Service()
    for made in values:
        assert made._state


def test_with_binding(context):
    made = Service()
    with context as made:
        assert made._state


def test_walrus(source):
    made = Service()
    if made := source():
        assert made._state


def test_except_binding():
    made = Service()
    try:
        assert made._state
    except ValueError as made:
        assert made


def test_match_binding(subject):
    made = Service()
    match subject:
        case [made, *rest]:
            assert made._state and rest
        case {"key": other, **made}:
            assert other and made._state
        case Service() as made:
            assert made._state
    assert made._state


def test_nested_function():
    made = Service()

    def inner():
        return made._state

    assert inner()


def test_lambda_and_comprehension():
    made = Service()
    read = lambda made: made._state
    assert read(1)
    assert [made._state for made in [1]]
    assert made._state


def test_deleted():
    made = Service()
    del made
    assert made._state


def test_augmented():
    made = Service()
    made += 1
    assert made._state


def test_annotated_without_value(source):
    made: Service
    made = source()
    assert made._state


def test_annotation_mismatch():
    made: Service = object()
    assert made._state
