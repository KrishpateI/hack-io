"""Property test for building height class.

Validates: Requirements 6.2, 6.3
"""
from hypothesis import given, settings, strategies as st

from app.geo import HIGH_RISE, LOW_RISE, MID_RISE, UNKNOWN, building_height_class

# Mix of valid numbers, zero/negative, non-numeric and absent values.
values = st.one_of(
    st.none(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(min_value=0, max_value=100, allow_nan=False),
    st.sampled_from([0, 0.0, 11.999, 12, 34.999, 35, 3, 4, 11, 12]),
    st.booleans(),
    st.text(max_size=5),
)


def _present(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:
        return None
    return v


def _oracle(h, f):
    h, f = _present(h), _present(f)
    if h is not None:
        return LOW_RISE if h < 12 else MID_RISE if h < 35 else HIGH_RISE
    if f is not None:
        return LOW_RISE if f < 4 else MID_RISE if f < 12 else HIGH_RISE
    return UNKNOWN


# Feature: safepath-evacuation-navigation, Property 21: Building height class is a pure, total function of source data
# Validates: Requirements 6.2, 6.3
@settings(max_examples=300)
@given(values, values)
def test_building_height_class_pure_total(h, f):
    result = building_height_class(h, f)
    assert result in {LOW_RISE, MID_RISE, HIGH_RISE, UNKNOWN}
    assert result == building_height_class(h, f)  # pure
    assert result == _oracle(h, f)
    # Height takes precedence over floors when present.
    if _present(h) is not None:
        assert result == building_height_class(h, None)
    elif _present(f) is not None:
        assert result == building_height_class(None, f)
    else:
        assert result == UNKNOWN
