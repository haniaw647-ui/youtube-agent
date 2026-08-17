from src.workers.stages.topic_generation import score_candidate


def test_score_rewards_interest_and_uniqueness():
    high = {"estimated_interest": 90, "uniqueness_score": 80, "difficulty": 10, "evergreen": False}
    low = {"estimated_interest": 20, "uniqueness_score": 10, "difficulty": 10, "evergreen": False}
    assert score_candidate(high) > score_candidate(low)


def test_score_penalizes_difficulty():
    easy = {"estimated_interest": 50, "uniqueness_score": 50, "difficulty": 10, "evergreen": False}
    hard = {"estimated_interest": 50, "uniqueness_score": 50, "difficulty": 90, "evergreen": False}
    assert score_candidate(easy) > score_candidate(hard)


def test_evergreen_bonus_applied():
    base = {"estimated_interest": 50, "uniqueness_score": 50, "difficulty": 50, "evergreen": False}
    evergreen = {**base, "evergreen": True}
    assert score_candidate(evergreen) == score_candidate(base) + 10


def test_missing_fields_default_to_zero():
    assert score_candidate({}) == 0.0
