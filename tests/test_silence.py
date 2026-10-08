from app.core.m4b import Chapter, snap_to_silences


def _chapters(starts, total):
    return [Chapter(s, starts[i + 1] if i + 1 < len(starts) else total, f"C{i}") for i, s in enumerate(starts)]


def test_snaps_to_long_nearby_pause_not_to_short_or_far_ones():
    total = 6 * 3600
    expected = _chapters([0, 600, 3000], total)          # Audible's (scaled) chapter starts
    silences = [(590, 591), (640, 643.2),                 # near 600: short close pause vs long pause 40 s later
                (2700, 2704.5),                           # very long pause, but 5 min before 3000
                (2995, 2997.5)]                           # good pause right at 3000
    out = snap_to_silences(expected, silences, total)
    assert [round(c.start) for c in out] == [0, 643, 2997]
    assert out[-1].end == total and out[0].end == out[1].start


def test_pause_at_a_file_join_wins_a_close_call():
    total = 3600
    expected = _chapters([0, 1000], total)
    silences = [(995, 997.0), (1010, 1011.9)]
    # a chapter starts half a second before the voice comes back
    assert snap_to_silences(expected, silences, total)[1].start == 996.5
    assert snap_to_silences(expected, silences, total, joins=[1011])[1].start == 1011.4


def test_gives_up_without_pauses():
    total = 3600
    expected = _chapters([0, 600, 1200, 1800, 2400], total)
    assert snap_to_silences(expected, [], total) is None
