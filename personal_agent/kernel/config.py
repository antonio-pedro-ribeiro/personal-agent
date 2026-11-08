"""Load ``interests.md``, ``sources.md`` and ``outputs.md`` into typed config (spec §3).

Invalid items are skipped with a ``file:line`` warning; a missing file is a warning, not an
error, so a partial setup still runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_agent.kernel.models import (
    Config,
    Dislike,
    Interests,
    OutputConfig,
    Profile,
    Source,
    Topic,
)
from personal_agent.kernel.services import checklist
from personal_agent.kernel.services.checklist import Document, as_list

INTERESTS_FILE = "interests.md"
SOURCES_FILE = "sources.md"
OUTPUTS_FILE = "outputs.md"

KNOWN_TIME_WINDOWS = {"weekday_evenings", "weekends"}


def default_config_dir() -> Path:
    return Path(os.environ.get("AGENT_CONFIG_DIR", "config"))


def load_config(config_dir: Path) -> Config:
    warnings: list[str] = []
    docs: dict[str, Document | None] = {}
    for name in (INTERESTS_FILE, SOURCES_FILE, OUTPUTS_FILE):
        path = config_dir / name
        if not path.is_file():
            warnings.append(f"{path}: file not found")
            docs[name] = None
            continue
        doc = checklist.parse(path.read_text(encoding="utf-8"), filename=str(path))
        warnings.extend(doc.warnings)
        docs[name] = doc

    interests_doc = docs[INTERESTS_FILE]
    sources_doc = docs[SOURCES_FILE]
    outputs_doc = docs[OUTPUTS_FILE]
    interests = parse_interests(interests_doc, warnings) if interests_doc else Interests()
    sources = parse_sources(sources_doc, warnings) if sources_doc else []
    outputs = parse_outputs(outputs_doc, warnings) if outputs_doc else []
    return Config(interests=interests, sources=sources, outputs=outputs, warnings=warnings)


# --- interests.md -------------------------------------------------------------------------


def _float(value: str | None, where: str, key: str, warnings: list[str]) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        warnings.append(f"{where}: '{key}' must be a number, got {value!r}")
        return None


def parse_profile(props: dict[str, str], where: str, warnings: list[str]) -> Profile:
    profile = Profile()
    known = {
        "city",
        "coordinates",
        "timezone",
        "max_km",
        "max_price",
        "time_windows",
        "max_events_per_week",
        "max_news_items",
    }
    profile.city = props.get("city") or None

    if coords := props.get("coordinates"):
        parts = as_list(coords)
        try:
            lat, lon = (float(p) for p in parts)
            profile.coordinates = (lat, lon)
        except ValueError:
            warnings.append(f"{where}: 'coordinates' must be 'lat, lon', got {coords!r}")

    if tz := props.get("timezone"):
        try:
            ZoneInfo(tz)
            profile.timezone = tz
        except (ZoneInfoNotFoundError, ValueError):
            warnings.append(f"{where}: unknown timezone {tz!r}; using UTC")
    else:
        warnings.append(f"{where}: no 'timezone' in Profile; using UTC")

    profile.max_km = _float(props.get("max_km"), where, "max_km", warnings)
    profile.max_price = _float(props.get("max_price"), where, "max_price", warnings)

    windows = as_list(props.get("time_windows"))
    for w in windows:
        if w not in KNOWN_TIME_WINDOWS:
            warnings.append(f"{where}: unknown time window {w!r} (ignored)")
    profile.time_windows = [w for w in windows if w in KNOWN_TIME_WINDOWS]

    for key in ("max_events_per_week", "max_news_items"):
        value = _float(props.get(key), where, key, warnings)
        if value is not None:
            setattr(profile, key, int(value))

    profile.extra = {k: v for k, v in props.items() if k not in known}
    return profile


def parse_interests(doc: Document, warnings: list[str]) -> Interests:
    interests = Interests()

    profile_section = doc.section("Profile")
    if profile_section:
        where = f"{doc.filename}:{profile_section.line}"
        interests.profile = parse_profile(profile_section.props, where, warnings)
    else:
        warnings.append(f"{doc.filename}: no '## Profile' section; using defaults")

    topics_section = doc.section("Topics")
    for item in topics_section.items if topics_section else []:
        where = f"{doc.filename}:{item.line}"
        if not item.id:
            warnings.append(f"{where}: topic needs an id like '**jazz** — description'")
            continue
        weight = _float(item.attrs.get("weight"), where, "weight", warnings)
        if weight is not None and not 0 <= weight <= 1:
            warnings.append(f"{where}: 'weight' must be between 0 and 1; topic skipped")
            continue
        plugins = as_list(item.attrs.get("plugins")) or None
        interests.topics.append(
            Topic(
                id=item.id,
                description=item.text,
                enabled=item.checked,
                keywords=as_list(item.attrs.get("keywords")),
                weight=0.5 if weight is None else weight,
                plugins=[p.lower() for p in plugins] if plugins else None,
            )
        )

    dislikes_section = doc.section("Dislikes")
    for item in dislikes_section.items if dislikes_section else []:
        text = item.id or item.text
        if text:
            interests.dislikes.append(Dislike(text=text, enabled=item.checked))
    return interests


# --- sources.md ---------------------------------------------------------------------------


def _type_of(text: str) -> str:
    return text.split()[0].lower() if text.split() else ""


def parse_sources(doc: Document, warnings: list[str]) -> list[Source]:
    sources: list[Source] = []
    seen: set[str] = set()
    for section in doc.sections:
        for item in section.items:
            where = f"{doc.filename}:{item.line}"
            if section.level < 2 or not section.title:
                warnings.append(f"{where}: source outside a '## <Plugin>' section (ignored)")
                continue
            if not item.id or not _type_of(item.text):
                warnings.append(f"{where}: source needs '**id** — type'")
                continue
            if item.id in seen:
                warnings.append(f"{where}: duplicate source id {item.id!r} (ignored)")
                continue
            seen.add(item.id)
            sources.append(
                Source(
                    id=item.id,
                    plugin=section.title.strip().lower(),
                    type=_type_of(item.text),
                    enabled=item.checked,
                    attrs=dict(item.attrs),
                    origin=where,
                )
            )
    return sources


# --- outputs.md ---------------------------------------------------------------------------


def parse_outputs(doc: Document, warnings: list[str]) -> list[OutputConfig]:
    outputs: list[OutputConfig] = []
    seen: set[str] = set()
    for item in doc.all_items():
        where = f"{doc.filename}:{item.line}"
        if not item.id or not _type_of(item.text):
            warnings.append(f"{where}: output needs '**id** — type'")
            continue
        if item.id in seen:
            warnings.append(f"{where}: duplicate output id {item.id!r} (ignored)")
            continue
        seen.add(item.id)
        attrs = dict(item.attrs)
        plugins = as_list(attrs.pop("plugins", None)) or None
        min_score = _float(attrs.pop("min_score", None), where, "min_score", warnings)
        outputs.append(
            OutputConfig(
                id=item.id,
                type=_type_of(item.text),
                enabled=item.checked,
                plugins=[p.lower() for p in plugins] if plugins else None,
                min_score=min_score or 0.0,
                attrs=attrs,
                origin=where,
            )
        )
    return outputs
