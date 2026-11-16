"""Unit tests for the pure field converters in ``server.request_io`` (#138)."""

import pytest

from consensus.server.request_io import float_field, int_field


def test_int_field_converts_and_defaults():
    """A present value is converted; an absent one takes the default."""
    assert int_field({"n": "7"}, "n", 0) == 7
    assert int_field({"n": 7.9}, "n", 0) == 7
    assert int_field({}, "n", 20) == 20


def test_float_field_converts_and_defaults():
    """A present value is converted; an absent one takes the default."""
    assert float_field({"x": "1.5"}, "x", 0.0) == 1.5
    assert float_field({}, "x", 0.25) == 0.25


@pytest.mark.parametrize("value", ["ten", None, [1], float("inf"), "1e999"])
def test_int_field_names_the_field(value):
    """Any unconvertible value is a ValueError naming field and type."""
    with pytest.raises(ValueError, match=r"^n must be an integer$"):
        int_field({"n": value}, "n", 0)


# 10**400: float() of a huge JSON integer is an OverflowError.
@pytest.mark.parametrize("value", ["cheap", None, {"a": 1}, 10**400])
def test_float_field_names_the_field(value):
    """Any unconvertible value is a ValueError naming field and type."""
    with pytest.raises(ValueError, match=r"^x must be a number$"):
        float_field({"x": value}, "x", 0.0)
