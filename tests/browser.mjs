import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
import assert from "node:assert/strict";
const root = resolve("docs");
const server = createServer(async (req, res) => {
  try {
    const path = resolve(
      root,
      "." +
        decodeURIComponent(
          new URL(req.url, "http://localhost").pathname.replace(
            /\/$/,
            "/index.html",
          ),
        ),
    );
    if (!path.startsWith(root + "/")) throw Error("path");
    res.setHeader(
      "Content-Type",
      {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
      }[extname(path)] || "application/octet-stream",
    );
    res.end(await readFile(path));
  } catch {
    res.writeHead(404);
    res.end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base =
  process.env.FERRYWIRE_URL || `http://127.0.0.1:${server.address().port}/`;
const shots = resolve(process.env.FERRYWIRE_SHOTS || "build/screenshots");
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true });
let checks = 0;
const errors = [],
  failed = [],
  requests = [];
const ok = (v, m) => {
  assert.ok(v, m);
  checks++;
};
try {
  for (const width of [1440, 1024, 768, 390, 320]) {
    const page = await browser.newPage({
      viewport: { width, height: 1080 },
      deviceScaleFactor: 1,
      acceptDownloads: true,
    });
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    page.on("request", (r) => requests.push(r.url()));
    page.on("requestfailed", (r) => failed.push(r.url()));
    await page.goto(base);
    await page.waitForFunction(() =>
      document.querySelector("#bytes").textContent.endsWith("bytes"),
    );
    await page.screenshot({ path: resolve(shots, `top-${width}.png`) });
    await page.screenshot({
      path: resolve(shots, `full-${width}.png`),
      fullPage: true,
    });
    await page.keyboard.press("Tab");
    ok(
      await page.locator(".skip").evaluate((e) => e === document.activeElement),
      "skip first",
    );
    await page.keyboard.press("Enter");
    ok(new URL(page.url()).hash === "#lab", "skip works");
    const idle = () =>
      page.waitForFunction(() => !document.querySelector("#reset").disabled);
    const run = async (scenario) => {
      await page.locator("#scenario").selectOption(scenario);
      await page.locator("#run").click();
      await idle();
    };
    await run("data-loss");
    ok(
      (await page.locator("#sender-state").innerText()) === "Confirmed",
      "data loss confirmed",
    );
    ok(
      Number(await page.locator("#retries").innerText()) === 1,
      "one retransmission",
    );
    ok(
      (await page.locator("#receiver-state").innerText()) === "Delivered",
      "delivered",
    );
    ok(
      (await page.locator("#received").inputValue()) ===
        (await page.locator("#message").inputValue()),
      "exact receiver bytes",
    );
    ok(
      (await page.locator("#trace-body").innerText()).includes("drop"),
      "trace shows fault",
    );
    await page.screenshot({ path: resolve(shots, `lab-${width}.png`) });
    for (const scenario of [
      "clean",
      "ack-loss",
      "corruption",
      "duplicate",
      "reorder",
      "final-ack",
    ]) {
      await run(scenario);
      ok(
        (await page.locator("#sender-state").innerText()) === "Confirmed",
        scenario + " confirmed",
      );
      ok(
        (await page.locator("#received").inputValue()) ===
          (await page.locator("#message").inputValue()),
        scenario + " exact",
      );
      if (["ack-loss", "duplicate", "reorder", "final-ack"].includes(scenario))
        ok(
          Number(await page.locator("#duplicates").innerText()) >= 1,
          scenario + " duplicate suppressed",
        );
    }
    await run("blackhole");
    ok(
      (await page.locator("#sender-state").innerText()) === "Unconfirmed",
      "blackhole fail",
    );
    ok(
      (await page.locator("#receiver-state").innerText()) === "Not delivered",
      "blackhole no delivery",
    );
    ok((await page.locator("#sent").innerText()) === "6", "retry cap6");
    ok(
      (await page.locator("#received").inputValue()) === "",
      "no stale received message",
    );
    await run("final-blackhole");
    ok(
      (await page.locator("#sender-state").innerText()) === "Unconfirmed",
      "final ACK uncertainty",
    );
    ok(
      (await page.locator("#receiver-state").innerText()) === "Delivered",
      "final ACK lost but delivered",
    );
    ok(
      (await page.locator("#notice").innerText()).includes("UNCONFIRMED"),
      "clear split state",
    );
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#download").click();
    const d = await downloadPromise,
      stream = await d.createReadStream();
    const chunks = [];
    for await (const c of stream) chunks.push(c);
    const doc = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    ok(
      doc.format === "ferrywire-virtual-trace-1" &&
        doc.input.scenario === "final-blackhole" &&
        doc.result.delivered &&
        !doc.result.confirmed,
      "actual downloaded trace semantics",
    );
    ok(
      doc.result.events.length ===
        Number((await page.locator("#event-count").innerText()).split(" ")[0]),
      "download event count",
    );
    await page.locator("#message").fill("x".repeat(4097));
    ok(await page.locator("#run").isDisabled(), "oversize blocks run");
    ok(await page.locator("#download").isDisabled(), "stale download disabled");
    ok(
      (await page.locator("#notice").innerText()).includes("4096"),
      "size error",
    );
    const content =
      '<img src="https://invalid.example/pixel" onerror="alert(1)">\n\ufeff😀';
    await page.locator("#message").fill(content);
    await run("corruption");
    ok(
      (await page.locator("#received").inputValue()) === content,
      "literal HTML BOM emoji",
    );
    ok((await page.locator("img").count()) === 0, "no injected img");
    await page.locator("#message").fill("");
    await run("clean");
    ok(
      (await page.locator("#sender-state").innerText()) === "Confirmed",
      "empty message",
    );
    ok(
      (await page.locator("#sent").innerText()) === "2",
      "empty START FIN only",
    );
    page.once("dialog", (d) => d.dismiss());
    await page.locator("#reset").click();
    ok((await page.locator("#message").inputValue()) === "", "reset cancelled");
    page.once("dialog", (d) => d.accept());
    await page.locator("#reset").click();
    ok(
      (await page.locator("#message").inputValue()).startsWith("Across"),
      "reset sample",
    );
    ok(
      await page.locator("#download").isDisabled(),
      "reset clears trace export",
    );
    const layout = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      width: innerWidth,
      controls: [...document.querySelectorAll("button,select")].map(
        (e) => e.getBoundingClientRect().height,
      ),
      hero: document.querySelector("h1").getBoundingClientRect().height,
      line: parseFloat(
        getComputedStyle(document.querySelector("h1")).lineHeight,
      ),
    }));
    ok(layout.scroll <= layout.width, "no overflow" + width);
    ok(
      layout.controls.every((h) => h >= 43.5),
      "44px controls" + width,
    );
    ok(layout.hero / layout.line < 4.1, "hero max4lines" + width);
    await page.close();
  }
  ok(errors.length === 0, "no browser errors " + errors.join(";"));
  ok(failed.length === 0, "no failed requests " + failed.join(";"));
  ok(
    requests.every((u) => u.startsWith(base) || u.startsWith("blob:")),
    "no runtime thirdparty",
  );
  console.log(
    `${checks} Chromium checks PASS; 1440/1024/768/390/320; zero browser errors, failed or third-party requests`,
  );
} finally {
  await browser.close();
  server.close();
}
