#!/usr/bin/env python3
"""Generate README.md and site/index.html from data/papers.yaml.

Usage:
    python scripts/build.py          # validate + generate
    python scripts/build.py --check  # validate only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "papers.yaml"
TEMPLATES = ROOT / "templates"
README_OUT = ROOT / "README.md"
SITE_OUT = ROOT / "site" / "index.html"

# "owner/name"; GitHub Actions sets GITHUB_REPOSITORY, so forks get their own URLs.
REPO = os.environ.get("GITHUB_REPOSITORY", "ryhara/awesome-event-based-hands")

TYPES = ("conference", "journal", "workshop", "preprint")
# Controlled vocabulary for the `task` field, in display order.
TASKS = (
    "pose",                # 2D/3D keypoint estimation
    "mesh",                # parametric / mesh reconstruction (e.g. MANO)
    "tracking",            # temporal tracking of hands, fingertips, hand motion
    "detection",           # bounding boxes / hand presence
    "segmentation",        # pixel- or event-level hand masks
    "gesture recognition", # hand gesture / action classification
    "sign language",       # sign language recognition / translation
    "hand-object",         # hand-object interaction / manipulation action recognition
    "action recognition",  # egocentric / hand-object action recognition (hands are the main actor)
    "dataset",             # papers whose main contribution includes a dataset
    "simulation",          # event simulators / synthetic data pipelines for hands
)
# Link keys in display order -> label shown in README / site.
LINKS = {"doi": "DOI", "project": "Project", "arxiv": "arXiv", "code": "Code"}
REQUIRED = ("title", "authors", "venue", "year", "type", "task")
ALLOWED = set(REQUIRED) | set(LINKS) | {"tags"}

URL_RE = re.compile(r"^https?://\S+$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")


def normalize_link(key: str, value: str) -> str | None:
    """Return a full URL for a link field, or None if the value is invalid."""
    if URL_RE.match(value):
        return value
    if key == "doi" and DOI_RE.match(value):
        return f"https://doi.org/{value}"
    if key == "arxiv" and ARXIV_RE.match(value):
        return f"https://arxiv.org/abs/{value}"
    return None


def load_papers() -> list[dict]:
    # BaseLoader keeps every scalar as a string, so an unquoted arXiv ID such as
    # 2505.19160 is not parsed as a float (which would drop the trailing zero).
    raw = yaml.load(DATA.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(raw, list):
        sys.exit(f"{DATA.name}: top level must be a list of papers")

    errors: list[str] = []
    papers: list[dict] = []
    seen: dict[str, int] = {}

    for i, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            errors.append(f"#{i}: entry must be a mapping")
            continue
        where = f"#{i} ({str(entry.get('title', '?'))[:50]})"
        n_errors = len(errors)

        for key in REQUIRED:
            if not entry.get(key):
                errors.append(f"{where}: missing required field '{key}'")
        for key in sorted(set(entry) - ALLOWED):
            errors.append(f"{where}: unknown field '{key}'")

        authors = entry.get("authors", [])
        if not isinstance(authors, list) or not all(isinstance(a, str) and a.strip() for a in authors):
            errors.append(f"{where}: 'authors' must be a list of names")
        tags = entry.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
            errors.append(f"{where}: 'tags' must be a list of strings")
        task = entry.get("task", [])
        if not isinstance(task, list) or not task or not all(isinstance(t, str) for t in task):
            errors.append(f"{where}: 'task' must be a non-empty list")
        else:
            for t in task:
                if t.strip() not in TASKS:
                    errors.append(f"{where}: unknown task '{t}' (allowed: {', '.join(TASKS)})")
        for key in ("title", "venue", "year", "type", *LINKS):
            if key in entry and not isinstance(entry[key], str):
                errors.append(f"{where}: '{key}' must be a single value")

        year = str(entry.get("year", ""))
        if entry.get("year") and not re.fullmatch(r"(19|20)\d{2}", year):
            errors.append(f"{where}: 'year' must be a 4-digit year, got '{year}'")
        if entry.get("type") and entry["type"] not in TYPES:
            errors.append(f"{where}: 'type' must be one of {', '.join(TYPES)}")

        links = {}
        for key in LINKS:
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                continue
            url = normalize_link(key, value.strip())
            if url is None:
                errors.append(f"{where}: invalid '{key}' value '{value}'")
            else:
                links[key] = url

        if len(errors) > n_errors:
            continue

        title = " ".join(entry["title"].split())
        norm = re.sub(r"[^a-z0-9]+", "", title.lower())
        if norm in seen:
            errors.append(f"{where}: duplicate title (same as #{seen[norm]})")
            continue
        seen[norm] = i

        papers.append(
            {
                "title": title,
                "authors": [a.strip() for a in authors],
                "venue": entry["venue"].strip(),
                "year": int(year),
                "type": entry["type"],
                # Keep vocabulary order so badges/facets are stable across entries.
                "task": [t for t in TASKS if t in {x.strip() for x in task}],
                "tags": [t.strip() for t in tags],
                "links": links,
            }
        )

    if errors:
        sys.exit(f"{DATA.name}: {len(errors)} error(s)\n  " + "\n  ".join(errors))

    # Newest year first; sorted() is stable, so file order is kept within a year.
    return sorted(papers, key=lambda p: -p["year"])


def md_escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", text)


def render_readme(papers: list[dict], site_url: str) -> str:
    years = sorted({p["year"] for p in papers}, reverse=True)
    toc = " · ".join(f"[{y}](#{y})" for y in years)
    # Task counts link to the pre-filtered site view.
    task_count = {t: sum(t in p["task"] for p in papers) for t in TASKS}
    tasks = " · ".join(
        f"[{t}]({site_url}#task={quote(t)}) ({n})" for t, n in task_count.items() if n
    )

    blocks = []
    for year in years:
        lines = [f"### {year}", ""]
        for p in (p for p in papers if p["year"] == year):
            links = " ".join(f"[[{label}]]({p['links'][key]})" for key, label in LINKS.items() if key in p["links"])
            task = "Task: " + ", ".join(p["task"])
            lines.append(f"- `{p['venue']} {p['year']}` **{md_escape(p['title'])}**  ")
            lines.append(f"  {md_escape(', '.join(p['authors']))}  ")
            lines.append(f"  {links} · {task}" if links else f"  {task}")
            lines.append("")
        blocks.append("\n".join(lines))

    out = (TEMPLATES / "README.md.tmpl").read_text(encoding="utf-8")
    for key, value in {
        "SITE_URL": site_url,
        "REPO_URL": f"https://github.com/{REPO}",
        "COUNT": str(len(papers)),
        "TOC": toc,
        "TASKS": tasks,
        "PAPERS": "\n".join(blocks).rstrip(),
    }.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def render_site(papers: list[dict]) -> str:
    # "</" must not appear inside an inline <script>.
    data = json.dumps(papers, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    labels = json.dumps(LINKS)
    out = (TEMPLATES / "index.html.tmpl").read_text(encoding="utf-8")
    return (
        out.replace("__REPO_URL__", f"https://github.com/{REPO}")
        .replace("__LINK_LABELS__", labels)
        .replace("__TASKS__", json.dumps(TASKS))
        .replace("__PAPERS__", data)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate data/papers.yaml without writing files")
    args = parser.parse_args()

    papers = load_papers()
    if args.check:
        print(f"OK: {len(papers)} papers")
        return

    owner, name = REPO.split("/", 1)
    site_url = f"https://{owner.lower()}.github.io/{name}/"

    README_OUT.write_text(render_readme(papers, site_url), encoding="utf-8")
    SITE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SITE_OUT.write_text(render_site(papers), encoding="utf-8")
    print(f"Generated {README_OUT.relative_to(ROOT)} and {SITE_OUT.relative_to(ROOT)} ({len(papers)} papers)")


if __name__ == "__main__":
    main()
