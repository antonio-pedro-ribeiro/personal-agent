from pathlib import Path

from personal_agent.kernel.config import load_config

INTERESTS = """# Interests
## Profile
- city: Lisbon
- coordinates: 38.72, -9.14
- timezone: Europe/Lisbon
- max_km: 15
- max_price: 60
- time_windows: weekday_evenings, weekends, mornings
- max_events_per_week: 4

## Topics
- [x] **ai** — AI talks
  - keywords: LLM, machine learning
  - weight: 0.8
  - plugins: events, News
- [x] **jazz** — Jazz
- [ ] **football** — paused
- [x] **bad** — wrong weight
  - weight: 3
- [x] no id here

## Dislikes
- [x] tribute bands
- [ ] comedy
"""

SOURCES = """# Sources
- [x] **orphan** — rss

## Events
- [x] **web** — web_search
  - max_queries: 5
- [ ] **cal** — ical
  - url: cal.ics
- [x] **web** — ical
- [x] **notype**

## News
- [x] **hn** — hackernews
"""

OUTPUTS = """# Outputs
- [x] **digest** — markdown_digest
  - plugins: events, news
  - path: digests
- [ ] **tasks** — google_tasks
  - min_score: 80
  - list_name: Agent
"""


def write(
    tmp: Path, interests: str = INTERESTS, sources: str = SOURCES, outputs: str = OUTPUTS
) -> Path:
    (tmp / "interests.md").write_text(interests, encoding="utf-8")
    (tmp / "sources.md").write_text(sources, encoding="utf-8")
    (tmp / "outputs.md").write_text(outputs, encoding="utf-8")
    return tmp


def test_profile(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path))
    p = cfg.interests.profile
    assert p.city == "Lisbon"
    assert p.coordinates == (38.72, -9.14)
    assert p.timezone == "Europe/Lisbon"
    assert (p.max_km, p.max_price, p.max_events_per_week) == (15, 60, 4)
    assert p.time_windows == ["weekday_evenings", "weekends"]
    assert any("unknown time window 'mornings'" in w for w in cfg.warnings)


def test_topics_defaults_and_validation(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path))
    topics = {t.id: t for t in cfg.interests.topics}
    assert set(topics) == {"ai", "jazz", "football"}
    assert topics["ai"].plugins == ["events", "news"]
    assert topics["ai"].keywords == ["LLM", "machine learning"]
    assert topics["jazz"].weight == 0.5 and topics["jazz"].plugins is None
    assert topics["football"].enabled is False
    assert any("between 0 and 1" in w for w in cfg.warnings)
    assert any("topic needs an id" in w for w in cfg.warnings)


def test_for_plugin_filters_topics_and_dislikes(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path))
    news = cfg.interests.for_plugin("news")
    assert [t.id for t in news.topics] == ["ai", "jazz"]  # jazz applies to all plugins
    assert [d.text for d in news.dislikes] == ["tribute bands"]


def test_sources(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path))
    by_id = {s.id: s for s in cfg.sources}
    assert set(by_id) == {"web", "cal", "hn"}
    assert by_id["web"].plugin == "events" and by_id["web"].type == "web_search"
    assert by_id["web"].attrs == {"max_queries": "5"}
    assert by_id["cal"].enabled is False
    assert by_id["hn"].plugin == "news"
    assert by_id["web"].origin.endswith("sources.md:5")
    joined = "\n".join(cfg.warnings)
    assert "outside a '## <Plugin>' section" in joined
    assert "duplicate source id 'web'" in joined
    assert "source needs '**id** — type'" in joined


def test_outputs(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path))
    digest, tasks = cfg.outputs
    assert (digest.id, digest.type, digest.plugins) == (
        "digest",
        "markdown_digest",
        ["events", "news"],
    )
    assert digest.attrs == {"path": "digests"}
    assert tasks.enabled is False and tasks.min_score == 80
    assert tasks.attrs == {"list_name": "Agent"}


def test_missing_files_are_warnings(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.sources == [] and cfg.outputs == []
    assert sum("file not found" in w for w in cfg.warnings) == 3


def test_unknown_timezone_falls_back_to_utc(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, interests="## Profile\n- timezone: Mars/Olympus\n"))
    assert cfg.interests.profile.timezone == "UTC"
    assert any("unknown timezone" in w for w in cfg.warnings)
