"""Markdown checklist parser and renderer.

Shared by the config loader and digest-style outputs so both agree on syntax (spec §3).

Grammar (everything else is ignored as free text):

- YAML frontmatter at the top (``---`` ... ``---``) is skipped.
- ``#``..``######`` headings open a section.
- A top-level bullet ``- [x] **id** — text`` (or ``- [ ] ...``) is a checklist item.
  The ``**id** —`` part is optional (e.g. dislikes are plain text).
- A top-level bullet ``- key: value`` without a checkbox is a section property.
- An indented bullet ``- key: value`` under an item is an attribute of that item.
- `` ← comment`` at the end of an item line or attribute value is stripped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TOP_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_NESTED_BULLET = re.compile(r"^(?:\t+| {2,})[-*+]\s+(.*)$")
_CHECKBOX = re.compile(r"^\[([ xX])\]\s+(.*)$")
_ID_TEXT = re.compile(r"^\*\*(?P<id>[^*]+)\*\*\s*(?:[\u2014\u2013-]+\s*(?P<text>.*))?$")
_KEY_VALUE = re.compile(r"^(?P<key>[A-Za-z_][\w .-]*?)\s*:\s*(?P<value>.*)$")
_COMMENT = re.compile(r"\s+←.*$")


@dataclass
class ChecklistItem:
    checked: bool
    text: str
    line: int
    id: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Section:
    title: str
    level: int
    line: int
    items: list[ChecklistItem] = field(default_factory=list)
    props: dict[str, str] = field(default_factory=dict)


@dataclass
class Document:
    filename: str
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)

    def section(self, title: str) -> Section | None:
        wanted = title.strip().lower()
        return next((s for s in self.sections if s.title.strip().lower() == wanted), None)

    def all_items(self) -> list[ChecklistItem]:
        return [item for s in self.sections for item in s.items]


def _strip_comment(text: str) -> str:
    return _COMMENT.sub("", text).strip()


def _skip_frontmatter(lines: list[str]) -> int:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                return i + 1
    return 0


def parse(text: str, filename: str = "<string>") -> Document:
    """Parse markdown text into sections, checklist items and properties."""
    lines = text.splitlines()  # handles \n, \r\n and \r
    root = Section(title="", level=0, line=0)
    doc = Document(filename=filename, sections=[root])
    current_item: ChecklistItem | None = None

    for idx in range(_skip_frontmatter(lines), len(lines)):
        raw = lines[idx]
        lineno = idx + 1
        if not raw.strip():
            continue

        if m := _HEADING.match(raw):
            doc.sections.append(Section(title=m.group(2), level=len(m.group(1)), line=lineno))
            current_item = None
            continue

        section = doc.sections[-1]

        if m := _NESTED_BULLET.match(raw):
            if current_item is None:
                continue
            kv = _KEY_VALUE.match(m.group(1).strip())
            if kv:
                current_item.attrs[kv.group("key").strip().lower()] = _strip_comment(
                    kv.group("value")
                )
            else:
                doc.warnings.append(
                    f"{filename}:{lineno}: ignored nested line (expected '- key: value')"
                )
            continue

        if m := _TOP_BULLET.match(raw):
            body = m.group(1).strip()
            if cb := _CHECKBOX.match(body):
                checked = cb.group(1).lower() == "x"
                content = _strip_comment(cb.group(2))
                item = ChecklistItem(checked=checked, text=content, line=lineno)
                if idm := _ID_TEXT.match(content):
                    item.id = idm.group("id").strip()
                    item.text = (idm.group("text") or "").strip()
                elif content.startswith("**"):
                    doc.warnings.append(f"{filename}:{lineno}: malformed item id: {content!r}")
                section.items.append(item)
                current_item = item
                continue
            if kv := _KEY_VALUE.match(body):
                section.props[kv.group("key").strip().lower()] = _strip_comment(kv.group("value"))
            current_item = None
            continue

        # Free text: ends the current item's attribute block.
        current_item = None

    return doc


def as_list(value: str | None) -> list[str]:
    """Split a comma-separated attribute value into a list of trimmed, non-empty strings."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


# --- Rendering ----------------------------------------------------------------------------


def render_item(title: str, body: str, url: str, short_id: str) -> str:
    """Render one digest entry with 👍/👎 feedback boxes."""
    parts = [f"- **{_escape(title)}**"]
    if body:
        parts.append(f" — {_escape(body)}")
    if url:
        parts.append(f" · [source]({url})")
    parts.append(f" `id:{short_id}`")
    return "".join(parts) + "\n  - [ ] 👍\n  - [ ] 👎\n"


def _escape(text: str) -> str:
    return text.replace("*", r"\*").replace("\n", " ").strip()


ID_MARKER = re.compile(r"`id:([0-9a-f]+)`")

_FEEDBACK_BOX = re.compile(r"^(?:\t+| {2,})[-*+]\s+\[([ xX])\]\s*(👍|👎)")


Vote = Literal["up", "down"]


def parse_feedback(text: str) -> dict[str, Vote]:
    """Ticked boxes in digest entries (see ``render_item``): short id → ``up`` or ``down``.

    An entry with both boxes ticked is ambiguous and left out; so are entries with none.
    Accepts CRLF and ``[X]``.
    """
    ticks: dict[str, set[Vote]] = {}
    current: str | None = None
    for line in text.splitlines():
        if _TOP_BULLET.match(line):
            ids = ID_MARKER.findall(line)
            current = ids[-1] if ids else None
            continue
        if current is None:
            continue
        if m := _FEEDBACK_BOX.match(line):
            if m.group(1) != " ":
                ticks.setdefault(current, set()).add("up" if m.group(2) == "👍" else "down")
        elif line.strip() and not _NESTED_BULLET.match(line):
            current = None  # free text or a heading ends the entry
    return {short: next(iter(s)) for short, s in ticks.items() if len(s) == 1}
