# skills-konnect-ui-on-call-summary

A Claude Code agent skill that drafts and creates the team's standard weekly on-call summary notebook for any Konnect UI microfrontend (gateway-manager, mesh-manager, ai-manager, etc.) using Datadog RUM data.

Distributed via the [`skills`](https://www.npmjs.com/package/skills) CLI.

## What it does

When invoked, the skill:

1. Identifies the target MFE and reporting window from your request.
2. Queries Datadog RUM via `pup` to aggregate errors and pull representative sessions.
3. Checks for incidents that intersect the window.
4. Drafts the report in the team's three-section format (Incidents / Errors / CI), with DD deep links for each error category.
5. **Previews the draft to you** and asks for confirmation.
6. Only after you approve, creates the notebook in Datadog and returns the URL.

## Prerequisites

### 1. `pup` CLI

This skill relies entirely on `pup` (Kong's Datadog CLI) for both reading RUM data and writing the notebook.

Install per the [agent-skills setup guide](https://github.com/datadog-labs/agent-skills/tree/main?tab=readme-ov-file#setup-pup), then authenticate:

```bash
pup auth login
pup auth status   # confirm: ✅ Authenticated for site: datadoghq.com
```

OAuth tokens last ~1 hour. If a command fails with 401/403 mid-run, refresh:

```bash
pup auth refresh
```

The OAuth scopes needed by this skill (all included in the default scope set granted by `pup auth login`):

- `rum_apps_read`
- `notebooks_read`, `notebooks_write`
- `incident_read`

### 2. `gh` CLI (GitHub)

The CI section of the report queries the [`kong-konnect/konnect-ui-apps`](https://github.com/kong-konnect/konnect-ui-apps) repo for failed CI runs on `main`. Install via `brew install gh` (macOS) and authenticate:

```bash
gh auth login
gh auth status   # confirm logged in to github.com
```

You need read access to `kong-konnect/konnect-ui-apps`. If `gh` is missing or unauthenticated, the rest of the report still works — the CI section just shows a hint to set it up. Pass `--skip-ci` to skip the lookup entirely.

### 3. Python 3

The skill is a single Python script using only the standard library. macOS and most Linux dev machines ship with this; no `pip install` required.

### 4. Access to Konnect's Datadog org

The skill targets the konnect-ui RUM application (`6e430333-9e0f-4d6b-ac63-5f7d4ad9a641`) under the Kong production Datadog org. You need an account with read access to this org's RUM data and notebooks.

### 5. Claude Code

You need [Claude Code](https://claude.com/claude-code) installed. The skill is loaded from `~/.claude/skills/` (user-global) or `.claude/skills/` (project-local).

## Installation

Use the [`skills`](https://www.npmjs.com/package/skills) CLI to install. The package handles fetching the repo and placing `SKILL.md` in the right location for Claude Code to pick it up.

### From git (recommended)

```bash
# User-global (available across all projects)
npx skills add 2eha0/konnect-ui-on-call-summary-skill -g

# Project-local
npx skills add 2eha0/konnect-ui-on-call-summary-skill
```

### Verify

```bash
# SKILL.md is in place
ls ~/.claude/skills/konnect-ui-on-call-summary/SKILL.md

# The helper script runs and shows its CLI
python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py --help
```

Restart Claude Code (or open a new session) so the skill is discovered. Confirm by asking Claude to list available skills, or by triggering it (see Usage below).

### Uninstall

```bash
npx skills remove konnect-ui-on-call-summary -g
```

## Usage

Trigger the skill with plain language. Examples:

- `帮我写一份上周 gateway-manager 的 on-call 周报`
- `Generate the weekly on-call summary for mesh-manager`
- `Write last week's RUM error report for ai-manager`
- `Draft the on-call notebook for gateway-manager covering Apr 28 – May 4`

Claude will:

1. Run the helper script (one bash call): `python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py collect --mfe <name>`. The script does all the pup queries internally.
2. **Print the full draft markdown to you, verbatim.**
3. Ask: _"Want me to create the notebook in Datadog?"_

Reply with confirmation (`yes`, `好的`, `创建吧`, `go ahead`, …) and a second bash call (`oncall.py create …`) creates the notebook and prints the URL. Reply with edits (`add a CI section about flaky test X`, `drop the 404 entry`, …) and the draft is updated before re-asking.

If you want to skip the preview and create immediately, say so explicitly: _"create it without previewing."_ (Default behavior is always preview-first.)

### Manual usage (without Claude)

The helper script is a normal CLI; you can run it directly:

```bash
# Draft markdown for last week of gateway-manager
python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py collect \
  --mfe gateway-manager > /tmp/draft.md

# Edit /tmp/draft.md, then create the notebook
python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py create \
  --mfe gateway-manager --week-of 2026-04-27 --markdown-file /tmp/draft.md
```

## What's in the report

The format is fixed by `scripts/oncall.py` — every report has the same three sections in the same order:

| Section | Content |
|---------|---------|
| **Incidents** | Datadog incidents created during the window. Empty section says `No incidents affecting <MFE> in this period.` |
| **Errors** | One subsection per non-blacklisted RUM error bucket. Each has a DD deep link (2-minute window around a representative event) and one line: `<N> occurrences.` Title is the first line of the raw error message, capped at 120 chars. |
| **CI** | Failed CI runs on `main` of `kong-konnect/konnect-ui-apps` affecting this MFE, queried via `gh`. The script filters to jobs named `mfe (<MFE>) / <step>` in the shared `CI` workflow plus any failed jobs in MFE-specific workflows (e.g. `Gateway Manager Plugin Tests Scheduler`). Cascade jobs (`check-dev-stage`, `check-prod-stage`, `Collect results`, `Slack Notification`) are dropped. Empty section says "No CI failures on `main` affecting `<MFE>` this week." |

### Filtering: blacklist only

The script uses a **single blacklist** (`NOISE` in `scripts/oncall.py`). Anything matching it is dropped; everything else appears in the report with its raw message. There is no whitelist, no per-category formatting, and no classification — new error patterns surface automatically.

The blacklist covers two tiers:

1. **Browser/extension noise** — `chrome-extension://`, `Unable to preload CSS`, `intervention: Ignored attempt to cancel`, `ResizeObserver loop`. No signal value.
2. **Known recurring errors** — `AxiosError ... 401/403/404`, `Failed to fetch dimensions ... CanceledError: canceled`, `Failed to fetch dynamically imported module`, `csp_violation:`. The team has decided these don't merit weekly mention.

A trailing HTML comment in the report tallies how many events were filtered: `<!-- N blacklisted event(s) filtered (see scripts/oncall.py NOISE list). -->`.

### Other knobs

- `--min-count N` (default 2) — drop buckets with fewer than N occurrences. Filters one-off truncated log lines pup occasionally indexes as errors. Set to 1 to include every non-blacklisted bucket.
- `--skip-ci` — skip the GitHub CI lookup. Useful if `gh` isn't installed/authenticated, or to speed up the run.
- `--ci-run-limit N` (default 30) — max number of failed CI runs to inspect. Each is a ~30s `gh run view` API call (parallelized 8-wide via `ThreadPoolExecutor`); the default ≈ 2 minutes wall-clock on a busy week. Bump for thoroughness on extreme weeks, lower for speed.

## Customization

Edit `skills/konnect-ui-on-call-summary/scripts/oncall.py`:

- **Hide a recurring error from reports** — append its regex to `NOISE`.
- **Stop hiding something** — comment out its entry in `NOISE`.
- **Override an MFE's display name** — extend `MFE_DISPLAY` (used in titles and notebook names).
- **Change the report skeleton** — edit `cmd_collect` (the function that prints the markdown).
- **Use a different RUM app** — pass `--app-id <UUID>` at the CLI, or change `KONNECT_UI_APP_ID`.

The trigger phrases (which determine when Claude activates the skill) live in `SKILL.md`'s `description` field — edit there to add team-specific terms ("incident review", "RUM digest", …).

## Limitations

- **Aggregation key is exact `@error.message`.** Errors that include resource IDs (e.g., `… for <uuid> …`) become many distinct buckets. Most are caught by `NOISE` regexes; the rest are listed individually.
- **DD deep links land on a time range, not a specific event.** The link uses a 2-minute window around the representative event because pup doesn't expose the encoded `event=` token. Reviewers click through and see the matching events listed in the side panel.
- **CI lookup is konnect-ui-apps-specific.** The skill hardcodes `kong-konnect/konnect-ui-apps` and the job-name convention `mfe (<MFE>) / <step>`. For a different repo or naming scheme, edit `GITHUB_REPO`, `SHARED_CI_WORKFLOW`, and the relevant filtering logic in `scripts/oncall.py`.
- **Konnect-ui is the default RUM app.** For MFEs in other apps (admin-konnect-ui, portal-nuxt, kong-manager-oss, …), pass `--app-id`.

## Troubleshooting

**`pup auth failed. Run pup auth refresh…`** — your token expired. Run `pup auth refresh`; if that also fails, `pup auth login` (interactive browser flow). Then re-run the collect step.

**Empty Errors section** — `service:<MFE>` matched no error events in the window. Confirm the MFE name (the `service` tag in RUM is usually identical to the MFE name) and try `--app-id` if it's not under konnect-ui.

**`### IDs: [` or other malformed-looking titles** — those are bucket messages with junky prefixes. Edit them away during preview, or add a regex to `NOISE` if they should be auto-hidden going forward.

**CI section says "lookup unavailable"** — run `gh auth status`. If not logged in, `gh auth login`. If logged in but the lookup fails, you may not have read access to `kong-konnect/konnect-ui-apps`. To skip CI entirely, pass `--skip-ci`.

**CI lookup is slow** — `gh run view` is ~30s per call because each CI workflow run has 700+ jobs. The script parallelizes 8-wide via `ThreadPoolExecutor`, so 30 runs ≈ 2 minutes wall-clock. Pass `--ci-run-limit 10` for speed, `--skip-ci` to skip entirely, or accept the wait if you want completeness.

**Skill doesn't activate on your phrase** — the skill triggers on "on-call summary", "周报", "weekly report", and MFE names. If your team uses different terms, edit the `description` in `SKILL.md`.

**`pup notebooks create` fails with `missing field time`** — the helper script always writes `time: {live_span: 1w}`. If you see this, you're likely calling pup directly from a custom payload — copy the payload structure from `cmd_create`.

## Repository layout

```
skills-konnect-ui-on-call-summary/
├── README.md                                ← you are here
└── skills/
    └── konnect-ui-on-call-summary/
        ├── SKILL.md                          ← agent instructions
        └── scripts/
            └── oncall.py                     ← all the pup logic
```

This follows the [`skills`](https://www.npmjs.com/package/skills) package convention: skills live under `skills/<skill-name>/SKILL.md`, where `<skill-name>` matches the `name` field in the SKILL.md frontmatter. Bundled scripts live alongside `SKILL.md` and are referenced by absolute path from there.

## License

MIT
