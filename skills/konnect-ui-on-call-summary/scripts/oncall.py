#!/usr/bin/env python3
"""
Konnect UI MFE on-call summary helper.

Subcommands:
  collect   Query Datadog via pup and print a draft on-call markdown report
            to stdout.
  create    Create a Datadog notebook from a markdown file.

This script wraps every `pup` invocation it needs internally so the calling
agent only has to authorize one bash call per phase (collect / create).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KONNECT_UI_APP_ID = "6e430333-9e0f-4d6b-ac63-5f7d4ad9a641"

# GitHub repo backing the konnect-ui MFEs.
GITHUB_REPO = "kong-konnect/konnect-ui-apps"
# The shared CI workflow runs every MFE; MFE-specific failures are job names
# of the form "mfe (<MFE>) / <step>".
SHARED_CI_WORKFLOW = "CI"
# Cascade jobs that always fail when an upstream test fails — uninformative
# on their own. We hide them so the report shows the actual broken step.
CI_CASCADE_STEPS = {
    "check-dev-stage",
    "check-prod-stage",
    "Collect results",
    "Slack Notification",
}
# How many failed CI runs to inspect per session. Each run = one ~30s gh API
# call, parallelized via ThreadPoolExecutor. 30 covers a normal week well; on
# very busy weeks the user can pass --ci-run-limit higher.
CI_RUN_LIMIT_DEFAULT = 30
CI_PARALLEL_WORKERS = 8

# Display name overrides for MFEs whose default title-casing isn't right.
MFE_DISPLAY = {
    "gateway-manager": "Gateway Manager",
    "mesh-manager": "Mesh Manager",
    "ai-manager": "AI Manager",
    "service-hub": "Service Hub",
    "konnect-shell": "Konnect Shell",
    "analytics": "Analytics",
}

# Blacklist — issues matching any of these are dropped from the report.
# Patterns match against the combined "<error_type>: <error_message>" string
# (the same form as each issue's title). To stop hiding one of these, comment
# it out — the issue will then appear in the report.
NOISE = [
    # ---- Browser / extension noise ----
    re.compile(r"chrome-extension://"),
    re.compile(r"Unable to preload CSS"),
    re.compile(r"Ignored attempt to cancel a touchmove event"),
    re.compile(r"ResizeObserver loop"),
    # ---- Known recurring, not actionable ----
    re.compile(r"Request failed with status code 401", re.IGNORECASE),
    re.compile(r"Request failed with status code 403"),
    re.compile(r"Request failed with status code 404"),
    re.compile(r"Failed to fetch dimensions[^\n]*?canceled", re.IGNORECASE),
    re.compile(r"Failed to fetch dynamically imported module"),
    re.compile(r"CanceledError: canceled$"),  # bare cancel, no further context
    # CSP violations: error-tracking surfaces these with type=script-src-elem,
    # script-src, worker-src, etc. and a "blocked by '<directive>'" message.
    re.compile(r"^script-src(?:-elem)?:"),
    re.compile(r"^worker-src:"),
    re.compile(r"blocked by 'script-src"),
    re.compile(r"blocked by 'worker-src"),
    # ---- Browser-extension wallet/etc. noise ----
    re.compile(r"Failed to connect to MetaMask"),
]


# ---------- helpers ----------

def fail(msg: str) -> None:
    print(f"oncall.py: {msg}", file=sys.stderr)
    sys.exit(1)


def run_pup(args: list[str]) -> dict:
    """Invoke pup and return the parsed JSON response."""
    try:
        result = subprocess.run(
            ["pup", *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        fail("`pup` not found on PATH. Install pup first "
             "(https://github.com/datadog-labs/agent-skills).")
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        if "401" in err or "unauthor" in err.lower():
            fail("pup auth failed. Run `pup auth refresh` "
                 "(or `pup auth login` if refresh fails).")
        fail(f"pup {' '.join(args)} failed:\n{err}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(f"pup {' '.join(args)}: response was not JSON:\n{result.stdout[:500]}")


def ensure_authed() -> None:
    data = run_pup(["auth", "status"])
    if not data.get("authenticated"):
        fail("Not authenticated to Datadog. Run `pup auth login`.")


def default_week_of() -> datetime:
    """Monday of the most-recent fully-completed Mon–Sun week (UTC)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today - timedelta(days=7)
    days_back_to_monday = seven_days_ago.weekday()
    return seven_days_ago - timedelta(days=days_back_to_monday)


def resolve_week(week_of: str | None) -> tuple[datetime, datetime, str]:
    if week_of:
        try:
            start = datetime.strptime(week_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            fail(f"--week-of must be YYYY-MM-DD, got {week_of!r}")
    else:
        start = default_week_of()
    end = start + timedelta(days=7) - timedelta(seconds=1)
    label = f"{start.strftime('%b ')}{start.day} – {end.strftime('%b ')}{end.day}, {end.year}"
    return start, end, label


def display_name(mfe: str) -> str:
    return MFE_DISPLAY.get(mfe) or " ".join(p.capitalize() for p in mfe.split("-"))


def is_blacklisted(message: str) -> bool:
    return any(n.search(message) for n in NOISE)


# UUID v4 + similar IDs in URL paths — collapse to `<id>` so paths from
# different tenants/resources merge into one diagnostic row.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def normalize_path(path: str, max_len: int = 90) -> str:
    p = _UUID_RE.sub("<id>", path)
    if len(p) > max_len:
        p = p[: max_len - 1] + "…"
    return p


def run_gh(args: list[str]) -> object:
    """Best-effort `gh` invocation. Returns parsed JSON on success, None on
    any failure (gh not installed, not authenticated, network error, etc.)."""
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def normalize_name(s: str) -> str:
    """Lowercase + strip non-alphanumerics, so 'Gateway Manager CI' matches 'gateway-manager'."""
    return re.sub(r"[\W_]+", "", s.lower())


def workflow_is_relevant(wf_name: str, mfe: str) -> bool:
    """A workflow is relevant if it's the shared CI workflow (any MFE may
    have failed jobs in it) or the MFE name appears in the workflow name."""
    if wf_name == SHARED_CI_WORKFLOW:
        return True
    return normalize_name(mfe) in normalize_name(wf_name)


def title_from_message(message: str) -> str:
    """First line of the message, capped at 120 chars."""
    return (message.splitlines() or [""])[0].strip()[:120]


# ---------- pup queries ----------

def fetch_issues(mfe: str, start: datetime, end: datetime, limit: int = 50) -> list[dict] | None:
    """Search Datadog Error Tracking for RUM issues with events on the MFE's
    pages, then fetch full details for each. Returns a list of dicts with
    the fields we use downstream (id, count, sessions, type, message,
    file_path, first_seen_ms, last_seen_ms), or None if pup fails.

    We filter by `@view.url_path:*<MFE>*` (events on the MFE's pages) rather
    than `service:<MFE>` (events emitted by code tagged with that service).
    The URL filter is more inclusive: it captures errors from the app shell
    or shared packages that affect users while they're on the MFE — the team
    cares about user impact, not where the throwing module's service tag was
    set. This also keeps issue counts consistent with the per-issue RUM
    aggregate path query later.
    """
    search = run_pup([
        "error-tracking", "issues", "search",
        "--query", f"@view.url_path:*{mfe}*",
        "--from", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--to", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--track", "rum",
        "--limit", str(limit),
        "--order-by", "TOTAL_COUNT",
    ])
    if search is None:
        return None
    summaries = search.get("data", []) or []
    if not summaries:
        return []

    def _enrich(s: dict) -> dict | None:
        issue_id = s.get("id")
        if not issue_id:
            return None
        det = run_pup(["error-tracking", "issues", "get", issue_id])
        if det is None:
            return None
        a = det.get("data", {}).get("attributes", {})
        return {
            "id": issue_id,
            "count": s.get("attributes", {}).get("total_count", 0) or 0,
            "sessions": s.get("attributes", {}).get("impacted_sessions", 0) or 0,
            "type": a.get("error_type", "") or "",
            "message": a.get("error_message", "") or "",
            "file_path": a.get("file_path", "") or "",
            "first_seen_ms": a.get("first_seen", 0) or 0,
            "last_seen_ms": a.get("last_seen", 0) or 0,
        }

    # Detail fetches are parallel-safe; order doesn't matter (we re-sort later).
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_enrich, summaries))
    return [r for r in results if r is not None]


def humanize_age(ms: int, now_ms: int) -> str:
    """Render a millisecond timestamp as a human-readable age relative to `now_ms`."""
    if not ms:
        return "unknown"
    days = (now_ms - ms) / 86_400_000
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 7:
        return f"{int(days)} days ago"
    if days < 30:
        weeks = int(days / 7)
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if days < 365:
        months = int(days / 30)
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = int(days / 365)
    return f"{years} year{'s' if years != 1 else ''} ago"


def issue_link(issue_id: str, mfe: str, start: datetime, end: datetime) -> str:
    """Return a DD Error Tracking issue URL filtered to the MFE's pages."""
    from_ts = int(start.timestamp() * 1000)
    to_ts = int(end.timestamp() * 1000)
    # %40view.url_path%3A*<mfe>*
    query = f"%40view.url_path%3A*{mfe}*"
    return (
        f"https://app.datadoghq.com/error-tracking/issue/{issue_id}"
        f"?query={query}&from_ts={from_ts}&to_ts={to_ts}"
    )


def fetch_top_paths(app_id: str, mfe: str, error_type: str, error_message: str,
                    start: datetime, end: datetime) -> list[dict]:
    """Return the top affected URL paths (within the MFE) for a given error.

    RUM stores `@error.message` as the FULL stringified error (with type
    prefix for AxiosError, etc.) but error-tracking stores type and message
    separately. We try the type-prefixed form first; on no match, fall back
    to the bare message.
    """
    msg_line = (error_message.splitlines() or [""])[0].strip()
    if not msg_line:
        return []
    if len(msg_line) > 250:
        msg_line = msg_line[:250]

    candidates = []
    if error_type:
        candidates.append(f"{error_type}: {msg_line}")
    candidates.append(msg_line)

    for candidate in candidates:
        esc = candidate.replace("\\", "\\\\").replace('"', '\\"')
        out = run_pup([
            "rum", "aggregate",
            "--query", (
                f"@type:error @application.id:{app_id} "
                f"@view.url_path:*{mfe}* "
                f'@error.message:"{esc}"'
            ),
            "--from", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "--to", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "--group-by", "@view.url_path",
            "--compute", "count",
            "--limit", "10",
        ])
        if not out or not out.get("data", {}).get("buckets"):
            continue
        # Found matches under this candidate form; stop trying alternatives.
        break
    merged: dict[str, int] = {}
    for b in out.get("data", {}).get("buckets", []) or []:
        path = b.get("by", {}).get("@view.url_path", "")
        cnt = b.get("computes", {}).get("c0", 0) or 0
        if not path or not cnt:
            continue
        norm = normalize_path(path)
        merged[norm] = merged.get(norm, 0) + cnt
    return sorted(
        [{"path": p, "count": c} for p, c in merged.items()],
        key=lambda x: -x["count"],
    )


def fetch_failed_jobs(run_id: int | str) -> list[str] | None:
    """Return failed job names for a single workflow run, or None on error.

    Uses --jq server-side to drop everything we don't need so the response
    stays small (a typical CI run has 700+ jobs).
    """
    try:
        result = subprocess.run([
            "gh", "run", "view", str(run_id),
            "--repo", GITHUB_REPO,
            "--json", "jobs",
            "--jq", '.jobs[] | select(.conclusion=="failure") | .name',
        ], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def fetch_ci_failures(mfe: str, start: datetime, end: datetime,
                      run_limit: int = CI_RUN_LIMIT_DEFAULT) -> list[dict] | None:
    """Query GitHub for failed CI runs on main affecting this MFE.

    Returns:
        list of {workflow, step, count, latest_url, latest_date} entries on
        success (possibly empty), or None if gh is unavailable/unauthenticated.
    """
    runs = run_gh([
        "run", "list",
        "--repo", GITHUB_REPO,
        "--branch", "main",
        "--status", "failure",
        "--created", f"{start.strftime('%Y-%m-%d')}..{end.strftime('%Y-%m-%d')}",
        "--limit", str(run_limit),
        "--json", "databaseId,workflowName,createdAt,url",
    ])
    if runs is None:
        return None

    relevant = [r for r in runs if workflow_is_relevant(r.get("workflowName", ""), mfe)]
    if not relevant:
        return []

    print(f"oncall.py: inspecting {len(relevant)} CI run(s) "
          f"(parallelized, {CI_PARALLEL_WORKERS} workers)…", file=sys.stderr)

    # Fetch failed job lists in parallel — each gh run view is ~30s.
    with ThreadPoolExecutor(max_workers=CI_PARALLEL_WORKERS) as pool:
        jobs_by_run = list(pool.map(
            lambda r: (r, fetch_failed_jobs(r["databaseId"])),
            relevant,
        ))

    mfe_marker = f"mfe ({mfe}) /"
    by_step: dict[tuple[str, str], dict] = {}

    # `relevant` is already sorted most-recent-first by gh, so the first time
    # we see a (workflow, step) pair, the run's URL/date are the latest.
    for run, failed_names in jobs_by_run:
        if failed_names is None:
            continue
        wf_name = run.get("workflowName", "")
        for job_name in failed_names:
            if wf_name == SHARED_CI_WORKFLOW:
                if mfe_marker not in job_name:
                    continue
                step = job_name.split(" / ", 1)[1] if " / " in job_name else job_name
            else:
                step = job_name
            if step in CI_CASCADE_STEPS:
                continue

            key = (wf_name, step)
            entry = by_step.get(key)
            if entry is None:
                by_step[key] = {
                    "workflow": wf_name,
                    "step": step,
                    "count": 1,
                    "latest_url": run.get("url", ""),
                    "latest_date": (run.get("createdAt") or "")[:10],
                }
            else:
                entry["count"] += 1

    return list(by_step.values())


# ---------- subcommands ----------

def cmd_collect(args: argparse.Namespace) -> None:
    ensure_authed()
    start, end, _label = resolve_week(args.week_of)

    issues = fetch_issues(args.mfe, start, end)
    if issues is None:
        fail("error-tracking lookup failed (see pup output above).")

    name = display_name(args.mfe)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)

    entries: list[dict] = []
    blacklisted_count = 0
    for iss in issues:
        msg = iss["message"] or ""
        full_msg = f"{iss['type']}: {msg}" if iss["type"] else msg
        if is_blacklisted(full_msg):
            blacklisted_count += iss["count"]
            continue
        if iss["count"] < args.min_count:
            continue
        entries.append(iss)

    # Top affected paths: one pup aggregate per kept issue. Sequential is fine
    # at this scale (typical reports have 3–10 issues).
    for e in entries:
        e["top_paths"] = fetch_top_paths(
            args.app_id, args.mfe, e["type"], e["message"], start, end,
        )[:3]
        e["dd_link"] = issue_link(e["id"], args.mfe, start, end)

    ci_failures = (
        None if args.skip_ci
        else fetch_ci_failures(args.mfe, start, end, run_limit=args.ci_run_limit)
    )

    out: list[str] = []
    out.append("# Incidents")
    out.append("")
    out.append(
        f"_TODO: List any incidents affecting {name} this week — "
        f'replace this line with `No incidents affecting {name} in this period.` if none._'
    )
    out.append("")

    out.append("# Errors")
    out.append("")
    if not entries:
        out.append("No notable errors observed during this week.")
        out.append("")
    else:
        for e in sorted(entries, key=lambda x: -x["count"]):
            # Title: <error_type>: <message-first-line> (matches reference style)
            msg_short = (e["message"].splitlines() or [""])[0].strip()[:120]
            title = f"{e['type']}: {msg_short}" if e["type"] else msg_short
            out.append(f"### {title}")
            out.append("")
            out.append(f"[DD Error]({e['dd_link']})")
            out.append("")

            # Stats line — count + impacted sessions + first/last seen.
            stats_parts = [
                f"{e['count']} occurrence{'s' if e['count'] != 1 else ''}"
            ]
            if e["sessions"]:
                stats_parts.append(
                    f"{e['sessions']} session{'s' if e['sessions'] != 1 else ''} impacted"
                )
            stats_line = ", ".join(stats_parts) + "."

            new_this_week = e["first_seen_ms"] >= start_ms
            if new_this_week:
                stats_line = f"**New this week.** {stats_line}"
            else:
                age = humanize_age(e["first_seen_ms"], now_ms)
                stats_line += f" First seen {age}."
            out.append(stats_line)

            top_paths = e.get("top_paths") or []
            if top_paths:
                paths_str = ", ".join(
                    f"`{p['path']}` (×{p['count']})" for p in top_paths
                )
                out.append("")
                out.append(f"Pages: {paths_str}.")

            if e["file_path"] and not e["file_path"].startswith("../"):
                out.append("")
                out.append(f"Reported source: `{e['file_path']}`.")

            # Diagnosis placeholder — Claude fills this in during preview
            # with an engineer-style 1–2 sentence hypothesis using all of the
            # facts above plus its codebase knowledge.
            out.append("")
            out.append(
                "_Possible cause: TODO — replace with a 1–2 sentence "
                "engineer-style hypothesis._"
            )
            out.append("")

    if blacklisted_count:
        out.append(
            f"<!-- {blacklisted_count} blacklisted event(s) filtered "
            f"(see scripts/oncall.py NOISE list). -->"
        )
        out.append("")

    out.append("# CI")
    out.append("")
    if ci_failures is None:
        # gh missing/unauthenticated, or --skip-ci; leave the user a hint.
        out.append(
            "(CI lookup skipped or unavailable — run `gh auth status` to verify access "
            f"to `{GITHUB_REPO}`.)"
        )
    elif not ci_failures:
        out.append(f"No CI failures on `main` affecting {name} this week.")
    else:
        for cf in sorted(ci_failures, key=lambda x: -x["count"]):
            s = "s" if cf["count"] != 1 else ""
            wf_prefix = "" if cf["workflow"] == SHARED_CI_WORKFLOW else f"{cf['workflow']}: "
            out.append(
                f"- `{wf_prefix}{cf['step']}` — {cf['count']} failure{s} on `main` "
                f"([latest]({cf['latest_url']}) {cf['latest_date']})"
            )

    print("\n".join(out))


def cmd_create(args: argparse.Namespace) -> None:
    ensure_authed()
    start, _end, label = resolve_week(args.week_of)

    if not os.path.isfile(args.markdown_file):
        fail(f"markdown file not found: {args.markdown_file}")
    with open(args.markdown_file, encoding="utf-8") as f:
        text = f.read()

    title = f"On-call summary - Konnect {display_name(args.mfe)} ({label})"
    payload = {
        "data": {
            "attributes": {
                "name": title,
                "time": {"live_span": "1w"},
                "cells": [{
                    "attributes": {
                        "definition": {"type": "markdown", "text": text}
                    },
                    "type": "notebook_cells",
                }],
                "status": "published",
            },
            "type": "notebooks",
        }
    }

    fd, tmp_path = tempfile.mkstemp(
        prefix=f"oncall-{args.mfe}-{start.strftime('%Y%m%d')}-", suffix=".json"
    )
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)

    out = run_pup(["notebooks", "create", "--file", tmp_path])
    nid = out.get("data", {}).get("id")
    if nid:
        print(f"Created: https://app.datadoghq.com/notebook/{nid}")
    else:
        print("Notebook created. Response:")
        print(json.dumps(out, indent=2))


# ---------- entry point ----------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Konnect UI MFE on-call summary helper",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("collect", help="Print draft on-call markdown to stdout")
    pc.add_argument("--mfe", required=True, help="MFE name, e.g. gateway-manager")
    pc.add_argument("--week-of",
                    help="Monday of target week (YYYY-MM-DD). "
                         "Default: previous fully-completed Mon–Sun week.")
    pc.add_argument("--app-id", default=KONNECT_UI_APP_ID, help="RUM application ID")
    pc.add_argument("--min-count", type=int, default=2,
                    help="Drop error buckets with fewer than this many "
                         "occurrences (default: 2). Set to 1 to include every "
                         "non-blacklisted error.")
    pc.add_argument("--skip-ci", action="store_true",
                    help="Skip GitHub CI lookup. Useful if gh isn't installed "
                         "or authenticated, or to speed up the run.")
    pc.add_argument("--ci-run-limit", type=int, default=CI_RUN_LIMIT_DEFAULT,
                    help=f"Max number of failed CI runs to inspect "
                         f"(default: {CI_RUN_LIMIT_DEFAULT}). Each run = one "
                         f"~30s gh API call, parallelized "
                         f"{CI_PARALLEL_WORKERS}-wide. Bump for very busy "
                         f"weeks; lower for speed.")
    pc.set_defaults(func=cmd_collect)

    pn = sub.add_parser("create", help="Create the Datadog notebook from a markdown file")
    pn.add_argument("--mfe", required=True)
    pn.add_argument("--week-of", required=True, help="Monday of target week (YYYY-MM-DD)")
    pn.add_argument("--markdown-file", required=True)
    pn.set_defaults(func=cmd_create)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
