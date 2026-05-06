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

The script:

1. Verifies pup auth (aborts with a clear message if expired).
2. Aggregates RUM errors by `@error.message` for `service:<MFE_NAME>` in the window.
3. Classifies each bucket against a fixed pattern table (session timeout, axios timeout, getComputedStyle race, dimension fetch cancel, dynamic-import fail, undefined-property bug, 403, 404, …) and drops noise (chrome-extension errors, preload-CSS warnings, intervention warnings, ResizeObserver loops).
4. Pulls one representative event per category for the DD deep link (uses exact-message match, not wildcards).
5. Looks up incidents in the window.
6. Prints the complete markdown report to stdout in the team's fixed format.

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

| ID | Title | Wording |
|---|---|---|
| `session_timeout` | `AxiosError: Request failed with status code 401 (session timeout)` | "Session timeout — user navigated to the page with an expired session." |
| `axios_timeout` | `AxiosError: timeout of 30000ms exceeded` | "Network issue or slow upstream API." |
| `axios_403` | `AxiosError: Request failed with status code 403` | "Permission denied (user lacks access to the resource)." |
| `axios_404` | `AxiosError: Request failed with status code 404` | "Resource not found (likely deleted or stale link)." |
| `get_computed_style` | `TypeError: Failed to execute 'getComputedStyle' on 'Window'…` | "Component unmounted before style read. Does not affect user interaction." |
| `canceled_dimensions` | `Failed to fetch dimensions — CanceledError: canceled` | "Request aborted when the user navigated away…" |
| `dynamic_import_failed` | `TypeError: Failed to fetch dynamically imported module` | "Bundle chunk fetch failed; usually a stale client after a deploy." |
| `undefined_property` | `TypeError: Cannot read properties of undefined (reading '<prop>')` | "Frontend bug — code accesses `.<prop>` on undefined. **Investigate.**" |

Anything unmatched is shown as `### <first 120 chars of message>` with wording `Unknown error pattern. **Investigate.**`. The user often wants to drop these or rewrite them — that's a normal edit during step 2.

To add or change patterns/noise rules, edit `scripts/oncall.py`'s `PATTERNS` and `NOISE` lists. Keep them ordered: first match wins.

## Common pitfalls

- **Auth expired mid-session.** The script prints `pup auth failed. Run pup auth refresh…`. Tell the user; they need to run it themselves (interactive). Re-run step 1.
- **Empty errors section.** Means `service:<MFE>` had no error events in the window. Confirm the MFE name and try `--app-id` if it's not under konnect-ui.
- **`### IDs: [` or other malformed titles.** These are real "unknown" buckets — usually short prefixes from custom error logs. The user may want to drop or rewrite them in step 2.
- **Don't fabricate context.** "This same error showed up the previous week" or "this is a regression from PR #X" are claims you can't verify from the data alone. Stick to the script's wording unless the user adds context themselves.
