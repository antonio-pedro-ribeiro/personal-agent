from personal_agent.kernel.models import Interests, RankHints, Topic
from personal_agent.kernel.services.ranker import KeywordRanker
from tests.conftest import make_item


def interests() -> Interests:
    return Interests(
        topics=[
            Topic(id="jazz", keywords=["jazz", "improv"], weight=0.9),
            Topic(id="ai", keywords=["LLM", "machine learning"], weight=0.6),
        ]
    )


def test_scores_by_best_topic_and_extra_matches() -> None:
    items = [
        make_item("a", "Improv jazz night"),
        make_item("b", "LLM meetup"),
        make_item("c", "Pottery class"),
    ]
    ranked = KeywordRanker().rank(items, interests(), RankHints(), [])
    assert [r.title for r in ranked] == ["Improv jazz night", "LLM meetup", "Pottery class"]
    assert ranked[0].score == 95.0  # 0.9*100 + 5 for the second keyword
    assert ranked[0].reason == "Matches topic 'jazz' (jazz, improv)"
    assert ranked[1].score == 60.0
    assert ranked[2].score == 0.0


def test_whole_word_matching() -> None:
    ranked = KeywordRanker().rank(
        [make_item("a", "Jazzercise class")], interests(), RankHints(), []
    )
    assert ranked[0].score == 0.0


def test_matches_summary_and_location() -> None:
    item = make_item("a", "Thursday night", summary="Live JAZZ trio")
    assert KeywordRanker().rank([item], interests(), RankHints(), [])[0].score == 90.0
