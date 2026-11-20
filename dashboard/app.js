// ADT Fleet dashboard. Reads fleet.json (the checklist, edited by maintainers),
// the website's tasks.json (ADT Work tasks), and public GitHub data. No keys needed.
(() => {
"use strict";
const ORG = "ADT-community";
const TASKS_URL = "https://adt-community.github.io/tasks.json";
const API = "https://api.github.com";
const CACHE_MS = 10 * 60 * 1000;
const DAY = 86400000;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function cached(key) {
  try { const c = JSON.parse(sessionStorage.getItem(key) || "null"); return c && Date.now() - c.t < CACHE_MS ? c.v : null; } catch (e) { return null; }
}
function store(key, v) { try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), v })); } catch (e) {} }
async function getJSON(url, key) {
  const hit = key && cached(key); if (hit) return hit;
  const r = await fetch(url, { headers: { Accept: "application/vnd.github+json" } });
  if (!r.ok) { const e = new Error(String(r.status)); e.status = r.status; throw e; }
  const v = await r.json(); if (key) store(key, v); return v;
}

async function repoInfo(name) {
  const out = { name, ok: false };
  try {
    const repo = await getJSON(`${API}/repos/${ORG}/${name}`, `repo:${name}`);
    out.ok = true; out.public = !repo.private; out.pages = !!repo.has_pages; out.pushed = repo.pushed_at; out.url = repo.html_url;
    const [pulls, runs] = await Promise.all([
      getJSON(`${API}/repos/${ORG}/${name}/pulls?state=open&per_page=100`, `pulls:${name}`).catch(() => []),
      getJSON(`${API}/repos/${ORG}/${name}/actions/runs?branch=main&per_page=10`, `runs:${name}`).catch(() => ({ workflow_runs: [] }))
    ]);
    out.pulls = pulls.map(p => ({ n: p.number, title: p.title, by: p.user.login, url: p.html_url, at: p.created_at }));
    const latest = {};
    for (const r of runs.workflow_runs || []) if (!latest[r.name]) latest[r.name] = r;
    out.runs = Object.values(latest).map(r => ({ name: r.name, status: r.status, conclusion: r.conclusion, url: r.html_url }));
  } catch (e) {
    out.private = e.status === 404; // private repos are invisible without a token
  }
  return out;
}

function ago(iso) {
  if (!iso) return "unknown";
  const d = Math.floor((Date.now() - new Date(iso)) / DAY);
  return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
}
function daysTo(date) { return Math.ceil((new Date(date + "T00:00:00Z") - Date.now()) / DAY); }

function autoChecks(app, info, tasks) {
  const appTasks = tasks.filter(t => t.repo === app.repo);
  const checks = [];
  if (info.ok) {
    checks.push({ text: "Repo is public", done: info.public });
    if (!app.internal || app.repo === "ADT_agent") checks.push({ text: "Published on GitHub Pages", done: info.pages });
    const failing = (info.runs || []).filter(r => r.conclusion === "failure");
    if ((info.runs || []).length) checks.push({ text: failing.length ? `Automatic checks failing: ${failing.map(r => r.name).join(", ")}` : "Automatic checks passing", done: !failing.length });
  } else if (info.private) {
    checks.push({ text: "Repo is public", done: false, note: "Private: contributors can't see it" });
  }
  if (appTasks.length) {
    const open = appTasks.filter(t => t.status === "open").length, done = appTasks.filter(t => t.status === "done").length;
    checks.push({ text: `Tasks on ADT Work: ${done} done, ${open} open, ${appTasks.length - open - done} taken`, done: appTasks.length > 0 && open === 0 && done === appTasks.length });
  }
  return checks;
}

function render(fleet, tasks, infos) {
  const kill = fleet.kill_rule, left = daysTo(kill.deadline), warnLeft = daysTo(kill.warning);
  const products = fleet.apps.filter(a => !a.internal);
  const revenue = fleet.apps.reduce((n, a) => n + (a.revenue_monthly_usd || 0), 0);
  const earning = products.filter(a => a.revenue_monthly_usd > 0).length;
  const allPRs = Object.values(infos).flatMap(i => (i.pulls || []).map(p => ({ ...p, repo: i.name })));
  const paidOpen = tasks.filter(t => t.paid && t.status === "open");
  const social = products.filter(a => a.social_good).length;

  $("sub").textContent = `${products.length} products · ${earning} earning · kill-rule deadline in ${left} days (${new Date(kill.deadline).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })})`;
  $("stats").innerHTML = [
    [`$${revenue}`, "revenue this month"],
    [`${earning} / ${products.length}`, "products earning"],
    [`${paidOpen.length} ($${paidOpen.reduce((n, t) => n + (t.reward || 0), 0)})`, "paid tasks open"],
    [allPRs.length, "pull requests waiting"]
  ].map(([b, s]) => `<div class="stat"><b>${esc(b)}</b><span>${esc(s)}</span></div>`).join("");

  // what needs the owner
  const needs = [];
  for (const a of fleet.apps) {
    const i = infos[a.repo] || {};
    if (i.private) needs.push(`<b>${esc(a.name)}</b>: repo is private, so contributors can't see it.`);
    else if (i.ok && !i.pages && a.live) needs.push(`<b>${esc(a.name)}</b>: turn on GitHub Pages (Settings → Pages → Source: GitHub Actions) so it goes live.`);
    const failing = (i.runs || []).filter(r => r.conclusion === "failure");
    if (failing.length) needs.push(`<b>${esc(a.name)}</b>: automatic checks failing (<a href="${esc(failing[0].url)}">see run</a>).`);
  }
  const byAuthor = {};
  allPRs.forEach(p => (byAuthor[p.by] = (byAuthor[p.by] || 0) + 1));
  if (allPRs.length) needs.push(`<b>${allPRs.length} pull requests</b> waiting for review: ${Object.entries(byAuthor).sort((a, b) => b[1] - a[1]).map(([u, n]) => `${esc(u)} (${n})`).join(", ")}.`);
  if (warnLeft <= 0 && left > 0) products.filter(a => !a.revenue_monthly_usd).forEach(a => needs.push(`<b>${esc(a.name)}</b>: past the 3-month warning with no revenue.`));
  if (social > Math.max(1, Math.floor(products.length / 10))) needs.push(`Too many social-good projects (${social}) for the 1-in-10 rule.`);
  $("attention").innerHTML = `<h3>Needs you now</h3>` + (needs.length ? `<ul class="check">${needs.map(n => `<li><span class="box">!</span><span>${n}</span></li>`).join("")}</ul>` : `<p class="note">Nothing urgent.</p>`);

  // apps
  $("apps").innerHTML = fleet.apps.map(a => {
    const i = infos[a.repo] || {}, auto = autoChecks(a, i, tasks), items = [...a.checklist, ...auto.map(x => ({ ...x, auto: true }))];
    const done = items.filter(x => x.done).length, pct = Math.round((done / items.length) * 100);
    const revenueItem = a.checklist.find(x => x.revenue);
    const stageCls = /live/i.test(a.stage) ? " live" : /practice/i.test(a.stage) ? " practice" : "";
    const prs = i.pulls || [];
    return `<details class="app"><summary>
        <div class="head"><h2>${esc(a.name)}${a.social_good ? " 🤝" : ""}</h2><span class="pill${stageCls}">${esc(a.stage)}</span></div>
        <p class="what">${esc(a.what)}</p>
        <div class="bar" aria-label="${pct}% of checklist done"><i style="width:${pct}%"></i></div>
        <div class="meta"><span>${done}/${items.length} done</span>
          <span class="money">$${a.revenue_monthly_usd || 0}/mo</span>
          ${a.internal ? "" : `<span class="${revenueItem && revenueItem.done ? "ok" : left < 90 ? "bad" : "warn"}">${revenueItem && revenueItem.done ? "Earning ✓" : `${left} days to first revenue`}</span>`}
          ${i.ok ? `<span>${prs.length} PR${prs.length === 1 ? "" : "s"}</span><span>Updated ${ago(i.pushed)}</span>` : i.private ? `<span>Private repo</span>` : ""}</div>
      </summary>
      <div class="body">
        <h3>Checklist</h3><ul class="check">${items.map(x => `<li class="${x.done ? "done" : ""}${x.auto ? " auto" : ""}">
          <span class="box">${x.done ? "✓" : ""}</span><span class="t">${esc(x.text)}${x.revenue ? " 💰" : ""}${x.note ? `<small>${esc(x.note)}</small>` : ""}</span></li>`).join("")}</ul>
        ${prs.length ? `<h3>Pull requests</h3>${prs.slice(0, 8).map(p => `<div class="pr"><a href="${esc(p.url)}">#${p.n} ${esc(p.title)}</a><br><span class="note">${esc(p.by)} · ${ago(p.at)}</span></div>`).join("")}${prs.length > 8 ? `<p class="note">and ${prs.length - 8} more</p>` : ""}` : ""}
        <div class="links">${a.live ? `<a class="btn solid" href="${esc(a.live)}">Open app</a>` : ""}<a class="btn" href="https://github.com/${ORG}/${esc(a.repo)}">Code</a>
          ${a.internal ? "" : `<a class="btn" href="https://adt-community.github.io/work.html">Tasks</a>`}</div>
      </div></details>`;
  }).join("");

  const net = fleet.network || [];
  $("network").innerHTML = `<h3>Network checklist</h3><ul class="check">${net.map(x => `<li class="${x.done ? "done" : ""}"><span class="box">${x.done ? "✓" : ""}</span><span class="t">${esc(x.text)}</span></li>`).join("")}</ul>
    <p class="note">Kill rule: ${esc(kill.rule)}</p>`;
}

async function main() {
  try {
    const fleet = await getJSON("fleet.json?" + Date.now());
    const tasks = await getJSON(TASKS_URL, "tasks").then(d => d.tasks).catch(() => []);
    const repos = [...new Set(fleet.apps.map(a => a.repo))];
    const infos = Object.fromEntries((await Promise.all(repos.map(repoInfo))).map(i => [i.name, i]));
    render(fleet, tasks, infos);
  } catch (e) {
    $("attention").innerHTML = `<h3>Needs you now</h3><p class="note">Couldn't load the dashboard. Check your connection and refresh.</p>`;
  }
}
main();
setInterval(() => { if (!document.hidden) main(); }, CACHE_MS);
window.ADT_FLEET = { autoChecks, daysTo };
})();
