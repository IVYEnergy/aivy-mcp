# Aivy agent playbook

How to operate a person's solar + battery system through the Aivy Developer
API or the `aivy-mcp` tools. Read this once before the first call. Tool names
are the MCP tools; the HTTP endpoint is in brackets for agents calling the
API directly (base `https://bridge.ivyenergy.com.au/v1`, header
`Authorization: Bearer aivy_sk_...`).

## 1. What you are operating

A real home battery and inverter. Every write changes what the hardware does
within about a minute and the person pays for the result on their power
bill. Treat it like driving someone else's car: look first, say what you are
about to do, wait for the yes, then report what happened.

Three energy modes:

- `selfConsumption`: the inverter's default. Solar to the house, surplus to
  the battery, then export.
- `amber_smart` (**Ai+**): Aivy's price-driven engine trades the battery
  against Amber Electric prices. Most users with Amber run this.
- `forced`: manual charge/discharge windows. Through the API it is always
  time-boxed; when the time is up the server returns the site to its
  previous mode on its own.

Units: watts, kWh, percent, Australian cents per kWh. `battery_w > 0` is
charging, `grid_w > 0` is exporting. `HH:MM` fields are in the site's local
timezone (on `get_site`).

## 2. Before anything else

1. No key at all (the tools answer 401, or the person says they have not
   signed up)? Workflow K gets them one. Nothing else works without it.
2. `whoami` [GET /me]: confirms the key works and whether it is `read` or
   `control`. A `read` key cannot change anything; say so when asked to.
3. `list_sites` [GET /sites]: the site ids (`vendor:station_id`) every other
   tool takes. Sites with `demo: true` (`sandbox:...`) are shared replay
   data for trying the API: readable, never controllable, and say so if you
   quote them.
4. Only demo sites? The person has not connected their own system yet:
   workflow H.

Do these once per conversation, not before every question.

## 3. Rules that always apply

- **Never guess numbers.** Capacity, usable kWh, earnings, best hours and
  discharge power all come from tools (`get_site`, `get_earnings`,
  `get_prices`, `plan_*`). Do not multiply a power by hours by one price; do
  not estimate capacity from SOC movement.
- **Writes need a yes.** Before `set_energy_mode`, `update_ai_plus`,
  `connect_site` or `disconnect_site`: restate exactly what will change (mode,
  windows, power, SOC limits, duration, when it reverts), ask, and call only
  after a clear yes in this conversation. One write per turn. Afterwards
  report what was applied, including `reverts_at` / `reverts_to`.
- **Forced mode always has a duration.** Default 240 minutes, maximum 1440.
  Tell the person when the site hands back and to what. Prefer the shorthand
  (`action`, `power_w`, `target_soc` / `stop_soc`, `duration_minutes`) for
  "charge now" requests; use `time_slots` for scheduled windows.
- **Do not poll.** Readings refresh about once a minute. Call `get_status`
  once per question, not in a loop; the rate limit (default 120/min) will
  stop you and the values would not change anyway.
- **Say what you could not reach.** A tool error is reported in plain words
  ("I couldn't read the battery just now"), never papered over with a
  number.
- **The server has the last word.** SOC floor and export limits apply in
  every mode and cannot be overridden; a 409 means the change was not
  applied and `detail.error` says why. Repeat the reason, do not retry
  blindly.

## 4. Workflows

### A. Status: "how's my battery / solar?"
`get_status` [GET /sites/{id}/status], add `get_site` for capacity and
usable kWh, `get_curve` (range `1d`) for the day's shape. Answer in one or
two sentences with rounded figures (3013 W → 3 kW). Mention anything the
person would want to know unprompted (battery too low for the evening, site
offline), once, with a concrete next step.

### B. Diagnosis: "why did it charge at 3am?"
`get_energy_mode` + `get_operations` [GET /sites/{id}/operations]. Each
operation carries a `reason` and the prices at the time; quote them. If the
mode is `amber_smart`, add `get_prices` to show what the engine saw.

### C. Prices and "should I sell now?"
`get_prices` [GET /sites/{id}/prices]: `now` plus the forecast with
`peak_sell_window` and `cheap_buy_window`. "How long does the high price
last?" is answered from that window's start, end and length. Any table of
prices by time uses the real times from the rows, never "now / highest".
Needs Amber linked to the site; a 409 means it is not, so say so.

### D. Earnings: "how much did it make yesterday?"
`get_earnings` [GET /sites/{id}/earnings] with `date`, and `window_start` /
`window_end` for a discharge period. Read `coverage`: under 100 %, say what
share of the day the figure covers; for today say "so far". It is an
estimate from vendor power samples, not the retailer's bill.

### E. "Sell at the best prices tonight"
1. `plan_best_price_discharge` [POST .../plans/best-price-discharge] with the
   window and any reserve SOC or minimum price the person named.
2. Reply with the headline: the windows, expected revenue, the average it
   beats, and that applying switches the site to forced mode (Ai+ pauses)
   until the windows end, then reverts. Ask for the yes.
3. On yes: `set_energy_mode` with `mode: forced`, `time_slots` = the plan's
   `proposed_time_slots` **unchanged**, and a `duration_minutes` that covers
   the last window. Report `reverts_at`.
   In discharge windows the floor is `stop_soc`; `target_soc` is for charge
   windows only.

### F. "Keep 30 % for 9 pm" / "make it last until 9 pm"
`plan_discharge_to_target` [POST .../plans/discharge-to-target] with `end`
and `target_soc`; it returns the steady power and the slot. Propose, wait for
the yes, apply as in E. If the plan says `nothing_to_do`, say so.

### G. "Charge now at 5 kW to 90 % for two hours"
Restate it, ask, then `set_energy_mode` with `mode: forced, action: charge,
power_w: 5000, target_soc: 90, duration_minutes: 120`. Report the window
and the hand-back time. For "stop" or "go back to normal": `set_energy_mode`
with the previous mode (`reverts_to` from `get_energy_mode`), permanent.

### H. Connecting a system (step by step)
Needs a `control` key; with a `read` key say so and stop.

1. **Find out what they have.** Ask the brand of the inverter or battery if
   you do not know it. Then `list_vendors` [GET /vendors] and answer from
   it, never from memory: what each brand needs changes. A brand with
   `available: false` cannot be connected on this deployment yet; say so
   and stop. A brand not in the list is not supported.
2. **Collect exactly what `connect` asks for**, and tell the person where to
   find it:
   - Sungrow, FoxESS (`fields: sn, nmi`): the inverter serial number (on
     the inverter label or in the vendor app) and the site's NMI (on the
     electricity bill, 10 or 11 characters).
   - Sigenergy (`fields: system_id`): the system id from the Sigenergy app.
   - Other serial vendors (`fields: sn`): the serial number.
   - AlphaESS, SolaX (`method: oauth`): nothing to collect; the person will
     sign in at the vendor.
   Do not guess or pad a serial number or NMI.
3. **Restate and confirm.** "Connect the Sungrow inverter A2340000001 at NMI
   2001234567 to your account?" Wait for the yes.
4. **`connect_site`** [POST /sites].
   - Serial vendors: the response has `connected` (new site ids, now yours)
     and `contested` (already held by another account; an administrator
     sorts that out, you cannot). A `warning` means the vendor accepted but
     the grant failed: ask them to contact support with the serial. A 409
     means the vendor refused; relay the message (wrong serial, device
     already registered elsewhere) and ask them to check the details.
   - OAuth vendors: the response has `authorize_url` (valid 15 minutes).
     Give it to the person to open in a browser, sign in at the vendor and
     approve Aivy. After they say it is done, `list_sites` once; the site
     appears within a minute. If it does not, ask whether the vendor page
     showed an error, and offer a fresh `connect_site` for a new link.
5. **Show it works.** `get_site` on the new id: name, capacity, SOC. Live
   readings follow once the vendor cloud answers, usually within a minute.
6. **Prices need Amber.** `get_prices` answers 409 until the person links
   their Amber Electric account, which is done in the Aivy app (site →
   Amber), not through this API. Say so when prices or Ai+ come up.

`disconnect_site` [DELETE /sites/{id}] removes a site from the account after
a yes; the vendor-side link and history stay. Demo sites cannot be removed.

### I. Ai+ tuning: "charge more aggressively", "stop selling so low"
`get_ai_plus` [GET /sites/{id}/ai-plus] first: it names the engine variant
and every lever with its range. Propose the specific lever and value in that
engine's terms; if the wish needs a different variant, say so and offer the
switch. On yes: `update_ai_plus`. A 409 with `rejected` lists what was out of
range; nothing was changed.

### J. The person has no hardware yet
Use the `demo` sites for every read workflow and say they are demos.
Control requests on them are refused by the server; explain that their own
system, once connected (H), is controllable.

### L. Alerts: "tell me when the battery drops below 20 %"
`list_alerts` [GET /sites/{id}/alerts] to see what exists, then
`create_alert` [POST /sites/{id}/alerts] with metric (`soc`, `import_price`,
`export_price`, `temperature`), operator, threshold, and `email: true` when
the person wants an email (check `email_alerts` on `whoami`; false means
the account has no verified email, so offer inbox/push only or suggest
adding an email in the app). Restate the rule before creating it ("email
you when SOC falls below 20 %, at most once every 6 hours"). Alerts only
notify; they never change what the battery does, and say so if asked for
"charge when...". "Did anything happen overnight?" is `get_alert_events`.
Demo sites do not raise alerts.

### K. No account or key yet (registration, in the chat)
The tools answer "No Aivy API key" until one is set up. With the MCP
tools this is done in the chat, in two steps, and the key is stored on the
person's computer by the server (never shown, never in the transcript).
Rules: the person chooses the password (it is also their Aivy app
password); never invent one; the terms are accepted by the person, not by
you; after it is done, do not repeat the key.

**New account:**
1. `get_registration_terms`: if `registration_available` is false, send
   them to https://bridge.ivyenergy.com.au/developer or support and stop.
   Otherwise show the terms link and ask whether they accept.
2. Ask for the email address and let them choose a password (8+
   characters). Ask whether they want the key to be read-only (default) or
   allowed to control.
3. `register_account(email, password, display_name)`. Tell them a code was
   emailed (check spam if it does not arrive; an address that already has
   an account gets no code, so offer `sign_in`).
4. Ask for the 6-digit code, then `verify_registration(registration_id,
   registration_session, code, accept_terms=true, key_name, scope)` with
   the ids from step 3. A 409 is a wrong code (five attempts) or an expired
   session (30 minutes; start over at step 3).
5. Say the account is ready and the key is saved on this computer
   (manageable at https://bridge.ivyenergy.com.au/account), then continue
   with section 2. Every new key lists the demo sites, so they can try
   things before connecting hardware (H).

**Existing account:** `sign_in(username_or_email, password, key_name,
scope)`. The password is used once to create the key and is not stored.

**HTTP-only agents** (no MCP tools) use the same endpoints:
`GET /auth/terms`, `POST /auth/register`, `POST /auth/verify` (returns
`api_key` once), or `POST /keys` with HTTP Basic for an existing account.
The web page https://bridge.ivyenergy.com.au/developer does all of this
too and is the better choice when the person prefers not to type a
password into a chat.

## 5. Errors

| Status | What to do |
|---|---|
| 401 | The key is missing, revoked or expired. Ask for a new one from bridge.ivyenergy.com.au/developer. |
| 403 | The key is read-only. Say the change needs a control key. |
| 404 | Unknown site or not this account's. Re-run `list_sites`. |
| 409 | Not applied. Relay `detail.error` and any `rejected` / `hint`. |
| 422 | Your request was malformed; fix the fields named. |
| 429 | Rate limited. Wait `Retry-After` seconds; do not loop. |
| 503 | No reading right now. Say so and offer to check again in a minute. |

## 6. Style

Plain, warm, short: one to three sentences unless real detail is asked for.
Use the person's language. Lead with the answer, then the one thing worth
noticing, then the next step. Never list every window of a plan twice.
