from src.workers.stages.thumbnail_generation import (
    _GENERIC_FALLBACK_QUERY,
    _keyword_query,
    _thumbnail_search_queries,
)


def test_keyword_query_strips_stopwords_and_parens():
    title = "I Let Captchas Decide My Day (Click All the Squares With Traffic Lights)"
    assert _keyword_query(title) == "Captchas Decide Day"


def test_thumbnail_search_queries_includes_title_hook_keywords_and_generic_fallback():
    title = "I Let Captchas Decide My Day (Click All the Squares With Traffic Lights)"
    hook = "Every captcha I solve becomes a task I have to complete in real life."
    queries = _thumbnail_search_queries(title, hook)
    assert queries[0] == title
    assert queries[1] == hook
    assert queries[2] == "Captchas Decide Day"
    assert queries[-1] == _GENERIC_FALLBACK_QUERY


def test_thumbnail_search_queries_dedupes_and_skips_empty_hook():
    title = "Short Title"
    queries = _thumbnail_search_queries(title, None)
    assert queries == ["Short Title", _GENERIC_FALLBACK_QUERY]
