// The card must complete the MCP Apps handshake even when the host also
// injects window.openai (ChatGPT and Codex do both; Codex tears the frame
// down after 5 s without ui/initialize). Needs playwright, like shoot.mjs:
//   node tests/ui/handshake.mjs
import { chromium } from "playwright";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const card = execFileSync("python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(join(here, "../../src"))}); from aivy_mcp.ui import render_template; print(render_template("status"))`]).toString();
const sample = { site_id: "sandbox:SBX-001", name: "Demo", soc: 61, battery_w: -1200, pv_w: 3000, load_w: 800, grid_w: -1000 };
const host = `<!doctype html><meta charset="utf-8"><body><iframe id="f" src="/card.html" style="width:600px;height:50px;border:0"></iframe><script>
window.LOG = [];
const f = document.getElementById('f');
window.addEventListener('message', (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== '2.0') return;
  window.LOG.push(m.method || ('reply:' + m.id));
  if (m.method === 'ui/initialize') f.contentWindow.postMessage({ jsonrpc: '2.0', id: m.id, result: { protocolVersion: '2026-01-26', hostInfo: { name: 'codex-like' }, hostCapabilities: {}, hostContext: {} } }, '*');
  else if (m.method === 'ui/notifications/initialized') f.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { structuredContent: ${JSON.stringify(sample)} } }, '*');
});
</script></body>`;

const server = createServer((req, res) => {
  res.setHeader("content-type", "text/html");
  if (req.url.startsWith("/host.html")) return res.end(host);
  if (req.url.startsWith("/card.html")) return res.end(card);
  res.statusCode = 404; res.end();
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;
const browser = await chromium.launch();

for (const withOpenai of [false, true]) {
  const page = await browser.newPage({ viewport: { width: 640, height: 800 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  if (withOpenai) {
    // Only the card frame gets window.openai, as in ChatGPT's sandbox.
    await page.addInitScript(() => {
      if (window.parent === window) return;
      window.__calls = [];
      window.openai = {
        theme: "dark", toolOutput: null,
        callTool: (n, a) => { window.__calls.push(n); return Promise.resolve({ content: [] }); },
        openExternal: () => {}, notifyIntrinsicHeight: () => { window.__height = true; },
      };
    });
  }
  await page.goto(`http://127.0.0.1:${port}/host.html`);
  await page.waitForFunction(() => window.LOG.includes("ui/notifications/initialized"), null, { timeout: 4000 });
  await page.waitForTimeout(400);
  const log = await page.evaluate(() => window.LOG);
  assert.ok(log.includes("ui/initialize"), `handshake sent (openai=${withOpenai})`);
  assert.ok(log.includes("ui/notifications/size-changed"), `size reported (openai=${withOpenai})`);
  const frame = page.frames().find((fr) => fr !== page.mainFrame());
  const state = await frame.evaluate(() => ({ host: window.AivyBridge.host, data: !!window.AivyBridge.data, theme: document.documentElement.getAttribute("data-theme"), height: !!window.__height }));
  assert.equal(state.data, true, "tool result reached the card");
  assert.equal(state.host, withOpenai ? "mcp-apps+openai" : "mcp-apps");
  if (withOpenai) { assert.equal(state.theme, "dark", "window.openai theme honoured"); assert.equal(state.height, true, "height also sent through window.openai"); }
  assert.deepEqual(errors, []);
  console.log(`ok  handshake with window.openai=${withOpenai}: ${log.filter((m) => !m.startsWith("reply")).join(", ")}`);
  await page.close();
}
await browser.close();
server.close();
