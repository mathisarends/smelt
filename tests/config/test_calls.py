import pytest

from smelt.config import calls


def test_matches_a_qualified_call_in_every_layer_by_default() -> None:
    matcher = calls.to("datetime.datetime.now")

    assert (matcher.target, matcher.layer) == ("datetime.datetime.now", None)


def test_in_layer_returns_a_scoped_copy() -> None:
    everywhere = calls.to("datetime.datetime.now")

    scoped = everywhere.in_layer("domain")

    assert (scoped.target, scoped.layer) == ("datetime.datetime.now", "domain")
    assert everywhere.layer is None


@pytest.mark.parametrize(
    "target", ["now", "", ".now", "datetime..now", "datetime.now()", "datetime.1st"]
)
def test_rejects_a_target_that_is_not_a_qualified_name(target: str) -> None:
    with pytest.raises(ValueError, match="qualified call target"):
        calls.to(target)


@pytest.mark.parametrize("layer", ["", "  "])
def test_rejects_a_blank_layer(layer: str) -> None:
    with pytest.raises(ValueError, match="layer name"):
        calls.to("datetime.datetime.now").in_layer(layer)
