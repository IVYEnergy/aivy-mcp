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
for (const name of ["status", "plan", "prices", "signup", "control", "connect", "curve", "earnings", "operations", "aiplus", "alerts", "sites"]) {
  rendered[name] = execFileSync("python3", ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(join(here, "../../src"))}); from aivy_mcp.ui import render_template; print(render_template(${JSON.stringify(name)}))`]).toString();
}
const server = createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/host.html") { res.setHeader("content-type", "text/html"); return res.end(readFileSync(join(here, "host.html"))); }
  const m = /^\/(status|plan|prices|signup|control|connect|curve|earnings|operations|aiplus|alerts|sites)\.html$/.exec(url.pathname);
  if (m) { res.setHeader("content-type", "text/html"); return res.end(rendered[m[1]]); }
  res.statusCode = 404; res.end();
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;
const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  for (const card of ["status", "plan", "prices", "signup", "control", "connect", "curve", "earnings", "operations", "aiplus", "alerts", "sites", "target"]) {
    const page = await browser.newPage({ viewport: { width: 640, height: 800 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`http://127.0.0.1:${port}/host.html?card=${card}&theme=${theme}`);
    await page.waitForFunction(() => parseInt(document.getElementById("f").style.height || "0", 10) > 100, null, { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(900);
    const h = await page.evaluate(() => document.getElementById("f").style.height);
    let calls = 0;
    if (card === "signup") {
      await page.screenshot({ path: join(out, `card-signup-form-${theme}.png`) });
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.fill("#email", "bob@example.com"); await frame.fill("#pw", "a long private phrase"); await frame.check("#accept");
      await frame.click("#go"); await page.waitForTimeout(300);
      await frame.fill("#code", "246810"); await frame.click("#confirm"); await page.waitForTimeout(300);
      const names = await page.evaluate(() => window.calls.map((c) => c.name + ":" + Object.keys(c.arguments).join(",")));
      console.log(`signup flow -> ${names.join(" | ")} | shown: ${await frame.locator("#done .big").innerText()}`);
    }
    if (card === "control") {
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.click('.seg button[data-mode="forced"]'); await page.waitForTimeout(200);
      await page.screenshot({ path: join(out, `card-control-manual-${theme}.png`) });
      await frame.click("#apply"); await page.waitForTimeout(300);
      console.log("control apply ->", JSON.stringify((await page.evaluate(() => window.calls))[0]?.arguments));
    }
    if (card === "connect") {
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.click('.v[data-id="sungrow"]'); await frame.fill('input[data-field="sn"]', "A234"); await frame.fill('input[data-field="nmi"]', "2001234567");
      await frame.click("#go"); await page.waitForTimeout(400);
      console.log("connect ->", (await page.evaluate(() => window.calls.map((c) => c.name))).join(","), "|", await frame.locator("#result").innerText());
    }
    if (card === "aiplus") {
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.fill('.lever input[type=number]', "15"); await frame.locator('.lever input[type=number]').first().dispatchEvent("change");
      await frame.click("#save"); await page.waitForTimeout(300);
      console.log("aiplus save ->", JSON.stringify((await page.evaluate(() => window.calls))[0]?.arguments), "|", await frame.locator("#state").innerText());
    }
    if (card === "alerts") {
      const frame = page.frames().find((fr) => fr !== page.mainFrame());
      await frame.fill("#name", "Low tonight"); await frame.click("#add"); await page.waitForTimeout(400);
      console.log("alerts add ->", (await page.evaluate(() => window.calls.map((c) => c.name))).join(","));
    }
    if (card === "plan" || card === "target") {
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
