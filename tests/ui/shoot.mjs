// Render each card inside the test host and screenshot it. Needs playwright:
//   node tests/ui/shoot.mjs [outdir]
import { chromium } from "playwright";
import { createServer } from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] || here;
// Rendered templates come from the package loader, so the test sees exactly what a host gets.
const rendered = {};
for (const name of ["status", "plan", "prices"]) {
  rendered[name] = execFileSync("python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(join(here, "../../src"))}); from aivy_mcp.ui import render_template; print(render_template(${JSON.stringify(name)}))`]).toString();
}
const server = createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/host.html") { res.setHeader("content-type", "text/html"); return res.end(readFileSync(join(here, "host.html"))); }
  const m = /^\/(status|plan|prices)\.html$/.exec(url.pathname);
  if (m) { res.setHeader("content-type", "text/html"); return res.end(rendered[m[1]]); }
  res.statusCode = 404; res.end();
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;
const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  for (const card of ["status", "plan", "prices"]) {
    const page = await browser.newPage({ viewport: { width: 640, height: 800 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`http://127.0.0.1:${port}/host.html?card=${card}&theme=${theme}`);
    await page.waitForFunction(() => parseInt(document.getElementById("f").style.height || "0", 10) > 100, null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(900);
    const h = await page.evaluate(() => document.getElementById("f").style.height);
    let calls = 0;
    if (card === "plan") {
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.click("#apply");
      await page.waitForTimeout(400);
      calls = await page.evaluate(() => window.calls.length);
      const state = await frame.locator("#state").innerText();
      console.log(`plan apply -> tools/call recorded: ${calls}, state: ${state}`);
    }
    await page.screenshot({ path: join(out, `card-${card}-${theme}.png`) });
    console.log(`${card} ${theme}: height ${h}, errors ${errors.length}${errors.length ? " " + errors.slice(0, 2).join(" | ") : ""}`);
    await page.close();
  }
}
await browser.close();
server.close();
