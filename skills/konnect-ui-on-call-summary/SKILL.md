---
name: konnect-ui-on-call-summary
description: Generate a weekly on-call summary notebook for a Konnect UI microfrontend (gateway-manager, mesh-manager, ai-manager, etc.) by querying RUM error data and incidents from Datadog via the pup CLI, then creating a Datadog notebook that follows the team's standard format. Use when the user asks for a past-week on-call summary, weekly RUM error report, or wants to create the team's on-call notebook for a specific Konnect UI MFE. Triggers on phrases like "on-call summary", "weekly on-call report", "on-call 周报", "上周的周报", or "<mfe-name> 周报".
---

# Konnect UI On-Call Summary

Drafts and creates the team's standard weekly on-call summary notebook for a Konnect UI microfrontend (MFE). All Datadog interactions are encapsulated in a single helper script (`scripts/oncall.py`); your job is to drive it, preview the output to the user, and create the notebook only after explicit approval.

## Hard rules

1. **Always preview before creating.** Never run `oncall.py create` (or `pup notebooks create`) until the user explicitly approves the draft (e.g., "yes", "create it", "go ahead", "好的", "创建吧"). If the user requests edits, revise the markdown buffer and re-preview.
2. **Show the script's stdout verbatim.** The format is fixed by the script. Do not summarize, reorder, or add interpretive context to the draft. If the user wants extra text (e.g., a CI note), append/edit explicitly on their request.
3. **Two bash calls per session.** All pup queries are wrapped by the script — don't shell out to `pup` directly except for `pup auth login` / `pup auth refresh` if the user needs to re-authenticate.

## Triggers

The user asks for a weekly on-call summary report for a specific Konnect UI MFE. Examples:

- `帮我写一份上周 gateway-manager 的 on-call 周报`
- `Generate the weekly on-call summary for mesh-manager`
- `Write last week's RUM error report for ai-manager`
- `Draft the on-call notebook for gateway-manager covering Apr 28 – May 4`

## Workflow

The script lives at `<skill-root>/scripts/oncall.py`. When installed via `npx skills add` to user-global, that's `~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py`. Use whichever absolute path matches the install location.

### Step 1 — Generate the draft

```bash
python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py collect \
  --mfe <MFE_NAME>
```

Optional flags:

- `--week-of YYYY-MM-DD` — Monday of the target week. Default: previous fully-completed Mon–Sun week.
- `--app-id <UUID>` — non-default RUM application ID. Default: konnect-ui (`6e430333-9e0f-4d6b-ac63-5f7d4ad9a641`), which covers gateway-manager / mesh-manager / ai-manager / analytics / etc.
- `--all` — include expected/low-signal categories (401 session timeouts, 403/404, navigation cancels, low-volume unknowns). Default keeps only noteworthy categories.
- `--min-unknown-count N` — minimum count for unclassified errors to appear in the default view (default 3).

The script:

1. Verifies pup auth (aborts with a clear message if expired).
2. Aggregates RUM errors by `@error.message` for `service:<MFE_NAME>` in the window.
3. Classifies each bucket against a fixed pattern table. Each pattern is tagged `noteworthy: True/False`. **By default only noteworthy categories are shown** (real bugs, perf issues, build/CSP issues). Routine errors (auth flow, permission denials, navigation cancels) are hidden under a footnote.
4. Drops noise outright: chrome-extension errors, preload-CSS warnings, intervention warnings, ResizeObserver loops.
5. Pulls one representative event per shown category for the DD deep link (exact-message match).
6. Looks up incidents in the window.
7. Prints the markdown report to stdout in the team's fixed format. A trailing HTML comment summarizes how many events/categories were hidden.

Use `--all` only when the user explicitly asks to see everything (e.g., "show me the full breakdown"). The default is the focused view that matches the team's existing on-call notebooks.

### Step 2 — Preview to the user

Show the script's stdout exactly as printed, then ask exactly:

> Want me to create the notebook in Datadog?

If the user requests edits ("drop the 404 entry", "add a CI note about flaky test X"), modify the markdown buffer in your reply, save it for use in step 3, and re-ask. **Do not advance to step 3 without explicit approval.**

If the user says "create it without previewing" upfront, you may run step 1 and step 3 back-to-back; otherwise the default is preview-first.

### Step 3 — Create the notebook (only after approval)

Save the approved markdown to a temp file, then run `create`:

```bash
cat > /tmp/oncall-draft.md << 'EOF'
<the markdown the user approved, exactly>
EOF

python3 ~/.claude/skills/konnect-ui-on-call-summary/scripts/oncall.py create \
  --mfe <MFE_NAME> \
  --week-of <YYYY-MM-DD> \
  --markdown-file /tmp/oncall-draft.md
```

The script builds the notebook JSON (including the required `time: {live_span: 1w}`), writes it to a temp file, and calls `pup notebooks create`. It prints the resulting notebook URL on success — relay it to the user.

`--week-of` is required for `create` so the title and `live_span` align with the data window. Use the same date you used (or the default resolved date) in step 1.

## Fixed report format

The script always emits this skeleton:

```markdown
# Incidents

<bullet list of incidents OR "No incidents affecting <MFE> in this period.">

# Errors

### <Error category title>

[DD Link](<URL>)

<N occurrences. <canned wording per category>>

### <next category>
…

# CI

No CI issues observed during this week. Failed tests all passed on reruns.
```

Categories the script knows:

| ID | Default | Title | Wording |
|---|---|---|---|
| `axios_timeout` | shown | `AxiosError: timeout of 30000ms exceeded` | "Network issue or slow upstream API." |
| `get_computed_style` | shown | `TypeError: Failed to execute 'getComputedStyle' on 'Window'…` | "Component unmounted before style read. Does not affect user interaction." |
| `dynamic_import_failed` | shown | `TypeError: Failed to fetch dynamically imported module` | "Bundle chunk fetch failed; usually a stale client after a deploy." |
| `csp_violation` | shown | `csp_violation: script blocked by CSP` | "Content Security Policy blocked a script load. **Investigate** if recurring." |
| `undefined_property` | shown | `TypeError: Cannot read properties of undefined (reading '<prop>')` | "Frontend bug — code accesses `.<prop>` on undefined. **Investigate.**" |
| `session_timeout` | hidden | `AxiosError: Request failed with status code 401 (session timeout)` | "Session timeout — user navigated to the page with an expired session." |
| `axios_403` | hidden | `AxiosError: Request failed with status code 403` | "Permission denied." |
| `axios_404` | hidden | `AxiosError: Request failed with status code 404` | "Resource not found." |
| `canceled_dimensions` | hidden | `Failed to fetch dimensions — CanceledError: canceled` | "Request aborted when the user navigated away…" |

Anything unmatched is shown as `### <first 120 chars of message>` with wording `Unknown error pattern. **Investigate.**` — but only if its count reaches `--min-unknown-count` (default 3). Lower-volume unknowns are folded into the hidden-categories footnote.

To add or change patterns/noise rules, edit `scripts/oncall.py`'s `PATTERNS` and `NOISE` lists. The `noteworthy` field on each pattern controls whether it shows by default. First match wins, so order patterns from most specific to most generic.

## Common pitfalls

- **Auth expired mid-session.** The script prints `pup auth failed. Run pup auth refresh…`. Tell the user; they need to run it themselves (interactive). Re-run step 1.
- **Empty errors section.** Means `service:<MFE>` had no error events in the window. Confirm the MFE name and try `--app-id` if it's not under konnect-ui.
- **`### IDs: [` or other malformed titles.** These are real "unknown" buckets — usually short prefixes from custom error logs. The user may want to drop or rewrite them in step 2.
- **Don't fabricate context.** "This same error showed up the previous week" or "this is a regression from PR #X" are claims you can't verify from the data alone. Stick to the script's wording unless the user adds context themselves.
