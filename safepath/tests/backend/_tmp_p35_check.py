from hypothesis import given, settings

import test_prop_failed_writes as m


@settings(max_examples=300, deadline=None)
@given(m.scenarios().filter(lambda s: s["op"] != "hide"))
def test_other_ops(s):
    m.test_failed_writes_leave_no_trace.hypothesis.inner_test(s)
