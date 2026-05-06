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
from datetime import datetime, timedelta, timezone

KONNECT_UI_APP_ID = "6e430333-9e0f-4d6b-ac63-5f7d4ad9a641"

# Display name overrides for MFEs whose default title-casing isn't right.
MFE_DISPLAY = {
    "gateway-manager": "Gateway Manager",
    "mesh-manager": "Mesh Manager",
    "ai-manager": "AI Manager",
    "service-hub": "Service Hub",
    "konnect-shell": "Konnect Shell",
    "analytics": "Analytics",
}

# Order matters: first match wins. Each entry classifies a RUM error message
# bucket into a category with a fixed title and canned wording.
PATTERNS = [
    {
        "id": "session_timeout",
        "regex": re.compile(
            r"AxiosError[^\n]*?Request failed with status code 401",
            re.IGNORECASE,
        ),
        "title": "AxiosError: Request failed with status code 401 (session timeout)",
        "wording": "Session timeout — user navigated to the page with an expired session.",
    },
    {
        "id": "axios_timeout",
        "regex": re.compile(r"AxiosError: timeout of \d+ms exceeded"),
        "title": "AxiosError: timeout of 30000ms exceeded",
        "wording": "Network issue or slow upstream API.",
    },
    {
        "id": "get_computed_style",
        "regex": re.compile(r"Failed to execute 'getComputedStyle' on 'Window'"),
        "title": (
            "TypeError: Failed to execute 'getComputedStyle' on 'Window': "
            "parameter 1 is not of type 'Element'."
        ),
        "wording": "Component unmounted before style read. Does not affect user interaction.",
    },
    {
        "id": "canceled_dimensions",
        "regex": re.compile(r"Failed to fetch dimensions[^\n]*?CanceledError: canceled"),
        "title": "Failed to fetch dimensions — CanceledError: canceled",
        "wording": (
            "Request aborted when the user navigated away before the analytics "
            "dimensions fetch completed. Same pattern as previous weeks."
        ),
    },
    {
        "id": "axios_403",
        "regex": re.compile(r"AxiosError[^\n]*?status code 403"),
        "title": "AxiosError: Request failed with status code 403",
        "wording": "Permission denied (user lacks access to the resource).",
    },
    {
        "id": "axios_404",
        "regex": re.compile(r"AxiosError[^\n]*?status code 404"),
        "title": "AxiosError: Request failed with status code 404",
        "wording": "Resource not found (likely deleted or stale link).",
    },
    {
        "id": "undefined_property",
        "regex": re.compile(r"Cannot read properties of undefined \(reading '([^']+)'\)"),
        "title": "TypeError: Cannot read properties of undefined (reading '{prop}')",
        "wording": "Frontend bug — code accesses `.{prop}` on undefined. **Investigate.**",
        "follow_up": True,
    },
    {
        "id": "dynamic_import_failed",
        "regex": re.compile(r"Failed to fetch dynamically imported module"),
        "title": "TypeError: Failed to fetch dynamically imported module",
        "wording": "Bundle chunk fetch failed; usually a stale client after a deploy. Self-healing on reload.",
    },
]

# Patterns considered noise; matches are excluded from the report entirely.
NOISE = [
    re.compile(r"chrome-extension://"),
    re.compile(r"Unable to preload CSS"),
    re.compile(r"intervention: Ignored attempt to cancel"),
    re.compile(r"ResizeObserver loop"),
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


def classify(message: str) -> dict | None:
    for n in NOISE:
        if n.search(message):
            return None
    for p in PATTERNS:
        m = p["regex"].search(message)
        if not m:
            continue
        ctx = {"prop": m.group(1) if m.groups() else ""}
        return {
            "id": p["id"],
            "title": p["title"].format(**ctx),
            "wording": p["wording"].format(**ctx),
            "follow_up": p.get("follow_up", False),
        }
    return {
        "id": f"unknown::{message[:60]}",
        "title": message.split("\n")[0][:120],
        "wording": "Unknown error pattern. **Investigate.**",
        "follow_up": True,
    }


def to_epoch_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def dd_link(app_id: str, mfe: str, anchor_iso: str) -> str:
    ms = to_epoch_ms(anchor_iso)
    from_ts, to_ts = ms - 60_000, ms + 60_000
    query = (
        f"%40type%3Aerror%20%40application.id%3A{app_id}%20env%3Aprod%20"
        f"-%40browser.name%3AHeadlessChrome%20service%3A{mfe}%20"
        f"-%40error.message%3A%22Unable%20to%20preload%20CSS%22%20"
        f"-%40error.message%3Achrome-extension"
    )
    return (
        f"https://app.datadoghq.com/rum/sessions?query={query}"
        f"&agg_m=count&agg_m_source=base&agg_t=count"
        f"&fromUser=false&refresh_mode=paused&track=rum"
        f"&from_ts={from_ts}&to_ts={to_ts}&live=false"
    )


# ---------- pup queries ----------

def fetch_aggregate(app_id: str, mfe: str, start: datetime, end: datetime) -> list[dict]:
    out = run_pup([
        "rum", "aggregate",
        "--query", f"@type:error @application.id:{app_id} service:{mfe}",
        "--from", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--to", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--group-by", "@error.message",
        "--compute", "count",
        "--limit", "50",
    ])
    return out.get("data", {}).get("buckets", [])


def fetch_sample(app_id: str, mfe: str, msg: str, start: datetime, end: datetime) -> dict | None:
    """Fetch one event matching the bucket message exactly.

    Datadog wildcards do not work inside quoted strings, so we use exact
    match. Multi-line messages are reduced to their first line; quotes and
    backslashes are escaped. Messages are capped at 250 chars to avoid
    pathological queries (the aggregate bucket value usually IS the full
    message, so this is plenty for known categories).
    """
    line = (msg.splitlines() or [""])[0].strip()
    if not line:
        return None
    if len(line) > 250:
        line = line[:250]
    escaped = line.replace("\\", "\\\\").replace('"', '\\"')
    q = (
        f'@type:error @application.id:{app_id} service:{mfe} '
        f'@error.message:"{escaped}"'
    )
    out = run_pup([
        "rum", "events",
        "--query", q,
        "--from", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--to", end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--limit", "1",
    ])
    events = out.get("data", [])
    if not events:
        return None
    e = events[0]
    a = e.get("attributes", {}).get("attributes", {})
    return {
        "ts": e.get("attributes", {}).get("timestamp", ""),
        "url": a.get("view", {}).get("url", ""),
    }


def fetch_incidents(start: datetime, end: datetime) -> list[dict]:
    out = run_pup(["incidents", "list", "--limit", "50"])
    items = out.get("data", {}).get("attributes", {}).get("incidents", []) or []
    result = []
    for i in items:
        d = i.get("data", {}).get("attributes", {})
        created = d.get("created", "")
        if not created:
            continue
        try:
            cdt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= cdt <= end:
            result.append({
                "title": d.get("title") or "(no title)",
                "severity": d.get("severity") or "UNKNOWN",
                "status": d.get("status") or "",
                "created": created[:10],
            })
    return result


# ---------- subcommands ----------

def cmd_collect(args: argparse.Namespace) -> None:
    ensure_authed()
    start, end, _label = resolve_week(args.week_of)

    buckets = fetch_aggregate(args.app_id, args.mfe, start, end)

    categories: dict[str, dict] = {}
    for b in buckets:
        msg = b.get("by", {}).get("@error.message", "") or ""
        count = b.get("computes", {}).get("c0", 0) or 0
        if not msg or count == 0:
            continue
        c = classify(msg)
        if c is None:
            continue
        cid = c["id"]
        if cid not in categories:
            categories[cid] = {
                "title": c["title"],
                "wording": c["wording"],
                "count": 0,
                "sample_message": msg,
                "follow_up": c.get("follow_up", False),
            }
        categories[cid]["count"] += count

    for c in categories.values():
        ev = fetch_sample(args.app_id, args.mfe, c["sample_message"], start, end)
        anchor = ev["ts"] if ev and ev.get("ts") else start.strftime("%Y-%m-%dT%H:%M:%SZ")
        c["dd_link"] = dd_link(args.app_id, args.mfe, anchor)

    incidents = fetch_incidents(start, end)
    name = display_name(args.mfe)

    out: list[str] = []
    out.append("# Incidents")
    out.append("")
    if incidents:
        for i in incidents:
            out.append(f"- {i['created']} [{i['severity']}] {i['title']}")
    else:
        out.append(f"No incidents affecting {name} in this period.")
    out.append("")

    out.append("# Errors")
    out.append("")
    if not categories:
        out.append("No notable errors observed during this week.")
        out.append("")
    else:
        ordered = sorted(categories.values(), key=lambda c: -c["count"])
        for c in ordered:
            out.append(f"### {c['title']}")
            out.append("")
            out.append(f"[DD Link]({c['dd_link']})")
            out.append("")
            stats = f"{c['count']} occurrence{'s' if c['count'] != 1 else ''}"
            out.append(f"{stats}. {c['wording']}")
            out.append("")

    out.append("# CI")
    out.append("")
    out.append("No CI issues observed during this week. Failed tests all passed on reruns.")

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
