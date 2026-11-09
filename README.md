# aivy-mcp

<!-- mcp-name: io.github.IVYEnergy/aivy-mcp -->

An [MCP](https://modelcontextprotocol.io) server that lets your own AI assistant
(Claude Code, Claude Desktop, ChatGPT, Codex, Cursor, ...) read and control
**your own** solar + battery system through the
[Aivy Developer API](https://bridge.ivyenergy.com.au/v1/docs) by Ivy Energy.

Works with every brand Aivy runs: Sungrow, Sigenergy, FoxESS, AlphaESS, SolaX
and more. The same tools, whatever the inverter.

## 1. Get a key (or let the chat do it)

**In the chat.** Add the server without any key (step 2) and say "sign me up
to Aivy" or "sign in to my Aivy account". The assistant walks through the
terms, the emailed code and the permission level (read only, or read and
control), and the server stores the key on your computer
(`~/.aivy/credentials.json`, readable by you only). The key is never shown
in the chat.

**On the web.** Open [bridge.ivyenergy.com.au/developer](https://bridge.ivyenergy.com.au/developer),
sign up or sign in, and create a key. Pick **Read only** to let an assistant
look, or **Read and control** to let it change the energy mode, Ai+ settings
and connect systems. Then pass it as `AIVY_API_KEY` (step 2). The key sees
exactly what your account sees, nothing more.

No hardware yet? Every key also lists demo sites (`sandbox:...`, replayed real
data, read-only), so you can try everything first. Accounts and keys can also
be created from the API itself; see the
[Quickstart](https://bridge.ivyenergy.com.au/v1/docs#description/quickstart).

## 2. Add the server

**One line** (macOS, Linux; installs `uv` if missing, then writes the server
into every MCP client it finds: Codex, Claude Code, Claude Desktop, Cursor):

```bash
curl -fsSL https://raw.githubusercontent.com/IVYEnergy/aivy-mcp/main/install.sh | sh
```

Already have `uv`? `uvx --from aivy-mcp aivy-mcp-setup` does the same, and
`aivy-mcp setup --client codex --key aivy_sk_...` targets one client or
stores an existing key. Files are backed up before they change. Restart the
client, then say "sign me up to Aivy". An assistant that can run shell
commands (Claude Code, Codex) can run the line for you.

Or by hand. Claude Code (drop `-e AIVY_API_KEY=...` to sign up or sign in from the chat instead):

```bash
claude mcp add aivy -e AIVY_API_KEY=aivy_sk_... -- uvx aivy-mcp
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.aivy]
command = "uvx"
args = ["aivy-mcp"]
# optional: [mcp_servers.aivy.env] AIVY_API_KEY = "aivy_sk_..."
```

Claude Desktop / Cursor / any MCP client (`mcpServers` block):

```json
{
  "mcpServers": {
    "aivy": {
      "command": "uvx",
      "args": ["aivy-mcp"],
      "env": { "AIVY_API_KEY": "aivy_sk_..." }
    }
  }
}
```

No `uv`? `pipx run aivy-mcp` or `pip install aivy-mcp && aivy-mcp` work too.

Assistants that cannot run local tools (ChatGPT in the browser) can use the
HTTP API directly. Paste this into the chat:

> Use the Aivy Developer API at https://bridge.ivyenergy.com.au/v1 with the
> header `Authorization: Bearer aivy_sk_...`. Read
> https://bridge.ivyenergy.com.au/v1/llms.txt first, then GET /sites.

## 3. Ask

- "How's my battery doing?"
- "Why did it charge at 3am?"
- "What are prices doing tonight? Should I sell?"
- "Plan the best time to sell tonight, keep 20%, then apply it."
- "Charge at 5 kW to 90% for the next two hours." (control key)
- "Make Ai+ a bit more aggressive on exports." (control key)
- "Connect my Sungrow inverter, serial A2340000001, NMI 2001234567." (control key)
- "Email me when the battery drops below 20%."

## For agents: the playbook

Tool descriptions say what each call does. The **playbook** says how to get
a job done with them: which tools answer which questions, when to stop and
confirm, how forced mode and its hand-back work, what each error means, and
the step-by-step workflows (status, diagnosis, prices, earnings, selling
tonight, keeping a reserve, charging now, connecting a system, Ai+ tuning).
It is one text, delivered four ways:

| Where | How to use it |
|---|---|
| MCP tool `get_agent_guide` | Any MCP client: the server's instructions ask the model to call it once per conversation. |
| Claude Code / Claude skill | Copy [`skills/aivy`](skills/aivy) into `~/.claude/skills/` (or a project's `.claude/skills/`). |
| Codex, Cursor, other rule files | Paste [`src/aivy_mcp/playbook.md`](src/aivy_mcp/playbook.md) into `AGENTS.md` or your rules. |
| HTTP-only assistants (ChatGPT in the browser) | Tell it to read https://bridge.ivyenergy.com.au/v1/agent-guide.md first. |

### Checking an agent against it

[`evals/cases.json`](evals/cases.json) holds conversation cases (the user's
words, the tools a good agent calls in order, the ones it must not call, and
whether the turn has to end in a question). [`evals/check.py`](evals/check.py)
judges a transcript:

```bash
# Claude Code, headless, with the server configured as "aivy":
claude -p "Sell at the best prices tonight, keep 20%." --output-format stream-json > t.json
python evals/check.py sell-tonight-propose t.json
```

A transcript can also be a plain list: `[{"name": "get_status", "args": {}}]`,
or `{"calls": [...], "text": "final reply"}`. Run the cases after changing
the playbook, the tool descriptions or the model.

## Cards in chat

Hosts that render [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
(and ChatGPT through its Apps SDK) show thirteen of the tools as cards instead
of text, in the same style as the Aivy mobile app, light and dark:

| Tool | Card |
|---|---|
| `get_status` | SOC ring and the four power flows (solar, home, grid, battery) |
| `get_prices` | Buying / selling now, the next hours as bars, the best hours to sell highlighted |
| `plan_best_price_discharge` | Revenue headline, the chosen windows on a timeline and an **Apply** button; the click is the user's yes and calls `set_energy_mode` |
| `get_registration_terms` | A sign-up / sign-in form: email, password, permission level and the terms are entered in the card and sent to Aivy by the card itself, never through the chat |
| `get_energy_mode` | Control panel: today's windows on a 24-hour bar, the pending hand-back, mode buttons and a manual charge / discharge form; the click is the user's yes |
| `list_vendors` | Connect a system: pick the brand, enter what it needs (or sign in at the vendor), see the new site appear |
| `get_curve` | Power history: solar, home, battery, grid lines with SOC |
| `get_earnings` | A day's net, earned, cost, exported and imported, hour by hour |
| `get_operations` | Timeline of what the platform did and why |
| `get_ai_plus` | Ai+ levers as sliders and switches, built from the engine's own ranges; Save |
| `plan_discharge_to_target` | The hold-a-reserve plan with Apply |
| `list_alerts` | Alert rules with Delete, and a form to add one (email optional) |
| `list_sites` | Your sites with a Status button each |

Every card is one self-contained HTML document (no network, no assets), so
it works under the extension's default content security policy. Hosts
without the extension get the same data as JSON. To look at the cards
locally: `node tests/ui/shoot.mjs` (needs `playwright`).

## Tools

| Tool | Changes anything? | What it does |
|---|---|---|
| `get_agent_guide` | no | The playbook above |
| `whoami` | no | Who the key is, read or control; says when there is no key yet |
| `get_registration_terms`, `register_account`, `verify_registration` | account only | Create an Aivy account from the chat; the key is stored locally |
| `sign_in` | account only | Create and store a key for an existing account (password used once) |
| `list_sites` | no | Your sites; ids look like `sungrow:1445844` |
| `get_site` | no | Capacity, limits, timezone, mode, live SOC |
| `get_status` | no | Live SOC and power flows |
| `get_curve` | no | 5-minute or daily history |
| `get_earnings` | no | A day's energy, priced at Amber's actual prices |
| `get_operations` | no | What the platform did and why |
| `get_prices` | no | Amber prices now and forecast |
| `list_alerts`, `get_alert_events` | no | Alert rules on a site and what fired |
| `create_alert`, `delete_alert` | account only | Threshold alerts to inbox, push and email; never control the battery |
| `send_test_alert_email` | no | One test alert to the account's email |
| `list_vendors` | no | Supported brands and what connecting one needs |
| `connect_site` | **yes** | Link the user's system (serial + NMI, system id, or an OAuth link to open) |
| `disconnect_site` | **yes** | Remove a site from the account |
| `get_energy_mode` | no | Mode, windows, pending hand-back |
| `set_energy_mode` | **yes** | selfConsumption / amber_smart (Ai+) / forced (time-boxed) |
| `get_ai_plus` | no | Ai+ variant, settings and tunable ranges |
| `update_ai_plus` | **yes** | Change Ai+ levers or variant |
| `plan_best_price_discharge` | no | Propose a sell schedule from the forecast |
| `plan_discharge_to_target` | no | Propose a discharge that lands on a SOC at a time |

## What the server enforces, whatever the assistant does

- A key sees only its owner's sites; writes need the control scope.
- A forced (manual) mode set through the API **always reverts** to the previous
  mode after `duration_minutes` (default 4 h, max 24 h).
- The SOC floor and grid export limits cannot be overridden.
- Every call is recorded with the key id and client; keys can be revoked in
  the app at any time and stop working immediately.
- Per-key rate limit (default 120/min). Readings refresh about once a minute.

## Development

```bash
git clone https://github.com/IVYEnergy/aivy-mcp && cd aivy-mcp
uv run --with pytest pytest
uv run aivy-mcp            # needs AIVY_API_KEY
```

The package also lives in Ivy Energy's main repository under `sdk/aivy-mcp`;
this public repository is where it is released from.

Point at another deployment with `AIVY_API_URL=https://host/v1`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
