---
name: konnect-ui-on-call-summary
description: Generate a weekly on-call summary notebook for a Konnect UI microfrontend (gateway-manager, mesh-manager, ai-manager, etc.) by querying RUM error data and incidents from Datadog via the pup CLI, then creating a Datadog notebook that follows the team's standard format. Use when the user asks for a past-week on-call summary, weekly RUM error report, or wants to create the team's on-call notebook for a specific Konnect UI MFE. Triggers on phrases like "on-call summary", "weekly on-call report", "on-call 周报", "上周的周报", or "<mfe-name> 周报".
---

# Konnect UI On-Call Summary

Generate a weekly on-call summary report for a Konnect UI microfrontend (MFE) by querying Datadog RUM data via `pup`, formatting it in the team's standard structure, **previewing it to the user**, and creating a notebook only after the user explicitly confirms.

## Critical workflow rule

**Always preview the draft before creating the notebook.** Never call `pup notebooks create` until the user explicitly approves (e.g., "yes", "create it", "go ahead", "好的", "创建吧"). If the user replies with edits, revise and re-preview. This is the defining behavior of this skill — do not skip it unless the user says "create it without previewing" upfront.

## When this skill activates

The user asks for a weekly on-call summary report for a specific Konnect UI MFE. Examples:

- `帮我写一份上周 gateway-manager 的 on-call 周报`
- `Generate the weekly on-call summary for mesh-manager`
- `Write last week's RUM error report for ai-manager`
- `Draft the on-call notebook for gateway-manager covering Apr 28 – May 4`

## Workflow

Execute these steps in order.

### Step 1: Verify pup is authenticated

```bash
pup auth status
```

If not authenticated, ask the user to run `pup auth login` (interactive, must run themselves). If the token is expired, run `pup auth refresh`.

### Step 2: Identify the MFE and time range

Parse the user's request to extract:

- **MFE name** (e.g., `gateway-manager`, `mesh-manager`, `ai-manager`).
- **Time range** — default to the last 7 days (`--from 7d`). If the user specifies an explicit week, compute the date range using today's date (which is given in the conversation context). State the resolved range to the user before querying, e.g. _"I'll target service:gateway-manager for Apr 28 – May 4, 2026."_

### Step 3: Resolve RUM application ID and service tag

Most Konnect MFEs run under the `konnect-ui` RUM application. Default mapping:

| MFE family | RUM application ID | service tag |
|------------|-------------------|-------------|
| gateway-manager, mesh-manager, ai-manager, analytics, and other Konnect MFEs | `6e430333-9e0f-4d6b-ac63-5f7d4ad9a641` (konnect-ui) | matches the MFE name |
| admin-konnect-ui | `2ba08040-7198-4130-99ec-a16d6100f40e` | — |
| portal-nuxt | `28468667-2366-4118-a584-a7e2a0ba5fe3` | — |
| kong-manager-oss | `aa070bf6-270c-471f-8c05-e4f801c12c58` | — |

If unsure, list all apps:

```bash
pup rum apps list
```

### Step 4: Reference an existing similar notebook (recommended)

Find a recent on-call summary for the same MFE so the new one matches the team's format and tone:

```bash
pup notebooks list 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for n in data.get('data', []):
    name = n.get('attributes', {}).get('name', '')
    if 'on-call' in name.lower() and 'MFE_NAME' in name.lower():
        print(n['id'], '|', name)
"
```

Replace `MFE_NAME`. If found, fetch and read the markdown to see the format used:

```bash
pup notebooks get <ID> 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for c in data['data']['attributes']['cells']:
    if c['attributes']['definition']['type'] == 'markdown':
        print(c['attributes']['definition']['text'])
"
```

### Step 5: Aggregate RUM errors by message

```bash
pup rum aggregate \
  --query '@type:error @application.id:<APP_ID> service:<MFE_NAME>' \
  --from 7d \
  --group-by '@error.message' \
  --compute count \
  --limit 30
```

Notes:

- Prefer `service:<MFE_NAME>` over `@view.url:*<MFE_NAME>*`. The URL filter also pulls in shared-shell errors that aren't this team's responsibility.
- Group similar errors that differ only by IDs (e.g., all `getControlPlanesIdGroupMemberships for <uuid> ... 401` rows are the same root cause — count them together).

### Step 6: Pull representative session IDs and timestamps

For each notable error category, fetch one example to get a session ID and timestamp for the DD link:

```bash
pup rum events \
  --query '@type:error @application.id:<APP_ID> service:<MFE_NAME> @error.message:"<ERROR_FRAGMENT>"' \
  --from 7d --limit 3 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for e in data.get('data', []):
    a = e.get('attributes', {}).get('attributes', {})
    print('ts:', e.get('attributes', {}).get('timestamp', ''))
    print('sid:', a.get('session', {}).get('id', ''))
    print('url:', a.get('view', {}).get('url', ''))
"
```

Pick a session whose timestamp falls inside the report's window, not just any session in the last 7 days.

### Step 7: Compute epoch_ms timestamps

For each event timestamp, compute epoch milliseconds and a 2-minute window for the DD link:

```bash
python3 -c "
from datetime import datetime
ts = '2026-05-03T16:15:33.651Z'
dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
ms = int(dt.timestamp() * 1000)
print('from_ts =', ms - 60000)
print('to_ts   =', ms + 60000)
"
```

### Step 8: Check for incidents

```bash
pup incidents list --limit 20 2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
for i in data['data']['attributes']['incidents']:
    d = i.get('data', {}).get('attributes', {})
    print(d.get('created', '')[:10], '|', d.get('severity', ''), '|', d.get('status', ''), '|', d.get('title', ''))
"
```

Filter for incidents created in the report's date range and that affect the MFE. If none, the Incidents section is just `No incidents affecting <MFE> in this period.`

### Step 9: Build the report markdown

Use this exact structure (the team's convention):

```markdown
# Incidents

<List incidents OR "No incidents affecting <MFE> in this period.">

# Errors

### <Error title — concise, capitalized, exact message or summary>

[DD Link](<DD_RUM_URL>)

<1–2 sentences: occurrence count, affected pages, root cause if known. Reference past weeks if recurring.>

### <next error>
...

# CI

<List CI issues OR "No CI issues observed during this week. Failed tests all passed on reruns.">
```

DD link format (URL-encode by replacing `<APP_ID>`, `<MFE_NAME>`, `<FROM_TS>`, `<TO_TS>`):

```
https://app.datadoghq.com/rum/sessions?query=%40type%3Aerror%20%40application.id%3A<APP_ID>%20env%3Aprod%20-%40browser.name%3AHeadlessChrome%20service%3A<MFE_NAME>%20-%40error.message%3A%22Unable%20to%20preload%20CSS%22%20-%40error.message%3Achrome-extension&agg_m=count&agg_m_source=base&agg_t=count&fromUser=false&refresh_mode=paused&track=rum&from_ts=<FROM_TS>&to_ts=<TO_TS>&live=false
```

### Step 10: PREVIEW to the user — do not create yet

Print the full markdown content to the user, formatted as plain markdown they can read. Then ask exactly one question:

> Here's the draft on-call summary for `<MFE>` (<date range>). Want me to create the notebook in Datadog?

**Wait for explicit confirmation.** If the user requests edits, revise the markdown and preview again. Do not run `pup notebooks create` until the user says yes.

### Step 11: Create the notebook (only after confirmation)

Write the JSON payload to a temp file and create:

```bash
cat > /tmp/<mfe>_oncall_<startdate>.json << 'ENDJSON'
{
  "data": {
    "attributes": {
      "name": "On-call summary - Konnect <MFE display name> (<start date>, <year> - <end date>, <year>)",
      "time": {"live_span": "1w"},
      "cells": [
        {
          "attributes": {
            "definition": {
              "type": "markdown",
              "text": "<full markdown content with \\n for newlines>"
            }
          },
          "type": "notebook_cells"
        }
      ],
      "status": "published"
    },
    "type": "notebooks"
  }
}
ENDJSON
pup notebooks create --file /tmp/<mfe>_oncall_<startdate>.json
```

The `time: {live_span: 1w}` field is required — `pup notebooks create` will fail with `missing field time` if it's omitted.

Capture the notebook ID from the response and share the URL: `https://app.datadoghq.com/notebook/<id>`.

## Common errors and how to describe them

When summarizing, describe these recurring categories succinctly rather than pasting the raw message:

| Error pattern | What it really is | Suggested wording |
|---------------|-------------------|-------------------|
| `AxiosError: Request failed with status code 401` (e.g., `getControlPlanesIdGroupMemberships`) | Session timeout | "Session timeout." |
| `AxiosError: timeout of 30000ms exceeded` | Slow API or network | "Network issue." or "Slow upstream API." |
| `Failed to fetch dimensions ... CanceledError: canceled` | Request abort on navigation | "Request aborted when the user navigated away." |
| `Failed to execute 'getComputedStyle' on 'Window'` | Component unmounted before style read | "Component unmount race; doesn't impact users." |
| `Cannot read properties of undefined (reading '<x>')` | Real frontend bug | Look at the stack trace; flag for follow-up. |
| `intervention: Ignored attempt to cancel a touchmove event` | Browser passive listener warning | Generally noise; omit unless volume is very high. |
| `chrome-extension://...` | Browser extension noise | Omit. |
| `Unable to preload CSS` | Bundle prefetch noise | Omit. |

## Notes

- Default time scope: `--from 7d`. Both `1w` and `7d` work.
- Report scope: only `service:<MFE_NAME>` errors. Don't include errors owned by other services that happened to render on the MFE's pages.
- If the user wants to skip the preview, they'll say so explicitly. The default is always preview-first.
