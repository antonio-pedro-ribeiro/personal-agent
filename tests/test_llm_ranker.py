from personal_agent.kernel.models import Interests, RankHints, Topic
from personal_agent.kernel.services.llm import Usage
from personal_agent.kernel.services.ranker import LLMRanker
from tests.conftest import make_item
from tests.fakes import FakeLLM

INTERESTS = Interests(topics=[Topic(id="jazz", description="Live jazz", weight=0.9)])
A, B, C = make_item("a", "Jazz trio"), make_item("b", "Pottery"), make_item("c", "Jazz trio (copy)")


def scores(*rows: tuple[str, float, str | None]) -> dict[str, object]:
    return {"scores": [{"id": i.ljust(10, "0"), "score": s, "reason": f"r{i}",
                        "duplicate_of": d and d.ljust(10, "0")} for i, s, d in rows]}  # fmt: skip


def test_scores_map_back_and_duplicates_collapse() -> None:
    llm = FakeLLM(scores(("a", 92, None), ("b", 10, None), ("c", 80, "a")))
    ranked = LLMRanker(llm).rank([A, B, C], INTERESTS, RankHints(rubric="be picky"), [])
    assert [(r.title, r.score) for r in ranked] == [("Jazz trio", 92), ("Pottery", 10)]
    assert ranked[0].reason == "ra" and ranked[0].also_at == [C.url]
    prompt = llm.calls[0]["prompt"]
    assert "Live jazz" in prompt and "be picky" in prompt and "<candidates" in prompt


def test_unscored_items_get_zero_and_scores_are_clamped() -> None:
    llm = FakeLLM(scores(("a", 140, None)))
    ranked = LLMRanker(llm).rank([A, B], INTERESTS, RankHints(), [])
    assert [(r.title, r.score, r.reason) for r in ranked] == [
        ("Jazz trio", 100, "ra"),
        ("Pottery", 0, "Not scored"),
    ]


def test_feedback_is_in_the_prompt() -> None:
    llm = FakeLLM(scores(("a", 50, None)))
    LLMRanker(llm).rank([A], INTERESTS, RankHints(), [(B, "rejected")])
    assert "- rejected: Pottery" in llm.calls[0]["prompt"]


def test_falls_back_to_keywords_on_error() -> None:
    ranker = LLMRanker(FakeLLM({}, fail=True))
    ranked = ranker.rank([A, B], INTERESTS, RankHints(), [])
    assert ranked[0].title == "Jazz trio" and ranked[0].score == 90
    assert "LLM ranking failed" in ranker.errors[0]


def test_large_batches_are_pretrimmed_to_50() -> None:
    items = [make_item(f"i{n:03d}", f"Jazz {n}" if n % 2 else f"Other {n}") for n in range(80)]
    llm = FakeLLM({"scores": []})
    LLMRanker(llm).rank(items, INTERESTS, RankHints(), [])
    assert llm.calls[0]["prompt"].count("<candidate>") == 50


def test_usage_cost() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=100_000, web_searches=3)
    assert u.cost_usd("claude-opus-5-5") == 4 + 2 + 0.03
    assert u.cost_usd("unknown-model") is None
