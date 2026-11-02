// Records a captioned demo of the web UI answering real questions, and saves
// screenshots of each answer for the README.
//
//   askdata-web &                       # needs the API key (see docs/RUNBOOK.md)
//   node scripts/demo.cjs http://127.0.0.1:8000 docs/media
//
// Writes <out>/demo.webm (and demo.mp4 if ffmpeg is installed) plus <out>/answer-*.png.

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const OUT = path.resolve(process.argv[3] || "docs/media");
fs.mkdirSync(OUT, { recursive: true });

const SCENES = [
  { caption: "Ask a business question in plain English",
    question: "What were the top 5 products by net revenue in FY2026?",
    after: "It applies the company's definition of net revenue and shows the SQL behind every number", openSql: true },
  { caption: "Ambiguous names get a clarifying question, not a guess",
    question: "What were Priya's sales last quarter?", pickOption: 0,
    after: "Pick an option and it answers for that person" },
  { caption: "Trends come back as charts",
    question: "Show monthly net revenue for FY2026.",
    after: "Fiscal years run April to March, as the glossary defines them" },
  { caption: "Governed metrics, such as target attainment",
    question: "What was each region's target attainment in Q1 FY2027?",
    after: "Every query runs read-only, behind a guardrail enforced by SQLite", openSteps: true },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    colorScheme: "light",
    recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
  });
  const page = await context.newPage();
  await page.goto(BASE);
  await page.waitForFunction(() => window.__askdata && document.querySelectorAll("#examples .chip").length > 0);
  await page.addStyleTag({ content: `
    #caption { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 99;
      max-width: 1000px; padding: 12px 22px; border-radius: 999px; font: 600 22px/1.3 system-ui, sans-serif;
      background: rgba(11,11,11,0.86); color: #fff; text-align: center; transition: opacity .3s; }` });
  await page.evaluate(() => { const c = document.createElement("div"); c.id = "caption"; document.body.append(c); });
  const caption = (t) => page.evaluate((text) => { document.querySelector("#caption").textContent = text; }, t);

  const finished = () => page.evaluate(() => document.querySelectorAll("#results .card:not(.pending)").length);
  const waitForAnswer = async (before) => {
    await page.waitForFunction((n) => document.querySelectorAll("#results .card:not(.pending)").length > n,
      before, { timeout: 240000 });
    await sleep(600);
  };
  const ask = async (q) => {
    await page.fill("#q", "");
    await page.type("#q", q, { delay: 28 });
    await sleep(400);
    const before = await finished();
    await page.click("#go");
    await waitForAnswer(before);
  };

  await caption("Ask your data: a Claude agent over a sales database");
  await sleep(3500);
  let shot = 1;
  for (const s of SCENES) {
    await caption(s.caption);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    await sleep(800);
    await ask(s.question);
    let card = page.locator("#results .card").first();
    await card.scrollIntoViewIfNeeded();
    await sleep(2500);
    if (s.pickOption !== undefined) {
      const opt = card.locator(".options .chip").nth(s.pickOption);
      if (await opt.count()) {
        await caption(s.after);
        const before = await finished();
        await opt.click();
        await waitForAnswer(before);
        card = page.locator("#results .card").first();
        await card.scrollIntoViewIfNeeded();
        await sleep(2500);
      }
    } else {
      await caption(s.after);
    }
    const open = async (label) => {
      const summary = card.locator("details summary", { hasText: label });
      if (await summary.count()) { await summary.first().click(); await sleep(2500); }
    };
    if (s.openSql) await open("SQL behind");
    if (s.openSteps) await open("How it got there");
    await page.evaluate(() => { document.querySelector("#caption").style.visibility = "hidden"; });
    await card.screenshot({ path: path.join(OUT, `answer-${shot++}.png`) });
    await page.evaluate(() => { document.querySelector("#caption").style.visibility = "visible"; });
    await sleep(3000);
  }
  await caption("Haiku 5.5 answers first and hands hard questions to Sonnet 5.5");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await sleep(4000);

  const video = page.video();
  await context.close();
  const webm = path.join(OUT, "demo.webm");
  fs.renameSync(await video.path(), webm);
  await browser.close();
  try {
    execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-i", webm, "-c:v", "libx264", "-crf", "28", "-preset", "slow",
      "-pix_fmt", "yuv420p", "-movflags", "+faststart", path.join(OUT, "demo.mp4")]);
    console.log("Wrote demo.mp4");
  } catch (e) {
    console.log("ffmpeg not available or failed; kept demo.webm");
  }
  for (const f of fs.readdirSync(OUT)) console.log(f, (fs.statSync(path.join(OUT, f)).size / 1e6).toFixed(2), "MB");
})().catch((e) => { console.error(e); process.exit(1); });
