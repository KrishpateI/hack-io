"""Property 24: Sanitization removes markup and is idempotent.

**Validates: Requirements 28.5**
"""
from __future__ import annotations

import re

from hypothesis import given, settings, strategies as st

from app.api.sanitize import sanitize_text

_TAGS = ["b", "i", "script", "style", "div", "a", "img", "iframe", "p", "svg"]

_plain = st.text(
    alphabet=st.characters(
        blacklist_characters="<>&\xa0",  # nh3 serializes NBSP as &nbsp; (safe)
        blacklist_categories=("Cs", "Cc"),
    ),
    max_size=60,
)

_markup = st.one_of(
    st.builds(
        lambda t, inner: f"<{t}>{inner}</{t}>", st.sampled_from(_TAGS), _plain
    ),
    st.builds(
        lambda t, v: f'<{t} onclick="{v}" href="javascript:alert(1)">',
        st.sampled_from(_TAGS),
        st.text(alphabet="abc12 ", max_size=8),
    ),
    st.builds(lambda p: f"<script>alert('{p}')</script>", st.text("xyz", max_size=5)),
    st.just("<!-- c -->"),
    st.just("<<b>script>"),
    st.just("&lt;b&gt;"),
)

_mixed = st.lists(st.one_of(_plain, _markup, st.sampled_from(["<", ">", "&"])), max_size=8).map("".join)

_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z!?]")


@settings(max_examples=200, deadline=None)
@given(text=st.one_of(_mixed, st.text(max_size=80)))
def test_no_markup_and_idempotent(text: str) -> None:
    out = sanitize_text(text)
    assert not _TAG_RE.search(out)
    assert "<script" not in out.lower()
    assert sanitize_text(out) == out


@settings(max_examples=100, deadline=None)
@given(text=_plain)
def test_plain_text_preserved(text: str) -> None:
    assert sanitize_text(text) == text


@settings(max_examples=100, deadline=None)
@given(inner=_plain, tag=st.sampled_from(["b", "i", "p", "div", "a"]))
def test_wrapping_tags_stripped_text_kept(inner: str, tag: str) -> None:
    assert sanitize_text(f"<{tag}>{inner}</{tag}>") == inner


def test_script_content_removed() -> None:
    out = sanitize_text("hi<script>alert(1)</script>there")
    assert "alert" not in out
    assert out == "hithere"
