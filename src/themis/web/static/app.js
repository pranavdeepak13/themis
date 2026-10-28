"use strict";
// All text is set with textContent (names come from files on disk); no innerHTML anywhere.

const $ = (id) => document.getElementById(id);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
};

const store = {
  get(k, d) { try { return localStorage.getItem("themis." + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("themis." + k, v); } catch { /* storage may be blocked */ } },
};
const state = {
  profile: store.get("profile", ""), since: store.get("since", "30d"), q: "", filter: "all", sort: "uses",
  meta: null, selected: null, data: null,
};

const fmt = {
  n(v) { if (v == null) return "–"; if (v >= 1e9) return (v / 1e9).toFixed(2) + "B"; if (v >= 1e6) return (v / 1e6).toFixed(1) + "M"; if (v >= 1e3) return (v / 1e3).toFixed(1) + "k"; return String(v); },
  usd(v) { return v == null ? "–" : "$" + v.toLocaleString(undefined, { minimumFractionDigits: v < 100 ? 2 : 0, maximumFractionDigits: v < 100 ? 2 : 0 }); },
  pct(v) { return v == null ? "–" : Math.round(v * 100) + "%"; },
  ago(iso) {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    if (s < 86400 * 60) return Math.round(s / 86400) + "d ago";
    return new Date(iso).toLocaleDateString(undefined, { month: "short", year: "numeric" });
  },
  dur(s) { if (!s) return "–"; const m = Math.round(s / 60); return m < 60 ? m + "m" : Math.floor(m / 60) + "h " + (m % 60) + "m"; },
};

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || res.statusText);
  return data;
}
const qs = (extra = {}) => new URLSearchParams(Object.fromEntries(Object.entries({ profile: state.profile, since: state.since, ...extra }).filter(([, v]) => v))).toString();

function toast(msg, err = false) {
  const t = h("div", { class: "toast" + (err ? " err" : "") }, msg);
  $("toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

// ---------------------------------------------------------------- routes
const ROUTES = [
  ["overview", "Overview", "Usage"], ["sessions", "Sessions", "Usage"],
  ["skills", "Skills", "Resources"], ["agents", "Agents", "Resources"], ["commands", "Commands", "Resources"],
  ["plugins", "Plugins", "Resources"], ["mcp", "MCP servers", "Resources"], ["hooks", "Hooks", "Resources"],
  ["advice", "Advice", "Health"], ["doctor", "Doctor", "Health"],
];
const route = () => (location.hash.replace(/^#\/?/, "") || "overview");

function renderNav() {
  const nav = $("nav");
  nav.replaceChildren(h("div", { class: "brand" }, h("i"), "Themis"));
  let group = "";
  for (const [id, label, g] of ROUTES) {
    if (g !== group) { group = g; nav.append(h("div", { class: "nav-label" }, g)); }
    nav.append(h("a", { href: "#/" + id, class: route() === id ? "on" : "" }, label));
  }
  const m = state.meta;
  nav.append(h("div", { class: "nav-foot" }, m ? `${m.profiles.length} profiles · ${m.sessions} sessions` : "", h("br"), "127.0.0.1 only"));
}

function renderTop(title, extra = []) {
  const m = state.meta;
  const prof = h("select", { class: "input", "aria-label": "Profile", onchange: (e) => { state.profile = e.target.value; store.set("profile", state.profile); closeDrawer(); load(); } },
    h("option", { value: "" }, "All profiles"), ...(m?.profiles || []).map((p) => h("option", { value: p.name, selected: p.name === state.profile }, p.alias || p.name)));
  const range = h("div", { class: "seg", role: "group", "aria-label": "Date range" },
    ...[["7d", "7d"], ["30d", "30d"], ["90d", "90d"], ["all", "All"]].map(([v, l]) =>
      h("button", { class: state.since === v ? "on" : "", onclick: () => { state.since = v; store.set("since", v); load(); } }, l)));
  $("top").replaceChildren(h("h1", {}, title), ...extra, prof, range,
    h("button", { class: "btn", title: "Rescan profiles", onclick: refresh }, "Refresh"));
}

async function refresh() {
  try { state.meta = await api("/api/refresh", { rebuild: false }); toast("Refreshed"); load(); } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- drawer / modal
function closeDrawer() { state.selected = null; $("drawer").hidden = true; document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel")); }
function openDrawer(...kids) { const d = $("drawer"); d.replaceChildren(...kids); d.hidden = false; d.scrollTop = 0; }
const dhead = (title, sub) => h("div", { class: "dhead" }, h("div", {}, h("h3", {}, title), sub ? h("div", { class: "muted" }, sub) : null),
  h("button", { class: "btn sm ghost", "aria-label": "Close details", onclick: closeDrawer }, "Close"));
const kv = (rows) => h("dl", { class: "kv" }, rows.filter((r) => r[1] != null && r[1] !== "").flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));

function modal(title, sub, body, actions) {
  const m = $("modal");
  const close = () => { m.hidden = true; m.replaceChildren(); };
  m.replaceChildren(h("div", { class: "dialog", role: "dialog", "aria-modal": "true" }, h("h3", {}, title), h("div", { class: "sub" }, sub), body,
    h("div", { class: "acts" }, ...actions(close))));
  m.hidden = false;
  m.onclick = (e) => { if (e.target === m) close(); };
  m.querySelector(".btn.primary")?.focus();
  return close;
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("modal").hidden) { $("modal").hidden = true; $("modal").replaceChildren(); } else closeDrawer();
});

function diffView(text) {
  const pre = h("pre", { class: "code diff" });
  for (const line of (text || "").split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) continue;
    pre.append(h("span", { class: line.startsWith("+") ? "a" : line.startsWith("-") ? "d" : "" }, line || " "));
  }
  return pre;
}

// ---------------------------------------------------------------- toggles
function switchFor(kind, name, member, onDone) {
  const sw = h("button", { class: "sw", role: "switch", "aria-checked": String(!!member.enabled), "aria-label": `${member.enabled ? "Disable" : "Enable"} ${name} in ${member.profile}`,
    title: `${member.enabled ? "Disable" : "Enable"} in ${member.profile}`,
    onclick: (e) => { e.stopPropagation(); requestToggle(kind, name, member, sw, onDone); } });
  return sw;
}

async function requestToggle(kind, name, member, sw, onDone) {
  const want = !member.enabled;
  const body = { kind, name, profile: member.profile, enabled: want };
  sw.classList.add("busy");
  let plan;
  try { plan = await api("/api/toggle", body); } catch (e) { sw.classList.remove("busy"); toast(e.message, true); return; }
  sw.classList.remove("busy");
  if (!plan.changed) { toast("Already " + (want ? "enabled" : "disabled")); return; }
  modal(`${want ? "Enable" : "Disable"} ${name}`, `${member.profile} · ${plan.path}`,
    h("div", {}, diffView(plan.diff), h("p", { class: "note" }, "A timestamped backup of the file is made before writing, and the change is added to the audit log.")),
    (close) => [
      h("button", { class: "btn", onclick: close }, "Cancel"),
      h("button", { class: "btn primary", onclick: async (ev) => {
        ev.target.disabled = true;
        try { const res = await api("/api/toggle", { ...body, apply: true }); close(); toast(res.message.startsWith("Written") ? `${want ? "Enabled" : "Disabled"} ${name} in ${member.profile}` : res.message); await onDone(); }
        catch (e) { ev.target.disabled = false; toast(e.message, true); }
      } }, want ? "Enable" : "Disable"),
    ]);
}

// ---------------------------------------------------------------- pages
const bars = (rows, max, label = "name") => rows.length
  ? h("div", { class: "list" }, rows.map((r) => h("div", { class: "li" }, h("span", { class: "name", title: r[label] }, r[label]),
      h("div", { class: "meter" }, h("i", { style: `width:${Math.max(3, (r.uses / max) * 100)}%` })), h("span", { class: "num" }, fmt.n(r.uses)))))
  : h("div", { class: "empty" }, "Nothing used in this range");
const card = (title, body, right) => h("section", { class: "card" }, h("h2", {}, title, right || null), body);
const stat = (v, l) => h("div", { class: "card stat" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l));

async function pageOverview(page) {
  const o = await api("/api/overview?" + qs());
  state.data = o;
  const max = (rows) => Math.max(1, ...rows.map((r) => r.uses));
  const peak = Math.max(1, ...o.daily.map((d) => d.tokens));
  const kids = [];
  if (o.sessions && o.unused_skills) kids.push(h("div", { class: "callout" }, h("div", {}, h("b", {}, `${o.unused_skills} of ${o.installed_skills} skills`), ` weren't used in this range. They still cost startup tokens every session.`),
    h("a", { class: "btn", href: "#/skills", onclick: () => { state.filter = "unused"; } }, "Review unused skills")));
  kids.push(h("div", { class: "grid stats" }, stat(fmt.n(o.sessions), "Sessions"), stat(fmt.n(o.tokens.total), "Tokens"),
    stat(fmt.usd(o.cost) + (o.cost_incomplete ? "+" : ""), "Est. API-equivalent cost"), stat(fmt.pct(o.cache_hit), "Cache hit rate")));
  kids.push(card("Daily token usage", o.daily.length
    ? h("div", {}, h("div", { class: "chart" }, o.daily.map((d) => h("div", { style: `height:${(d.tokens / peak) * 100}%`, title: `${d.label} · ${fmt.n(d.tokens)} tokens · ${d.sessions} sessions` }))),
      h("div", { class: "axis" }, h("span", {}, o.daily[0].label), h("span", {}, "peak " + fmt.n(peak)), h("span", {}, o.daily.at(-1).label)))
    : h("div", { class: "empty" }, "No sessions in this range")));
  kids.push(h("div", { class: "grid two" },
    card("Most used skills", bars(o.top_skills, max(o.top_skills)), h("a", { class: "btn sm ghost", href: "#/skills" }, "All")),
    card("Most used tools", bars(o.top_tools, max(o.top_tools))),
    card("Slash commands", bars(o.top_commands, max(o.top_commands)), h("a", { class: "btn sm ghost", href: "#/commands" }, "All")),
    card("MCP servers", bars(o.top_mcp, max(o.top_mcp)), h("a", { class: "btn sm ghost", href: "#/mcp" }, "All")),
    card("Subagents", bars(o.top_agents, max(o.top_agents)), h("a", { class: "btn sm ghost", href: "#/agents" }, "All")),
    card("Models", bars(o.by_model.map((m) => ({ name: m.label, uses: m.tokens })), Math.max(1, ...o.by_model.map((m) => m.tokens))))));
  kids.push(h("div", { class: "grid two" },
    card("By profile", h("div", { class: "list" }, o.by_profile.map((p) => h("div", { class: "li", style: "grid-template-columns:1fr auto auto;" }, h("span", {}, p.label), h("span", { class: "num" }, fmt.n(p.tokens) + " tok"), h("span", { class: "num", style: "min-width:64px" }, fmt.usd(p.cost)))))),
    card("Health", kv([["Context", `${o.context.near_limit} sessions near limit · ${fmt.pct(o.context.compaction_rate)} compacted`], ["Doctor", `${o.findings} issues`], ["Advice", `${o.advice} suggestions`]]),
      h("a", { class: "btn sm ghost", href: "#/advice" }, "Open"))));
  page.replaceChildren(...kids);
}

function headCols(cols, sortKey, onSort) {
  return h("div", { class: "thead", style: `--cols:${cols.map((c) => c.w).join(" ")}` }, cols.map((c) =>
    h("span", { class: (c.r ? "r " : "") + (c.k ? "sortable" + (sortKey === c.k ? " on" : "") : ""), onclick: c.k ? () => onSort(c.k) : null }, c.t + (c.k && sortKey === c.k ? " ↓" : ""))));
}

async function pageSessions(page) {
  const sortKey = state.sort === "uses" ? "recent" : state.sort;
  const d = await api("/api/sessions?" + qs({ q: state.q, sort: sortKey }));
  const cols = [{ t: "Session", w: "minmax(0,2.4fr)" }, { t: "Profile", w: "90px" }, { t: "Last active", w: "90px", k: "recent" }, { t: "Tokens", w: "80px", k: "tokens", r: 1 }, { t: "Cost", w: "70px", r: 1 }, { t: "Context", w: "70px", k: "context", r: 1 }];
  const table = h("div", { class: "table" }, headCols(cols, sortKey, (k) => { state.sort = k; load(); }));
  for (const s of d.rows) {
    const row = h("div", { class: "row", tabindex: "0", style: `--cols:${cols.map((c) => c.w).join(" ")}`, onclick: () => selectSession(s, row), onkeydown: (e) => e.key === "Enter" && row.click() },
      h("div", { class: "cell-name" }, h("b", {}, s.topic), h("span", {}, `${s.project} · ${s.model}`)),
      h("span", { class: "dim" }, s.profile), h("span", { class: "muted" }, fmt.ago(s.ended)), h("span", { class: "num r" }, fmt.n(s.tokens)), h("span", { class: "num r" }, fmt.usd(s.cost)),
      h("span", { class: "num r" }, s.context + "%"));
    table.append(row);
  }
  if (!d.rows.length) table.append(h("div", { class: "empty" }, "No sessions match"));
  page.replaceChildren(toolbar(`${d.total} sessions`), table, h("p", { class: "note" }, "Metadata only. Transcript text is never shown here."));
}

function selectSession(s, row) {
  document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel")); row.classList.add("sel"); state.selected = s.id;
  openDrawer(dhead(s.topic, `${s.profile} · ${s.project}`),
    kv([["Session", h("span", { class: "mono" }, s.id)], ["Model", s.model], ["Branch", s.branch], ["Last active", fmt.ago(s.ended)], ["Duration", fmt.dur(s.duration_s)], ["Messages", s.messages],
      ["Tokens", fmt.n(s.tokens)], ["Est. cost", fmt.usd(s.cost)], ["Cache hit", fmt.pct(s.cache_hit)], ["Context peak", s.context + "%"], ["Compactions", s.compactions], ["Subagents", s.subagents], ["Tags", s.tags.join(", ")]]),
    h("div", { class: "sec" }, h("h4", {}, "Tools used"), s.top_tools.length ? h("div", { class: "list" }, s.top_tools.map(([n, c]) => h("div", { class: "li" }, h("span", { class: "name" }, n), h("span"), h("span", { class: "num" }, c)))) : h("div", { class: "muted" }, "None")),
    Object.keys(s.skills).length ? h("div", { class: "sec" }, h("h4", {}, "Skills"), Object.entries(s.skills).map(([n, c]) => h("span", { class: "tag" }, `${n} ×${c}`))) : null,
    h("div", { class: "sec" }, h("h4", {}, "Resume"), h("pre", { class: "code" }, `cd ${s.project}\nclaude --resume ${s.id}`)));
}

function toolbar(count, filters = false) {
  const search = h("input", { class: "input", type: "search", placeholder: "Search…", value: state.q, "aria-label": "Search",
    oninput: debounce((e) => { state.q = e.target.value; load(true); }, 220) });
  const kids = [search];
  if (filters) kids.push(h("div", { class: "seg", role: "group", "aria-label": "Usage filter" }, ...[["all", "All"], ["used", "Used"], ["unused", "Unused"]].map(([v, l]) =>
    h("button", { class: state.filter === v ? "on" : "", onclick: () => { state.filter = v; load(true); } }, l))));
  kids.push(h("span", { class: "muted sp" }, count));
  return h("div", { class: "toolbar" }, ...kids);
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

const LABEL = { skills: "skill", agents: "agent", commands: "command", plugins: "plugin", mcp: "MCP server", hooks: "hook" };
async function pageResources(page, kind) {
  const d = await api(`/api/resources/${kind}?` + qs({ q: state.q }));
  const tracked = kind !== "plugins" && kind !== "hooks"; // transcripts record no plugin or hook invocations
  let rows = d.rows;
  if (!tracked) state.filter = "all";
  if (state.filter === "used") rows = rows.filter((r) => r.uses > 0);
  if (state.filter === "unused") rows = rows.filter((r) => !r.uses);
  const reload = async () => { await load(true); if (state.selected) { const g = rows.find((r) => r.key === state.selected); } };
  const cols = [{ t: "Name", w: "minmax(0,2.2fr)" }, { t: "Used", w: "minmax(80px,1fr)" }, { t: "Sessions", w: "72px", r: 1 }, { t: "Last used", w: "84px" }, { t: "Startup tok", w: "86px", r: 1 }, { t: "Profiles", w: "minmax(0,1fr)" }, { t: "On", w: "48px", r: 1 }];
  const max = Math.max(1, ...rows.map((r) => r.uses));
  const table = h("div", { class: "table" }, headCols(cols));
  for (const g of rows) {
    const t = g.members.filter((m) => m.toggleable);
    let ctl = h("span", { class: "muted" }, "–");
    if (t.length === 1) ctl = switchFor(g.kind, g.name, t[0], reload);
    else if (t.length > 1) ctl = h("span", { class: "num muted" }, `${t.filter((m) => m.enabled).length}/${t.length}`);
    const row = h("div", { class: "row" + (state.selected === g.key ? " sel" : ""), tabindex: "0", style: `--cols:${cols.map((c) => c.w).join(" ")}`, onclick: () => selectResource(g, row, reload), onkeydown: (e) => e.key === "Enter" && row.click() },
      h("div", { class: "cell-name" }, h("b", {}, g.name), h("span", {}, g.description || g.info || g.origin)),
      g.uses ? h("div", { style: "display:flex;gap:10px;align-items:center" }, h("div", { class: "meter", style: "flex:1" }, h("i", { style: `width:${Math.max(4, (g.uses / max) * 100)}%` })), h("span", { class: "num" }, g.uses)) : h("span", { class: "muted" }, tracked ? "unused" : "not tracked"),
      h("span", { class: "num r" }, g.sessions || "–"), h("span", { class: "muted" }, !tracked ? "–" : g.uses ? fmt.ago(g.last_used) : "never"), h("span", { class: "num r" }, g.startup_tokens || "–"),
      h("span", { class: "muted", style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, g.profiles.join(", ")), h("span", { class: "r" }, ctl));
    table.append(row);
  }
  if (!rows.length) table.append(h("div", { class: "empty" }, "Nothing here"));
  const note = (kind === "plugins" || kind === "mcp")
    ? "Switches change enabledPlugins (settings.json) or disabledMcpServers (.claude.json). Plugin-provided MCP servers follow their plugin."
    : "Startup tokens are estimates (about 4 characters per token) for the name and description Claude loads each session.";
  page.replaceChildren(toolbar(`${rows.length} ${LABEL[kind]}${rows.length === 1 ? "" : "s"}${tracked ? ` · ${d.unused} unused` : ""}`, tracked), table, h("p", { class: "note" }, note));
}

function selectResource(g, row, reload) {
  document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel")); row.classList.add("sel"); state.selected = g.key;
  const members = g.members.map((m) => h("div", { class: "member" }, h("div", { style: "min-width:0" }, h("div", {}, m.profile, m.drifted ? h("span", { class: "tag", style: "margin-left:8px" }, "drifted") : null),
    h("div", { class: "muted mono", style: "word-break:break-all;font-size:11.5px" }, m.path || "")),
    m.toggleable ? switchFor(g.kind, g.name, m, async () => { await reload(); }) : h("span", { class: "muted" }, m.enabled == null ? "" : m.enabled ? "on" : "off")));
  const perProfile = Object.entries(g.per_profile);
  openDrawer(dhead(g.name, `${LABEL[g.kind === "mcp" ? "mcp" : g.kind + "s"] || g.kind} · ${g.origin}`),
    g.description ? h("p", { class: "dim", style: "margin:0" }, g.description) : null,
    h("div", { class: "sec" }, h("h4", {}, "Usage"), kv([["Invocations", g.uses], ["Sessions", g.sessions], ["Last used", g.uses ? fmt.ago(g.last_used) : "never"], ["Startup tokens", g.startup_tokens || null], ["Full tokens", g.full_tokens || null], ["Scope", g.scope]]),
      perProfile.length ? h("div", { style: "margin-top:8px" }, perProfile.map(([p, n]) => h("span", { class: "tag" }, `${p} ×${n}`))) : null),
    h("div", { class: "sec" }, h("h4", {}, g.kind === "plugin" || g.kind === "mcp" ? "Enabled per profile" : "Installed in"), members),
    g.tags.length ? h("div", { class: "sec" }, h("h4", {}, "Tags"), g.tags.map((t) => h("span", { class: "tag" }, t))) : null,
    Object.keys(g.frontmatter).length ? h("div", { class: "sec" }, h("h4", {}, "Frontmatter"), kv(Object.entries(g.frontmatter).slice(0, 8).map(([k, v]) => [k, String(v).slice(0, 200)]))) : null);
}

async function pageAdvice(page) {
  const d = await api("/api/advice?" + qs());
  page.replaceChildren(h("div", { class: "toolbar" }, h("span", { class: "muted" }, `${d.items.length} suggestions · advisory only, nothing is edited`)),
    h("div", { class: "table" }, ...(d.items.length ? d.items.map((a) => {
      const row = h("div", { class: "row", tabindex: "0", style: "--cols:minmax(0,1fr) 90px 120px", onclick: () => adviceDrawer(a, row), onkeydown: (e) => e.key === "Enter" && row.click() },
        h("div", { class: "cell-name" }, h("b", {}, a.title), h("span", {}, a.summary)), h("span", {}, h("i", { class: "dot " + (a.severity === "high" ? "f" : a.severity === "medium" ? "h" : "") }), a.severity),
        h("span", { class: "num r muted" }, a.tokens_per_month ? "~" + fmt.n(a.tokens_per_month) + "/mo" : a.rule));
      return row;
    }) : [h("div", { class: "empty" }, "Nothing to suggest")])));
}
function adviceDrawer(a, row) {
  document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel")); row.classList.add("sel");
  const copy = h("button", { class: "btn sm", onclick: async () => { try { await navigator.clipboard.writeText(a.prompt); toast("Prompt copied"); } catch { toast("Copy failed", true); } } }, "Copy prompt");
  openDrawer(dhead(a.title, `${a.severity} · ${a.confidence} confidence`), h("p", { class: "dim", style: "margin:0" }, a.summary),
    h("div", { class: "sec" }, h("h4", {}, "Evidence"), h("ul", { class: "dim", style: "margin:0;padding-left:18px" }, a.evidence.map((e) => h("li", {}, e)))),
    a.files.length ? h("div", { class: "sec" }, h("h4", {}, "Files"), h("pre", { class: "code" }, a.files.join("\n"))) : null,
    a.prompt ? h("div", { class: "sec" }, h("h4", {}, "Prompt for Claude Code ", copy), h("pre", { class: "code prompt" }, a.prompt)) : null);
}

async function pageDoctor(page) {
  const d = await api("/api/doctor?" + qs());
  const order = { error: 0, warning: 1, info: 2 };
  const rows = d.findings.sort((a, b) => order[a.severity] - order[b.severity]);
  page.replaceChildren(h("div", { class: "toolbar" }, h("span", { class: "muted" }, `${rows.length} findings · read-only checks`)),
    h("div", { class: "table" }, ...(rows.length ? rows.map((f) => h("div", { class: "row", style: "--cols:90px minmax(0,1fr) 110px;cursor:default" },
      h("span", {}, h("i", { class: "dot " + (f.severity === "error" ? "f" : f.severity === "warning" ? "h" : "") }), f.severity),
      h("div", { class: "cell-name" }, h("b", { style: "white-space:normal" }, f.message), f.path ? h("span", { class: "mono" }, f.path) : null), h("span", { class: "muted" }, f.profile || ""))) : [h("div", { class: "empty" }, "All clear")])));
}

// ---------------------------------------------------------------- boot
const TITLES = Object.fromEntries(ROUTES.map(([id, l]) => [id, l]));
async function load(keepScroll = false) {
  const id = route();
  const page = $("page");
  const y = page.scrollTop;
  renderNav(); renderTop(TITLES[id] || "Overview");
  try {
    if (id === "overview") await pageOverview(page);
    else if (id === "sessions") await pageSessions(page);
    else if (id === "advice") await pageAdvice(page);
    else if (id === "doctor") await pageDoctor(page);
    else if (LABEL[id]) await pageResources(page, id);
    else page.replaceChildren(h("div", { class: "empty" }, "Not found"));
  } catch (e) { page.replaceChildren(h("div", { class: "empty" }, "Could not load: " + e.message)); }
  if (keepScroll) page.scrollTop = y;
}
let lastRoute = null;
window.addEventListener("hashchange", () => { if (lastRoute !== route()) { state.q = ""; state.filter = state.filter === "unused" && route() === "skills" ? "unused" : "all"; closeDrawer(); } lastRoute = route(); load(); });
(async () => {
  lastRoute = route();
  try { state.meta = await api("/api/meta"); } catch (e) { toast(e.message, true); }
  load();
})();
