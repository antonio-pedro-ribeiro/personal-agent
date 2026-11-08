"""Markdown digest output: ``<path>/YYYY-MM-DD.md`` with one section per content plugin.

Idempotent: items already present in the day's file (by ``id:`` marker) are not added again.
Only writes inside its own folder (``path`` attribute, default ``digests``; relative paths
resolve against the current working directory).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from personal_agent.kernel.context import RunContext
from personal_agent.kernel.models import Delivery, Feedback, OutputConfig, RankedItem
from personal_agent.kernel.services.checklist import ID_MARKER, render_item

DEFAULT_DIR = "digests"
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_when(item: RankedItem, ctx: RunContext) -> str:
    if item.start is None:
        return ""
    local = item.start.astimezone(ctx.tz)
    day = f"{_WEEKDAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]}"
    return day if item.extra.get("all_day") else f"{day} {local:%H:%M}"


def render_entry(item: RankedItem, ctx: RunContext) -> str:
    body = " · ".join(filter(None, [format_when(item, ctx), item.location, item.reason]))
    return render_item(item.title, body, item.url, item.short_id)


def render_sections(items: Sequence[RankedItem], ctx: RunContext) -> dict[str, str]:
    """Heading → markdown block, in input order of plugins."""
    sections: dict[str, str] = {}
    for item in items:
        heading = ctx.plugin_titles.get(item.plugin, item.plugin.title())
        sections[heading] = sections.get(heading, "") + render_entry(item, ctx)
    return sections


def insert_block(text: str, heading: str, block: str) -> str:
    """Append ``block`` at the end of section ``## heading``, creating it if missing."""
    lines = text.splitlines(keepends=True)
    marker = f"## {heading}"
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == marker)
    except StopIteration:
        sep = "" if text.endswith("\n\n") or not text else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{sep}{marker}\n\n{block}"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    head = "".join(lines[:end])
    if not head.endswith("\n"):
        head += "\n"
    tail = "".join(lines[end:])
    return head + block + ("\n" + tail.lstrip("\n") if tail.strip() else "")


class MarkdownDigestOutput:
    type = "markdown_digest"
    google_scopes: list[str] = []  # noqa: RUF012 - protocol attribute

    def accepts(self, item: RankedItem) -> bool:
        return True

    def path_for(self, cfg: OutputConfig, ctx: RunContext) -> Path:
        return Path(cfg.attrs.get("path") or DEFAULT_DIR) / f"{ctx.today.isoformat()}.md"

    def publish(
        self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext
    ) -> list[Delivery]:
        path = self.path_for(cfg, ctx)
        text = (
            path.read_text(encoding="utf-8")
            if path.is_file()
            else f"# Digest {ctx.today.isoformat()}\n"
        )
        present = set(ID_MARKER.findall(text))
        new_items = [i for i in items if i.short_id not in present]
        if new_items:
            for heading, block in render_sections(new_items, ctx).items():
                text = insert_block(text, heading, block)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        ref = path.as_posix()
        return [
            Delivery(item_id=i.id, output_id=cfg.id, external_ref=f"{ref}#{i.short_id}")
            for i in items
        ]

    def sync_feedback(
        self, deliveries: Sequence[Delivery], cfg: OutputConfig, ctx: RunContext
    ) -> list[Feedback]:
        return []  # ticked 👍/👎 boxes are read back in M3

    def preview(self, items: Sequence[RankedItem], cfg: OutputConfig, ctx: RunContext) -> str:
        blocks = [f"## {h}\n\n{b}" for h, b in render_sections(items, ctx).items()]
        return f"→ {self.path_for(cfg, ctx).as_posix()}\n\n" + "\n".join(blocks)
