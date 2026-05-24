from quran_tui.models import (
    Category,
    Track,
    category_matches_filter,
    format_duration,
    track_matches_filter,
)


def test_format_duration() -> None:
    assert format_duration(None) == "--:--"
    assert format_duration(-1) == "--:--"
    assert format_duration(0) == "0:00"
    assert format_duration(125) == "2:05"
    assert format_duration(3723) == "1:02:03"


def test_track_display_subtitle_combines_subtitle_and_extra() -> None:
    t = Track(track_id="x", title="Al-Fatihah", subtitle="Mishary", extra="Makkah")
    assert t.display_subtitle == "Mishary · Makkah"


def test_track_display_subtitle_omits_empty_fields() -> None:
    t = Track(track_id="x", title="Al-Fatihah", subtitle="Mishary")
    assert t.display_subtitle == "Mishary"


def test_track_matches_filter_covers_title_subtitle_extra() -> None:
    t = Track(track_id="x", title="Al-Fatihah", subtitle="Mishary", extra="Makkah")
    assert track_matches_filter(t, "fatih")
    assert track_matches_filter(t, "MISHARY")
    assert track_matches_filter(t, "Makkah")
    assert track_matches_filter(t, "  ")  # blank == match-all
    assert not track_matches_filter(t, "nasheed")


def test_category_matches_filter() -> None:
    c = Category(key="k", title="Hafs Recitations", subtitle="Standard riwayah")
    assert category_matches_filter(c, "hafs")
    assert category_matches_filter(c, "riwayah")
    assert not category_matches_filter(c, "warsh")
