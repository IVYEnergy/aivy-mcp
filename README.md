# aivy-mcp

An [MCP](https://modelcontextprotocol.io) server that lets your own AI assistant
(Claude Code, Claude Desktop, ChatGPT, Codex, Cursor, ...) read and control
**your own** solar + battery system through the
[Aivy Developer API](https://bridge.ivyenergy.com.au/v1/docs) by Ivy Energy.

Works with every brand Aivy runs: Sungrow, Sigenergy, FoxESS, AlphaESS, SolaX
and more. The same tools, whatever the inverter.

## 1. Get a key

Open [bridge.ivyenergy.com.au/developer](https://bridge.ivyenergy.com.au/developer),
sign up or sign in, and create a key. Pick **Read only** to let an assistant
look, or **Read and control** to let it change the energy mode, Ai+ settings
and connect systems. The key sees exactly what your account sees, nothing more.

No hardware yet? Every key also lists demo sites (`sandbox:...`, replayed real
data, read-only), so you can try everything first. Accounts and keys can also
be created from the API itself; see the
[Quickstart](https://bridge.ivyenergy.com.au/v1/docs#description/quickstart).

## 2. Add the server

Claude Code:

```bash
claude mcp add aivy -e AIVY_API_KEY=aivy_sk_... -- uvx aivy-mcp
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

## Tools

| Tool | Changes anything? | What it does |
|---|---|---|
| `get_agent_guide` | no | The playbook above |
| `whoami` | no | Who the key is, read or control |
| `list_sites` | no | Your sites; ids look like `sungrow:1445844` |
| `get_site` | no | Capacity, limits, timezone, mode, live SOC |
| `get_status` | no | Live SOC and power flows |
| `get_curve` | no | 5-minute or daily history |
| `get_earnings` | no | A day's energy, priced at Amber's actual prices |
| `get_operations` | no | What the platform did and why |
| `get_prices` | no | Amber prices now and forecast |
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
