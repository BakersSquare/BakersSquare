#!/usr/bin/env python3
"""Render project status badges and progress bars into README.md.

Reads projects.json, counts done/total items on each entry's GitHub Project (v2)
via the GraphQL API, writes a progress-bar SVG per entry to
assets/progress/, and rewrites the block between the PROGRESS markers in
README.md.

Usage:
  GH_TOKEN=... python scripts/update_progress.py
  python scripts/update_progress.py --fixture counts.json   # no API calls
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "projects.json"
README = ROOT / "README.md"
ASSETS = ROOT / "assets" / "progress"
START = "<!-- PROGRESS:START -->"
END = "<!-- PROGRESS:END -->"

STATUS = {
    "not_started": ("Not started", "lightgrey", "#9ca3af"),
    "in_progress": ("In progress", "yellow", "#eab308"),
    "completed": ("Completed", "brightgreen", "#22c55e"),
}


PROJECT_QUERY = """
query($owner: String!, $name: String!, $title: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    projectsV2(query: $title, first: 20) {
      nodes {
        title
        items(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            isArchived
            fieldValueByName(name: "Status") {
              ... on ProjectV2ItemFieldSingleSelectValue { name }
            }
            content {
              ... on Issue { state }
              ... on PullRequest { state }
            }
          }
        }
      }
    }
  }
}
"""


def graphql(query, variables, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "readme-progress",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        sys.exit(f"GitHub GraphQL error: {body['errors']}")
    return body["data"]


def is_done(item, done_status):
    status = (item.get("fieldValueByName") or {}).get("name")
    if status:
        return status == done_status
    # No Status set: fall back to the issue/PR state.
    return (item.get("content") or {}).get("state") in ("CLOSED", "MERGED")


def fetch_counts(project, token):
    """Return (done, total) for the GitHub Project (v2) named project["project"]."""
    if not token:
        sys.exit("GH_TOKEN is required to read GitHub Projects")
    owner, name = project["repo"].split("/")
    done_status = project.get("done_status", "Done")
    done = total = 0
    cursor = None
    while True:
        data = graphql(PROJECT_QUERY, {
            "owner": owner, "name": name, "title": project["project"], "cursor": cursor,
        }, token)
        nodes = data["repository"]["projectsV2"]["nodes"]
        match = next((n for n in nodes if n["title"] == project["project"]), None)
        if match is None:
            found = ", ".join(repr(n["title"]) for n in nodes) or "none"
            sys.exit(f'Project {project["project"]!r} not found in {project["repo"]} (found: {found})')
        for item in match["items"]["nodes"]:
            if item.get("isArchived"):
                continue
            total += 1
            done += is_done(item, done_status)
        page = match["items"]["pageInfo"]
        if not page["hasNextPage"]:
            return done, total
        cursor = page["endCursor"]


def status_of(closed, total):
    if total == 0:
        return "not_started"
    return "completed" if closed >= total else "in_progress"


def slug(project):
    raw = f'{project["repo"]}-{project["version"]}'.lower()
    return re.sub(r"[^a-z0-9.]+", "-", raw).strip("-")


def badge_part(text):
    # shields.io static badge escaping: '-' -> '--', '_' -> '__', space -> '_'
    text = text.replace("-", "--").replace("_", "__").replace(" ", "_")
    return urllib.parse.quote(text, safe="_.")


def progress_svg(closed, total, color):
    width, height, bar_w = 260, 20, 160
    pct = round(100 * closed / total) if total else 0
    fill = round(bar_w * pct / 100)
    label = f"{closed}/{total} · {pct}%" if total else "no items yet"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" aria-label="{label}">
  <title>{label}</title>
  <rect x="0" y="4" width="{bar_w}" height="12" rx="6" fill="#e5e7eb"/>
  <rect x="0" y="4" width="{fill}" height="12" rx="6" fill="{color}"/>
  <text x="{bar_w + 10}" y="14.5" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" font-size="12" fill="#6b7280">{label}</text>
</svg>
"""


def render_row(project, closed, total):
    text, badge_color, bar_color = STATUS[status_of(closed, total)]
    name = slug(project)
    (ASSETS / f"{name}.svg").write_text(progress_svg(closed, total, bar_color))
    badge = ("https://img.shields.io/badge/"
             f'{badge_part(project["version"])}-{badge_part(text)}-{badge_color}')
    return (
        f'**[{project["name"]}](https://github.com/{project["repo"]})**<br>\n'
        f'<img src="{badge}" alt="{project["version"]}: {text}"><br>\n'
        f'<img src="assets/progress/{name}.svg" alt="{closed} of {total} items done">\n'
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", help="JSON file mapping '<repo>@<project>' to [closed, total]")
    args = parser.parse_args()

    projects = json.loads(CONFIG.read_text())["projects"]
    fixture = json.loads(Path(args.fixture).read_text()) if args.fixture else None
    token = os.environ.get("GH_TOKEN")
    ASSETS.mkdir(parents=True, exist_ok=True)

    rows = []
    for project in projects:
        if fixture is not None:
            closed, total = fixture[f'{project["repo"]}@{project["project"]}']
        else:
            closed, total = fetch_counts(project, token)
        rows.append(render_row(project, closed, total))

    readme = README.read_text()
    if START not in readme or END not in readme:
        sys.exit(f"README.md is missing the {START} / {END} markers")
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    README.write_text(head + START + "\n" + "\n".join(rows) + END + tail)


if __name__ == "__main__":
    main()
