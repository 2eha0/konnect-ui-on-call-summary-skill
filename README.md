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

### 2. Python 3

The skill pipes JSON through small `python3` snippets to extract fields. macOS and most Linux dev machines ship with this; no additional packages required.

### 3. Access to Konnect's Datadog org

The skill targets the konnect-ui RUM application (`6e430333-9e0f-4d6b-ac63-5f7d4ad9a641`) under the Kong production Datadog org. You need an account with read access to this org's RUM data and notebooks.

### 4. Claude Code

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
ls ~/.claude/skills/konnect-ui-on-call-summary/SKILL.md
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

1. Tell you the resolved date range.
2. Run the pup queries (you'll see the commands go by).
3. **Print the full draft markdown to you.**
4. Ask: _"Want me to create the notebook in Datadog?"_

Reply with confirmation (`yes`, `好的`, `创建吧`, `go ahead`, …) and the notebook is created and you get a link. Reply with edits (`add a CI section about flaky test X`, `drop the intervention error`, …) and the draft is regenerated before re-asking.

If you want to skip the preview and create immediately, say so explicitly: _"create it without previewing."_ (Default behavior is always preview-first.)

## What's in the report

| Section | Content |
|---------|---------|
| **Incidents** | Datadog incidents created during the window that affect the MFE, or `No incidents affecting <MFE> in this period.` |
| **Errors** | One subsection per notable RUM error category. Each has a DD deep link (filtered to the right time window) and a 1–2 sentence summary. Common categories like 401 session timeouts, 30s API timeouts, navigation-canceled requests, and `getComputedStyle` unmount races are described in plain language. Pure noise (chrome extension errors, CSS preload warnings, etc.) is omitted. |
| **CI** | Flaky/broken tests for the week. Defaults to "No CI issues observed" — extend by telling Claude what CI issues to include. |

## Customization

To adapt for a different team or MFE family, edit `skills/konnect-ui-on-call-summary/SKILL.md`:

- The **MFE → RUM app mapping** table in step 3.
- The **Common errors and how to describe them** table to match patterns your team sees.
- The report sections (e.g., add a "Performance" or "Customer issues" block) by editing the template in step 9.

## Limitations

- The skill aggregates errors by exact `@error.message`. Errors that include resource IDs in the message (e.g., `… for <uuid> …`) appear as many distinct buckets and need manual grouping.
- DD deep links use a 2-minute window around a representative event, not a precise `event=` token, so the link lands on the time range rather than scrolling to a specific event in the side panel.
- CI status is not pulled automatically — defaults to "No CI issues observed" unless the user provides input or asks the skill to investigate further.
- Only Konnect MFEs running under the konnect-ui RUM app are pre-mapped. For others, the skill will list all RUM apps and ask.

## Troubleshooting

**`pup notebooks create` fails with `missing field time`** — the JSON payload must include `"time": {"live_span": "1w"}` at the same level as `name` and `cells`. The skill adds this; if you customized the payload, make sure it's there.

**`@view.url:*<mfe>*` returns events but `service:<mfe>` doesn't (or vice versa)** — the `service` filter is narrower (only the MFE's own errors); the URL filter pulls in shared shell errors. The skill prefers `service:` for accurate ownership; if you want a broader scope explicitly, ask for it.

**Skill doesn't activate on your phrase** — the skill triggers on "on-call summary", "周报", "weekly report", and MFE names. If your team uses a different term ("incident review", "RUM digest", …), edit the description in `SKILL.md` to add it.

**`pup auth status` shows expired** — run `pup auth refresh`. If refresh fails, run `pup auth login` (interactive browser flow).

## Repository layout

```
skills-konnect-ui-on-call-summary/
├── README.md                              ← you are here
└── skills/
    └── konnect-ui-on-call-summary/
        └── SKILL.md                       ← the skill itself
```

This follows the [`skills`](https://www.npmjs.com/package/skills) package convention: skills live under `skills/<skill-name>/SKILL.md`, where `<skill-name>` matches the `name` field in the SKILL.md frontmatter.

## License

MIT
