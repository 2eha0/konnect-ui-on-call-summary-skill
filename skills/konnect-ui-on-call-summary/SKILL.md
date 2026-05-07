---
name: konnect-ui-on-call-summary
description: Generate a weekly on-call summary notebook for a Konnect UI microfrontend (gateway-manager, mesh-manager, ai-manager, etc.) by querying RUM error data and incidents from Datadog via the pup CLI, then creating a Datadog notebook that follows the team's standard format. Use when the user asks for a past-week on-call summary, weekly RUM error report, or wants to create the team's on-call notebook for a specific Konnect UI MFE. Triggers on phrases like "on-call summary", "weekly on-call report", "on-call 周报", "上周的周报", or "<mfe-name> 周报".
---

# Konnect UI On-Call Summary

Drafts and creates the team's standard weekly on-call summary notebook for a Konnect UI microfrontend (MFE). All Datadog interactions are encapsulated in a single helper script (`scripts/oncall.py`); your job is to drive it, preview the output to the user, and create the notebook only after explicit approval.

## Hard rules

1. **Always preview before creating.** Never run `oncall.py create` (or `pup notebooks create`) until the user explicitly approves the draft (e.g., "yes", "create it", "go ahead", "好的", "创建吧"). If the user requests edits, revise the markdown buffer and re-preview.
2. **Fill in the `_Possible cause: TODO_` placeholders before previewing.** The script intentionally leaves them blank; you (the LLM) write them. See Step 1.5.
3. **Show the script's stdout verbatim, _except_ for those TODOs.** Do not summarize, reorder, or add other interpretive context. If the user wants extra text (e.g., a CI note), append/edit explicitly on their request.
4. **Two bash calls per session.** All pup and gh queries are wrapped by the script — don't shell out to `pup` or `gh` directly except for `pup auth login` / `pup auth refresh` / `gh auth login` if the user needs to re-authenticate.

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
- `--min-count N` — drop buckets with fewer than this many occurrences (default 2). Filters one-off truncated log lines that pup occasionally indexes as errors.
- `--skip-ci` — skip the GitHub CI lookup. Use if `gh` isn't installed/authenticated, or to speed up the run.
- `--ci-run-limit N` — max number of failed CI runs to inspect (default 30). Each is a ~30s API call, parallelized 8-wide. Bump for very busy weeks; lower for speed.

The script:

1. Verifies pup auth (aborts with a clear message if expired).
2. Searches Datadog Error Tracking for issues with events on `@view.url_path:*<MFE>*` in the window, then enriches each issue with full details in parallel (error type, message, file path, first/last seen, count, impacted sessions). The URL-path filter captures errors visible to MFE users regardless of which service tag the throwing module had.
3. **Drops blacklisted issues** (the `NOISE` list in `scripts/oncall.py`): browser/extension noise *plus* known recurring errors that don't merit weekly mention (401/403/404, navigation cancels, dynamic-import retries, CSP violations, MetaMask wallet noise, …). The blacklist is matched against the combined `<error_type>: <error_message>` form.
4. Drops issues below `--min-count` to filter low-volume noise.
5. Lists every remaining issue with: title (`<error_type>: <message>`), DD Error Tracking link, occurrence + session impact, first-seen age, top affected URL paths (with UUIDs collapsed to `<id>`), and a `_Possible cause: TODO_` placeholder for you to fill in.
6. Tags issues that first appeared in this week's window with **New this week.**
7. Emits a TODO placeholder in the **Incidents** section — incident data is **not** auto-fetched. The user fills this in (or confirms "no incidents") during preview.
8. Queries GitHub via `gh` for failed CI runs on `main` of `kong-konnect/konnect-ui-apps` in the window. Filters to jobs named `mfe (<MFE>) /<step>` (within the shared `CI` workflow) plus any failed jobs in MFE-specific workflows. Cascade jobs (`check-dev-stage`, `check-prod-stage`, `Collect results`, `Slack Notification`) are dropped to keep the focus on the actual broken steps.
9. Prints the markdown report. A trailing HTML comment notes how many error events were filtered as blacklisted.

**Pure blacklist semantics.** There is no whitelist or per-category formatting. New error types appear automatically with their raw message. To hide an error type going forward, add its regex to the `NOISE` list in `scripts/oncall.py`.

The CI query needs an authenticated `gh` (run `gh auth status` to verify). If `gh` is unavailable, the CI section says so but the rest of the report still produces.

### Step 1.5 — Fill in the engineer-style diagnoses

The script puts a `_Possible cause: TODO — replace with a 1–2 sentence engineer-style hypothesis._` line under each error. Replace each TODO **before** previewing. This is the LLM-powered diagnostic step — without it the report is just a list of facts.

For each error block, write 1–2 sentences as if you were the on-call engineer triaging it. Use:

- The **error type and message** — e.g. `LaunchDarklyTimeoutError`, `AxiosError: timeout of 30000ms exceeded`, `TypeError: Cannot read properties of undefined (reading '<x>')`.
- The **affected pages** — they tell you which feature/route is broken (overview, plugin update flow, gateway-logs, etc.).
- The **session/occurrence ratio** — high count + low sessions = one user looping; high sessions = many users hit it.
- The **first-seen age and the "New this week" flag** — distinguishes regressions from chronic noise.
- Your knowledge of the **Konnect codebase**: gateway-manager / mesh-manager / ai-manager / etc. live in `kong-konnect/konnect-ui-apps`; the shell is `app-root` from `Kong/shared-ui-components`; many shared components come from `Kong/public-ui-components` (`@kong-ui-public/*`).

Style guide (see [reference notebook 12138800](https://app.datadoghq.com/notebook/12138800/on-call-summary-konnect-gateway-manager-fe-makito-apr-7-14) for the team's tone):

- **Hedge.** Use "likely", "looks like", "may be", "could be" — never assert. The hypothesis is a starting point, not a finding.
- **Be specific where the data lets you.** Name a probable component, package, or upstream service. If a path points at a known feature, say so (e.g. "the plugin update form" rather than "some page").
- **Link source when you can confidently locate it.** Markdown link to a file or line in `kong-konnect/konnect-ui-apps`, `Kong/shared-ui-components`, or `Kong/public-ui-components` is great. Don't fabricate URLs — only link if you're confident the file exists.
- **Flag known/recurring noise.** If something has been recurring for months, say so ("Recurring known issue, probably from external scripts") instead of speculating fresh causes.
- **Mark non-issues with `~~strikethrough~~`** (for the title line) when you're confident the error is harmless (e.g. ResizeObserver loop, Intercom-related).

**Examples** (drawn from the reference style):

> Error title: `AxiosError: timeout of 30000ms exceeded`
> Pages: `/<region>/gateway-manager/<id>/overview`, `/.../plugins/post-function/<id>/update`
> _Possible cause:_ Likely a slow upstream — the overview view aggregates control-plane stats and the plugin update flow validates against the dataplane. Could be transient degradation in `konnect-api` or the analytics aggregator; check APM for slow traces during the spike windows.

> Error title: `LaunchDarklyTimeoutError: waitForInitialization timed out after 5 seconds.`
> First seen: 1 week ago, 38 sessions impacted
> _Possible cause:_ The LaunchDarkly client failed to initialize within 5 s — likely a regional CDN/network blip or the SDK init timeout being too aggressive. Tracked from `app-root`'s `useSession.ts` in `Kong/shared-ui-components`; widespread session impact suggests it's worth raising the timeout or adding a retry.

> Error title: `TypeError: Cannot read properties of undefined (reading 'app')`
> First seen: this week, on `/.../gateway-manager/<id>/plugins`
> _Possible cause:_ **New this week** on the plugins listing — likely a regression from a recent schema change or a missing optional-chaining guard. The `.app` access points at a metadata object that the parent loaded asynchronously; check the most recent gateway-manager PRs touching the plugins list view.

> Error title: `SecurityError: Failed to read a named property 'document' from 'Window'…`
> _Possible cause:_ Cross-origin iframe access denial — typical when an embedded iframe (Intercom, Pendo, third-party widget) tries to read the parent. Recurring noise; rarely indicates a real bug unless volume changes sharply.

If a particular error doesn't have enough data for a useful guess, it's fine to write only one short hedged sentence ("Recurring; data here doesn't suggest a fresh cause — left for ad-hoc investigation."). Don't make things up.

### Step 2 — Preview to the user

Show the script's stdout, with all `_Possible cause: TODO_` placeholders replaced by your hypotheses. The Incidents section will still contain its `_TODO_` placeholder — that's the user's to fill. Ask the user **two** things in the preview:

> Any incidents affecting `<MFE>` this week I should add? (Or just say "none" to drop the TODO.)
> Once that's settled — want me to create the notebook in Datadog?

Wait for a response on incidents before asking about creation. Edit the markdown buffer to:
- Replace the Incidents TODO line with the user's incident list (bulleted), or
- Replace it with `No incidents affecting <MFE> in this period.` if they say none.

If the user requests other edits ("drop the 404 entry", "tighten the LaunchDarkly hypothesis", "add a CI note about flaky test X"), modify the markdown buffer and re-show. **Do not advance to step 3 without explicit approval.**

If the user says "create it without previewing" upfront, still fill in the diagnoses (step 1.5) — they're the whole point — and then run step 3.

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

The script always emits this skeleton (you fill in the `_Possible cause_` lines in step 1.5):

```markdown
# Incidents

_TODO: List any incidents affecting <MFE> this week — replace this line with `No incidents affecting <MFE> in this period.` if none._

# Errors

### <error_type>: <error_message-first-line, capped at 120 chars>

[DD Error](https://app.datadoghq.com/error-tracking/issue/<uuid>?query=…)

**New this week.** N occurrences, M sessions impacted.
(or: N occurrences, M sessions impacted. First seen <age>.)

Pages: `<view.url_path with UUIDs collapsed to <id>>` (×K), …

Reported source: `<file_path>` (only if error-tracking has one)

_Possible cause: TODO — replace with a 1–2 sentence engineer-style hypothesis._

### <next issue>
…

<!-- N blacklisted event(s) filtered … -->

# CI

- `<failed step name>` — N failure(s) on `main` ([latest](url) YYYY-MM-DD)
- …
```

Pages come from a per-issue `pup rum aggregate --group-by @view.url_path`; UUIDs are collapsed to `<id>` so the same logical page merges across tenants. Some issues have no `@view.url_path` association (errors raised before the first view event) and the Pages line is omitted.

If `gh` is unavailable, the CI section says so. If no CI failures match the MFE, it says "No CI failures on `main` affecting `<MFE>` this week."

One list in `scripts/oncall.py` drives the behavior:

**`NOISE` — blacklist. Buckets matching any of these regexes are dropped from the report.**

| Regex | Why blacklisted |
|---|---|
| `chrome-extension://` | Browser extension noise |
| `Unable to preload CSS` | Bundle prefetch warning, no signal |
| `Ignored attempt to cancel a touchmove event` | Browser passive-listener warning |
| `ResizeObserver loop` | Browser layout warning, harmless |
| `Request failed with status code 401/403/404` | Auth/permission/stale-link (expected user-flow) |
| `Failed to fetch dimensions … canceled` | Navigation cancel (expected) |
| `Failed to fetch dynamically imported module` | Stale client after deploy, self-healing |
| `^script-src(?:-elem)?:`, `^worker-src:`, `blocked by 'script-src'`, `blocked by 'worker-src'` | CSP violations from external scripts |
| `csp_violation:` | Legacy CSP-violation form (RUM aggregate) |
| `Failed to connect to MetaMask` | Browser-extension wallet noise |

Every other Error Tracking issue appears as a section with title `### <error_type>: <error_message>`, count + impacted sessions, first-seen age, top affected pages (when available), and a `_Possible cause: TODO_` placeholder you fill in during step 1.5. An issue needs at least `--min-count` occurrences to appear (default 2).

To hide more error types going forward, add their regex to `NOISE`. To stop hiding one, comment it out — it will reappear in the report on the next run.

## Common pitfalls

- **pup auth expired mid-session.** The script prints `pup auth failed. Run pup auth refresh…`. Tell the user; they need to run it themselves (interactive). Re-run step 1.
- **gh not authenticated.** The CI section will show "lookup unavailable". Tell the user to run `gh auth login`, or pass `--skip-ci` to skip the lookup.
- **CI lookup is slow on a busy week.** Each relevant failed run takes one ~30s `gh run view` API call. Calls run 8-wide via `ThreadPoolExecutor`, so ~30 runs ≈ 2 minutes total. The user can pass `--skip-ci` (fill CI manually) or `--ci-run-limit N` (cap inspection) to trade thoroughness for speed.
- **Empty errors section.** Means `service:<MFE>` had no error events in the window. Confirm the MFE name and try `--app-id` if it's not under konnect-ui.
- **`### IDs: [` or other malformed titles.** These are bucket messages with junky prefixes. The user may want to drop them in step 2.
- **Don't fabricate context.** "This same error showed up the previous week" or "this is a regression from PR #X" are claims you can't verify from the data alone. Stick to the script's wording unless the user adds context themselves.
