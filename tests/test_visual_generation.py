"""Real bug, caught on the first genuine live job that fell back to
Pixabay: visual_note wasn't truncated the same way narration was, and
Pixabay's API rejects a search query over 100 chars with a hard 400 —
Pexels tolerates longer queries, which is why this was latent until then.
"""

from src.workers.stages.visual_generation import (
    _MAX_QUERY_LENGTH,
    _visual_query_for_segment,
)


def test_long_visual_note_is_truncated():
    segment = {"visual_note": "x" * 150, "narration": "short narration"}
    query = _visual_query_for_segment(segment)
    assert len(query) == _MAX_QUERY_LENGTH
    assert query == "x" * _MAX_QUERY_LENGTH


def test_long_narration_fallback_is_truncated_when_no_visual_note():
    segment = {"narration": "y" * 150}
    query = _visual_query_for_segment(segment)
    assert len(query) == _MAX_QUERY_LENGTH


def test_short_visual_note_is_used_as_is():
    segment = {"visual_note": "a mountain lake", "narration": "irrelevant"}
    assert _visual_query_for_segment(segment) == "a mountain lake"
