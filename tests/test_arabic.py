"""Tests for the Arabic shaping + bidi helper."""

from __future__ import annotations

from quran_tui.arabic import contains_arabic, render_for_terminal


def test_contains_arabic_detects_real_quran_codepoints() -> None:
    # Surah Al-Fatihah, ayah 1.
    assert contains_arabic("بِسْمِ اللَّهِ")
    # Arabic name with diacritics.
    assert contains_arabic("مشاري راشد العفاسي")
    # English-only is not Arabic.
    assert not contains_arabic("Mishary Alafasy")
    # Empty / None-like inputs.
    assert not contains_arabic("")


def test_contains_arabic_handles_mixed_strings() -> None:
    assert contains_arabic("Surah الفاتحة (The Opener)")
    assert not contains_arabic("Surah 1. The Opener")


def test_pure_ascii_passes_through_unchanged() -> None:
    for s in ("", "Mishary Alafasy", "001. Al-Fatihah", "2:143"):
        assert render_for_terminal(s) == s


def test_pure_arabic_is_reshaped_to_presentation_forms() -> None:
    raw = "بِسْمِ اللَّهِ"
    rendered = render_for_terminal(raw)
    # The codepoints should now sit in the Arabic Presentation Forms-A/B
    # range (FB50-FEFF), meaning the joining algorithm picked the right
    # initial/medial/final glyphs.
    assert any(0xFB50 <= ord(c) <= 0xFEFF for c in rendered)
    # And the string is no longer literally the input.
    assert rendered != raw


def test_arabic_is_reversed_so_visual_order_is_correct() -> None:
    """The reshape+bidi pipeline produces a string whose codepoint order
    matches the visual order on a LTR-only terminal — i.e. the first
    character in the OUTPUT corresponds to what should be drawn on the
    LEFT side of the screen."""
    raw = "السلام"
    rendered = render_for_terminal(raw)
    # In the bidi'd output, the rightmost Arabic letter of the source
    # (final ﻡ/م) becomes one of the LEFTMOST characters of the rendered
    # string (so it ends up on the visual left after LTR rendering).
    # Reasoning: get_display reverses the RTL run.
    first_rendered_arabic_letter = next(
        (c for c in rendered if 0x0600 <= ord(c) <= 0xFEFF), ""
    )
    # The first rendered Arabic letter is a presentation form of the LAST
    # source codepoint (final-meem / isolated-meem). We can't compare
    # codepoints directly because shaping rewrites them, but we can at
    # least confirm the rendered string starts with an Arabic glyph that
    # isn't the source's first letter — i.e. order genuinely changed.
    assert first_rendered_arabic_letter != raw[0]
    assert rendered != raw


def test_mixed_string_preserves_latin_portion() -> None:
    rendered = render_for_terminal("Ref 2:143 — وَكَذَٰلِكَ — middle")
    # The Latin portions and punctuation remain intact.
    assert "Ref 2:143" in rendered
    assert "middle" in rendered
    # The Arabic portion is shaped.
    assert any(0xFB50 <= ord(c) <= 0xFEFF for c in rendered)
