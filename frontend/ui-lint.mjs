import fs from "node:fs";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");

const index = read("frontend/index.html");
const baseCss = read("frontend/styles.css");
const themeCss = read("frontend/ui-rbot.css");
const appJs = read("frontend/app.js");
const rcJs = read("frontend/rc.js");
const ui2Js = read("frontend/ui2.js");

const fail = (msg) => {
  console.error("UI_LINT_FAIL:", msg);
  process.exitCode = 1;
};

const assert = (ok, msg) => {
  if (!ok) fail(msg);
};

const hsl = (r, g, b) => {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let h = 0;
  let s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  return [h, s, l];
};

const collectGreenTokens = (css) => {
  const hits = new Set();

  for (const match of css.matchAll(/#[0-9a-fA-F]{6}\b/g)) {
    const hex = match[0].slice(1);
    const r = Number.parseInt(hex.slice(0, 2), 16);
    const g = Number.parseInt(hex.slice(2, 4), 16);
    const b = Number.parseInt(hex.slice(4, 6), 16);
    const [h, s] = hsl(r, g, b);
    if (h >= 120 && h <= 190 && s >= 0.18) hits.add(match[0].toLowerCase());
  }

  for (const match of css.matchAll(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[.\d]+)?\s*\)/g)) {
    const [h, s] = hsl(Number(match[1]), Number(match[2]), Number(match[3]));
    if (h >= 120 && h <= 190 && s >= 0.18) hits.add(match[0].replace(/\s+/g, ""));
  }

  return [...hits];
};

const balanced = (css) =>
  (css.match(/\{/g) || []).length === (css.match(/\}/g) || []).length;

assert(index.includes('<meta name="color-scheme" content="light">'), "index must force light color-scheme");
assert((index.match(/class="nav-icon"/g) || []).length === 9, "sidebar must contain exactly 9 SVG nav icons");
assert(!/<button[^>]*class="nav[^"]*"[^>]*>\s*<span>/m.test(index), "legacy text nav icons must not return");
assert((index.match(/\sstyle\s*=/g) || []).length === 0, "inline style attributes are not allowed in index.html");

assert(themeCss.includes("OCI-N&T UI FOUNDATION 2.0"), "consolidated UI foundation marker missing");
assert(!themeCss.includes("BEGIN R-BOT PREVIEW"), "historical R2/R3/R4/R5/R6 patch chain must not return");
assert(balanced(baseCss), "styles.css braces are unbalanced");
assert(balanced(themeCss), "ui-rbot.css braces are unbalanced");

const baseGreens = collectGreenTokens(baseCss);
const themeGreens = collectGreenTokens(themeCss);
const appGreens = collectGreenTokens(appJs);
const rcGreens = collectGreenTokens(rcJs);
const ui2Greens = collectGreenTokens(ui2Js);
assert(baseGreens.length === 0, "green/teal literals remain in styles.css: " + baseGreens.join(", "));
assert(themeGreens.length === 0, "green/teal literals remain in ui-rbot.css: " + themeGreens.join(", "));
assert(appGreens.length === 0, "green/teal literals remain in app.js: " + appGreens.join(", "));
assert(rcGreens.length === 0, "green/teal literals remain in rc.js: " + rcGreens.join(", "));
assert(ui2Greens.length === 0, "green/teal literals remain in ui2.js: " + ui2Greens.join(", "));

const fixedSettings = /#settings-page\s+\.settings-(?:login|session)-card[\s\S]{0,260}(?:height|min-height|max-height)\s*:\s*310px/;
assert(!fixedSettings.test(baseCss + "\n" + themeCss), "fixed 310px settings-card height must not return");

assert((themeCss.match(/BEGIN R-BOT PREVIEW/g) || []).length === 0, "legacy preview sections detected");
assert((themeCss.match(/!important/g) || []).length < 1300, "theme override grew beyond the guardrail");
assert(themeCss.length < 70000, "theme override grew beyond 70KB; consolidate before adding more patches");

if (!process.exitCode) {
  console.log("UI_LINT_OK");
  console.log("nav_icons=9");
  console.log("green_tokens=0");
  console.log("theme_bytes=" + themeCss.length);
  console.log("theme_important=" + (themeCss.match(/!important/g) || []).length);
}
