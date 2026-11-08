from personal_agent.kernel.services.checklist import ID_MARKER, as_list, parse, render_item

SAMPLE = """---
tags: [agent]
---
# Interests

Some free text that should be ignored.

## Profile
- city: Lisbon
- timezone: Europe/Lisbon

## Topics
- [x] **ai** — Technical AI; skip hype
  - keywords: LLM, machine learning
  - weight: 0.8
- [ ] **football** — Benfica matches   ← paused, kept for later
  - weight: 0.3
Paragraph text ends the attribute block.
  - orphan: ignored

## Dislikes
- [x] networking mixers
- [ ] stand-up comedy
"""


def test_sections_props_and_items() -> None:
    doc = parse(SAMPLE, "interests.md")
    profile = doc.section("profile")
    assert profile is not None
    assert profile.props == {"city": "Lisbon", "timezone": "Europe/Lisbon"}

    topics = doc.section("Topics")
    assert topics is not None
    ai, football = topics.items
    assert (ai.id, ai.checked, ai.text) == ("ai", True, "Technical AI; skip hype")
    assert ai.attrs == {"keywords": "LLM, machine learning", "weight": "0.8"}
    assert (football.id, football.checked, football.text) == ("football", False, "Benfica matches")
    assert football.attrs == {"weight": "0.3"}


def test_plain_text_items_and_frontmatter_ignored() -> None:
    doc = parse(SAMPLE, "interests.md")
    dislikes = doc.section("Dislikes")
    assert dislikes is not None
    assert [(i.id, i.text, i.checked) for i in dislikes.items] == [
        (None, "networking mixers", True),
        (None, "stand-up comedy", False),
    ]
    assert all("tags" not in s.props for s in doc.sections)


def test_crlf_parses_the_same() -> None:
    crlf = SAMPLE.replace("\n", "\r\n")
    a, b = parse(SAMPLE), parse(crlf)
    assert [(i.id, i.attrs) for i in a.all_items()] == [(i.id, i.attrs) for i in b.all_items()]


def test_line_numbers_and_warnings() -> None:
    text = "## Events\n- [x] **web** — web_search\n  - this has no colon\n- [x] **broken — x\n"
    doc = parse(text, "sources.md")
    assert doc.all_items()[0].line == 2
    assert any("sources.md:3" in w for w in doc.warnings)
    assert any("sources.md:4" in w and "malformed" in w for w in doc.warnings)


def test_comment_stripped_from_attribute_values() -> None:
    doc = parse("## Events\n- [x] **a** — web_page\n  - url: TBD   ← confirm\n")
    assert doc.all_items()[0].attrs["url"] == "TBD"


def test_as_list() -> None:
    assert as_list(" a, b ,, c ") == ["a", "b", "c"]
    assert as_list(None) == []


def test_render_item_roundtrips_id_marker() -> None:
    text = render_item("Jazz *night*", "Thu 12 Nov", "https://x.test", "abc123def0")
    assert text.startswith("- **Jazz \\*night\\*** — Thu 12 Nov · [source](https://x.test)")
    assert "  - [ ] 👍\n  - [ ] 👎\n" in text
    assert ID_MARKER.findall(text) == ["abc123def0"]


def test_render_item_without_url() -> None:
    assert "[source]" not in render_item("T", "", "", "abc")
