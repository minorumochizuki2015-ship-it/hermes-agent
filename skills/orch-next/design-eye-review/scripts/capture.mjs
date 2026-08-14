#!/usr/bin/env node
// design-eye-review capture: multi-viewport screenshots (fold + full page).
//
// Usage:
//   node capture.mjs <base-url> [outdir] [--routes=/,/menu,/access] [--wait=2500]
//
// Default wait is 2500ms, not a short "page settled" guess: staggered CSS
// entrance animations (fade-in-up hero text with animation-delay stacks of
// 0.3s/0.6s/0.9s/1.2s/1.5s+ a ~1s animation duration are common) can still be
// mid-fade or fully pre-animation (opacity:0) at 1000-1200ms, producing a
// screenshot that misses real content non-deterministically — this bit a
// live design-eye-review run against onsen-spa, where a hero subtitle
// (animation-delay: 1.2s) intermittently vanished. If a site's slowest
// hero delay class exceeds ~1.5s, pass a longer --wait explicitly.
//
// Output per route: <outdir>/<route>__mobile__fold.png, __mobile__full.png,
//                   __desktop__fold.png, __desktop__full.png
//
// Requires playwright (resolved from cwd/node_modules or global). Falls back
// to `npx playwright screenshot` if the module is not importable. If browsers
// are missing, run: npx playwright install chromium

import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const argv = process.argv.slice(2);
if (argv.length === 0 || argv[0].startsWith('--')) {
  console.error('Usage: node capture.mjs <base-url> [outdir] [--routes=/,/a] [--wait=2500]');
  process.exit(2);
}
const baseUrl = argv[0].replace(/\/$/, '');
const outdir = argv[1] && !argv[1].startsWith('--') ? argv[1] : 'design-review-shots';
const routesArg = argv.find(a => a.startsWith('--routes='));
const waitArg = argv.find(a => a.startsWith('--wait='));
const routes = routesArg ? routesArg.slice('--routes='.length).split(',') : ['/'];
const waitMs = waitArg ? parseInt(waitArg.slice('--wait='.length), 10) : 2500;

const viewports = [
  { name: 'mobile', width: 375, height: 812 },
  { name: 'desktop', width: 1440, height: 900 },
];

const slug = r => (r === '/' || r === '' ? 'home' : r.replace(/^\//, '').replace(/[^a-zA-Z0-9_-]+/g, '-'));
mkdirSync(outdir, { recursive: true });

function routeUrl(route) {
  if (/^(https?|file):/.test(route)) return route;
  return route === '/' ? baseUrl : baseUrl + route;
}

async function withPlaywright(pw) {
  const browser = await pw.chromium.launch();
  const shots = [];
  try {
    for (const route of routes) {
      const url = routeUrl(route);
      for (const vp of viewports) {
        const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
        try {
          await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
        } catch {
          await page.goto(url, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
        }
        await page.waitForTimeout(waitMs);
        const base = resolve(outdir, `${slug(route)}__${vp.name}`);
        await page.screenshot({ path: `${base}__fold.png` });
        await page.screenshot({ path: `${base}__full.png`, fullPage: true });
        shots.push(`${base}__fold.png`, `${base}__full.png`);
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
  return shots;
}

function withCli() {
  const shots = [];
  for (const route of routes) {
    const url = routeUrl(route);
    for (const vp of viewports) {
      const base = resolve(outdir, `${slug(route)}__${vp.name}`);
      for (const [suffix, extra] of [['fold', []], ['full', ['--full-page']]]) {
        const out = `${base}__${suffix}.png`;
        const res = spawnSync('npx', [
          'playwright', 'screenshot',
          `--viewport-size=${vp.width},${vp.height}`,
          `--wait-for-timeout=${waitMs}`,
          ...extra, url, out,
        ], { stdio: 'inherit' });
        if (res.status !== 0) {
          console.error(`FAILED: ${out} — if browsers are missing run: npx playwright install chromium`);
          process.exit(1);
        }
        shots.push(out);
      }
    }
  }
  return shots;
}

let shots;
try {
  const pw = await import('playwright');
  shots = await withPlaywright(pw);
} catch (e) {
  if (e && (e.code === 'ERR_MODULE_NOT_FOUND' || /Cannot find (module|package)/.test(String(e)))) {
    console.error('playwright module not found — falling back to `npx playwright screenshot`');
    shots = withCli();
  } else {
    console.error(String(e && e.message || e));
    console.error('If browsers are missing run: npx playwright install chromium');
    process.exit(1);
  }
}

console.log('\nCaptured:');
for (const s of shots) console.log('  ' + s);
console.log('\nNow VIEW every image before writing any review conclusion.');
