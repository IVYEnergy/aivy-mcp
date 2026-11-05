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

1. `whoami` [GET /me]: confirms the key works and whether it is `read` or
   `control`. A `read` key cannot change anything; say so when asked to.
2. `list_sites` [GET /sites]: the site ids (`vendor:station_id`) every other
   tool takes. Sites with `demo: true` (`sandbox:...`) are shared replay
   data for trying the API: readable, never controllable, and say so if you
   quote them.
3. No sites and nothing demo? The person has not connected a system yet:
   see workflow H.

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

### H. Connecting a system
`list_vendors` [GET /vendors] first and answer from it, never from memory
(what each brand needs changes). Then `connect_site` [POST /sites]:
- Sungrow, FoxESS: serial number and the site's NMI.
- Sigenergy: the system id from the Sigenergy app.
- AlphaESS, SolaX: the response carries `authorize_url`; give it to the
  person to open and approve in a browser. The site appears in `list_sites`
  within a minute; check once, do not poll.
A site in `contested` already belongs to another account: an administrator
sorts it out, you cannot. A brand with `available: false` cannot be
connected on this deployment yet.

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
