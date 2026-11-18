"""What an agent should know first about the user's research (07 §4.7, 08 §1.5)."""

# grounded-in: libs/research/reference/research.md (keel_help topic "research").
#
# A projection, never stored: each list with its bound and its total; every
# `next` is generated from the registry this caller is served (AT-14).

from ._output_schemas import RESEARCH_INPUT_SCHEMAS
from ._research_commands import call, register_research, research_url, study_result
from ._research_view import Keep, _cap, _day, next_call


REQUIRED_ACTION = "backtest.research.read"
DESCRIPTION = (
    "Use when research work starts and what the user has already studied is needed; not for "
    "one study's numbers (keel_research_get). Returns projects, studies, "
    "versions tried, set-aside and planned studies, the latest notes and paper and live runs, "
    "each with its total."
)

_SCHEMA = RESEARCH_INPUT_SCHEMAS["keel_research_brief"]
PROPERTIES = dict(_SCHEMA["properties"])


#: A note body in the brief, at most this many characters (WL-AG-01): the server serves each body whole (up to 8,192
#: characters, ten notes), so an uncapped brief ran past rule 10's 8,000 characters on notes alone.
NOTE_BODY_MAX = 200


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _body(text: object) -> str:
    """A note body for the brief: whole when short, else cut at a word before :data:`NOTE_BODY_MAX` with its
    length said, so a cut is never read as the whole note."""
    body = " ".join(str(text or "").split())
    if len(body) <= NOTE_BODY_MAX:
        return f'"{body}"'
    cut = body[:NOTE_BODY_MAX].rsplit(" ", 1)[0]
    return f'"{cut}…" (the first {len(cut):,} of {len(body):,} characters)'


def _total(env: dict, key: str, rows: list) -> int:
    value = env.get(key)
    return int(value) if isinstance(value, int) else len(rows)


def brief_text(env: dict, ctx) -> str:
    """07 §4.7's text over keel-api's brief (``research.brief.build_brief``, projected by the route): each list
    with its total, then its rows by id, then the calls that act on them.

    RI-615: the rows are read by the server's names, ``id`` and ``by`` (the brief once printed ``- None · Check ·
    …`` and ``note (decision, None)`` because it read ``study_id`` and ``author_role``, and the golden that pinned it
    was built from a fixture with those names); projects, set-aside, planned studies and families are rows with
    their ids, not only counts; note bodies are capped (:func:`_body`); and the whole text is held to 8,000
    characters by :func:`_research_view._cap`, which never drops a next call."""
    studies = [s for s in env.get("studies") or [] if isinstance(s, dict)]
    projects = [p for p in env.get("projects") or [] if isinstance(p, dict)]
    set_aside = [r for r in env.get("set_aside") or [] if isinstance(r, dict)]
    planned = [r for r in env.get("planned") or [] if isinstance(r, dict)]
    notes = [n for n in env.get("notes") or [] if isinstance(n, dict)]
    families = [f for f in env.get("versions_tried") or [] if isinstance(f, dict)]
    deployed = [d for d in env.get("paper_and_live") or [] if isinstance(d, dict)]
    n_studies = _total(env, "studies_total", studies)
    n_projects = _total(env, "projects_total", projects)
    n_set_aside = _total(env, "set_aside_total", set_aside)
    n_notes = _total(env, "notes_total", notes)
    n_families = _total(env, "families_total", families)
    n_deployed = _total(env, "paper_and_live_total", deployed)
    by_state = env.get("by_state") or {}
    states = ", ".join(
        f"{n:,} {str(k).replace('_', ' ')}" for k, n in sorted(by_state.items()) if n
    )
    lines: list[str] = [
        "Research brief: "
        + ", ".join(
            (
                _plural(n_studies, "study", "studies") + (f" ({states})" if states else ""),
                _plural(n_projects, "project"),
                f"{n_set_aside:,} set aside",
                _plural(n_notes, "note"),
                _plural(n_families, "family", "families") + " with versions tried",
                f"{n_deployed:,} on paper or live",
            )
        )
    ]
    aside = {str(r.get("study")) for r in set_aside}
    if studies or n_studies:
        lines.append(f"Studies: {len(studies[:25])} shown of {n_studies:,}")
    for study in studies[:25]:
        label = (study.get("kind_label") or {}).get("short", "Study")
        # AGENT F2 (SERVE 63cc77014e): the state in its served words ("Done", "Planned"), then the card's bars line
        # ("Meets 7 of 9 of your bars") or, for a study with none, its headline.
        state = study.get("state_words") or str(study.get("state") or "").replace("_", " ")
        row = f"- {study.get('id')} · {label} · {study.get('title')} · {state}"
        lead = study.get("bars_met") or study.get("headline")
        if isinstance(lead, str) and lead:
            row += f" · {lead}"
        subjects = [str(x) for x in study.get("subjects") or []]
        if subjects:
            row += (
                " · "
                + ", ".join(subjects[:3])
                + (f" and {len(subjects) - 3} more" if len(subjects) > 3 else "")
            )
        if study.get("id") in aside:
            row += " · set aside"
        lines.append(row)
    if projects or n_projects:
        lines.append(f"Projects: {len(projects)} shown of {n_projects:,}")
    for project in projects:
        held = [str(x) for x in project.get("studies") or []]
        total = int(project.get("studies_total") or len(held))
        row = f"- {project.get('project_id')} · {project.get('name')}"
        if project.get("question"):
            row += f": {project['question']}"
        row += f" · {_plural(total, 'study', 'studies')}"
        if held:
            row += (
                " (" + ", ".join(held[:5]) + (f" and {total - 5} more" if total > 5 else "") + ")"
            )
        if project.get("subjects"):
            row += " · " + ", ".join(str(x) for x in project["subjects"][:3])
        lines.append(row)
    if set_aside or n_set_aside:
        lines.append(f"Set aside: {len(set_aside)} shown of {n_set_aside:,}")
    for r in set_aside:
        lines.append(
            f"- {r.get('study')} · {r.get('what')}: {_body(r.get('why'))} ({r.get('by')}, {_day(r.get('when'))})"
        )
    listed = {str(s.get("id")) for s in studies[:25]}
    n_planned = _total(env, "planned_total", planned)
    unlisted = [r for r in planned if str(r.get("id")) not in listed]
    if planned or n_planned:
        line = f"Planned, not run yet: {len(planned)} shown of {n_planned:,}"
        lines.append(
            line + (" (every one listed under Studies)" if planned and not unlisted else "")
        )
    for r in unlisted:
        lines.append(f"- {r.get('id')} · {r.get('title')}")
    if families or n_families:
        lines.append(
            f"Versions tried: {len(families)} shown of {_plural(n_families, 'family', 'families')}"
        )
    for f in families:
        count = f.get("count")
        lines.append(
            f"- {f.get('family')}: {'unknown' if count is None else f'{count:,}'} since {_day(f.get('since'))}"
        )
    if deployed or n_deployed:
        lines.append(f"Paper and live: {len(deployed)} shown of {n_deployed:,}")
    for d in deployed:
        lines.append(
            f"- {d.get('deployment')} · {d.get('name')} · {d.get('mode')} since {_day(d.get('since'))}"
        )
    if notes or n_notes:
        lines.append(f"Notes: {len(notes)} shown of {n_notes:,}, newest first")
    for n in notes:
        anchor = n.get("anchor") or {}
        on = f" on {anchor.get('kind')} {anchor.get('id')}" if anchor.get("id") else ""
        lines.append(
            f"- {str(n.get('kind') or '').replace('_', ' ')}{on} ({n.get('by')}, {_day(n.get('when'))}): {_body(n.get('body'))}"
        )
    pagination = env.get("pagination") or {}
    if pagination.get("has_more") and pagination.get("cursor"):
        lines.append(
            Keep(
                "More studies: "
                + next_call("keel_research_brief", {"cursor": pagination["cursor"]})
            )
        )
    first = next((str(s["id"]) for s in studies if s.get("id")), None)
    if first:
        lines.append(Keep("Read a study: " + next_call("keel_research_get", {"study_id": first})))
    project = next((str(p["project_id"]) for p in projects if p.get("project_id")), None)
    if project:
        lines.append(
            Keep("Read a project: " + next_call("keel_research_get", {"project_id": project}))
        )
    return _cap(lines, f"View in Keel: {research_url(ctx)}")


def _handler(args, ctx):
    params = {k: args.get(k) for k in ("project_id", "limit", "cursor")}
    env = call(ctx, "get", "/v1/research/brief", params=params)
    env = {**env, "links": {"app": research_url(ctx)}}
    return study_result(env, brief_text(env, ctx), size="view")


TOOL = register_research(
    "research_brief",
    ("research", "brief"),
    DESCRIPTION,
    PROPERTIES,
    list(_SCHEMA["required"]),
    REQUIRED_ACTION,
    _handler,
    read=True,
    title="Research Brief",
)
