/* Penny Lane web UI. Plain JS over the JSON API; the iPhone shell renders this
   same page and injects scanned barcodes through window.pennyScan(code).
   Every result renders as a shelf tag (see app.css). */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const state = { status: null, stores: [], tab: "home" };
  const LABELS = { homedepot: "Home Depot", lowes: "Lowe's", dollargeneral: "Dollar General", walmart: "Walmart" };
  const SHORT = { homedepot: "HD", lowes: "LOW", dollargeneral: "DG", walmart: "WMT" };
  const TYPES = {};
  const STAGE = { penny: "On the shelf", imminent: "Imminent", late: "Late markdown", early: "Early markdown", watch: "Watching", gone: "Not on shelf" };

  // The GitHub Pages copy sets PENNY_STATIC and loads static-api.js, which answers from published data.
  const STATIC = !!window.PENNY_STATIC;
  const api = async (path, body) => {
    if (STATIC) return window.pennyStatic.api(path, body);
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (v) => (v == null ? "" : `$${Number(v).toFixed(2)}`);
  const ago = (ts) => {
    if (!ts) return "";
    const d = (Date.now() / 1000 - ts) / 60;
    if (d < 60) return `${Math.max(1, Math.round(d))}m ago`;
    if (d < 1440) return `${Math.round(d / 60)}h ago`;
    return `${Math.round(d / 1440)}d ago`;
  };
  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");
  // Whole calendar days between a timestamp and today: "today", "1 day", "9 days".
  const daysOld = (ts) => {
    if (!ts) return "";
    const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const n = Math.round((start(new Date()) - start(new Date(ts * 1000))) / 86400000);
    return n <= 0 ? "today" : `${n} day${n === 1 ? "" : "s"}`;
  };
  const dayAge = (ts) => { const a = daysOld(ts); return a === "today" ? `${day(ts)} (today)` : `${day(ts)} (${a} ago)`; };
  const snack = (msg) => { const s = $("#snack"); s.textContent = msg; s.classList.add("show"); setTimeout(() => s.classList.remove("show"), 2500); };
  const isPenny = (p) => p != null && p <= 0.01;
  // Store names from DG already start with "DG"; do not print the retailer twice.
  const storeName = (retailer, name) => String(name || "").replace(new RegExp(`^${SHORT[retailer] || "@@"}\\s+`), "");

  // A price set like a shelf tag: superscript dollar sign, condensed numerals.
  const bigPrice = (v) => (v == null ? `<div class="now">&mdash;</div>` : `<div class="now"><sup>$</sup>${Number(v).toFixed(2)}</div>`);
  // Left column of every tag: the register price, the MSRP, and the dollars saved.
  const priceBlock = (eff, msrp) => {
    const pen = isPenny(eff);
    const save = msrp && eff != null && msrp - eff >= 0.01 ? msrp - eff : null;
    return `<div class="price-block">
      ${pen ? `<div class="stamp">Rings up</div>` : ""}
      ${bigPrice(eff)}
      ${msrp ? `<div class="msrp"><span>MSRP</span>${money(msrp)}</div>` : ""}
      ${save ? `<div class="save"><span>Save</span>${money(save)}<small>${Math.round((save / msrp) * 100)}% off</small></div>` : ""}
    </div>`;
  };
  const meter = (score) => {
    const on = Math.round((Math.max(0, Math.min(100, score || 0)) / 100) * 5);
    return `<div class="bars">${[0, 1, 2, 3, 4].map((i) => `<span class="${i < on ? "on" : ""}"></span>`).join("")}</div>`;
  };
  // "When did it go to a penny?": our own first sighting at this store (bounded by the
  // last higher price we saw), plus the earliest community report anywhere.
  const pennyWhen = (r) => {
    const parts = [];
    const p = r.penny || {};
    if (p.since) parts.push(p.after ? `Pennied here between ${day(p.after)} and ${dayAge(p.since)}` : `$0.01 here since at least ${dayAge(p.since)}`);
    if (r.first_reported) parts.push(`first reported ${dayAge(r.first_reported)}`);
    return parts.length ? `<div class="when"><i>event</i><span>${esc(parts.join(" · "))}</span></div>` : "";
  };
  const codes = (r) => {
    const out = [];
    if (r.ctype && r.ctype !== "other") out.push(`<span class="kind">${esc(TYPES[r.ctype] || r.ctype)}</span>`);
    if (r.sku) out.push(`<span>SKU ${esc(r.sku)}</span>`);
    if (r.upc) out.push(`<span>UPC ${esc(r.upc)}</span>`);
    return out.length ? `<div class="codes">${out.join("")}</div>` : "";
  };

  // ---- tabs ----
  function showTab(name) {
    state.tab = name;
    $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${name}`));
    $$(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
    window.scrollTo(0, 0);
    if (name === "home") loadHome();
    if (name === "lists") loadLists();
    if (name === "settings") loadSettings();
    if (name === "scan") setTimeout(() => $("#scanInput").focus(), 50);
  }
  $$("[data-tab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.tab); }));
  $("#scanBtn").addEventListener("click", () => (window.webkit?.messageHandlers?.scan ? window.webkit.messageHandlers.scan.postMessage("open") : showTab("scan")));
  $("#refreshBtn").addEventListener("click", () => { loadStatus().then(() => showTab(state.tab)); });

  // ---- status / stores / ticker ----
  async function loadStatus() {
    state.status = await api("/api/status");
    state.stores = state.status.stores;
    const unseen = state.status.counts.alerts_unseen;
    $("#unseen").textContent = unseen;
    $("#unseen").classList.toggle("hidden", !unseen);
    const retailers = [...new Set(state.stores.map((s) => s.retailer))];
    for (const sel of ["#fRetailer", "#lRetailer"]) fillSelect(sel, retailers.map((r) => [r, LABELS[r] || r]), "All");
    fillSelect("#scanRetailer", retailers.map((r) => [r, LABELS[r] || r]), "Any");
    const types = await api("/api/types").catch(() => []);
    for (const t of types) TYPES[t.key] = t.label;
    for (const sel of ["#fType", "#lType"]) fillSelect(sel, types.filter((t) => t.items || t.key === "food").map((t) => [t.key, t.label]), "Any");
    fillSelect("#fStore", state.stores.map((s) => [s.store_id, `${SHORT[s.retailer] || s.retailer} ${storeName(s.retailer, s.name)}`]), "All");
    fillSelect("#scanStore", state.stores.map((s) => [s.store_id, `${SHORT[s.retailer] || s.retailer} ${storeName(s.retailer, s.name)}`]), "All watched");
    const c = state.status.counts;
    const running = Object.entries(state.status.jobs || {}).filter(([, j]) => j.running).map(([k]) => k);
    const pennies = await api("/api/predictions?stage=penny&limit=500").then((r) => r.length).catch(() => 0);
    const lastRun = (state.status.runs || []).find((r) => r.finished);
    const bits = [
      `<span><b>${pennies}</b> AT $0.01 NEAR YOU</span>`,
      `<span>${state.stores.length} STORES WATCHED</span>`,
      `<span>${c.observations.toLocaleString()} PRICE CHECKS</span>`,
      `<span>${c.reports.toLocaleString()} COMMUNITY REPORTS</span>`,
      lastRun ? `<span>LAST ${esc(lastRun.kind.toUpperCase())} ${esc(ago(lastRun.finished).toUpperCase())}</span>` : "",
      running.length ? `<span><b>RUNNING ${esc(running.join(", ").toUpperCase())}</b></span>` : "",
    ].filter(Boolean);
    $("#ticker").innerHTML = `<div class="ticker-in">${bits.join('<span class="dot">/</span>')}</div>`;
  }
  function fillSelect(sel, pairs, first) {
    const el = $(sel); const cur = el.value;
    el.innerHTML = `<option value="">${esc(first)}</option>` + pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    el.value = cur;
  }

  // ---- home ----
  // Each list load takes a ticket; an answer that arrives after a newer request is dropped,
  // so a slow old sort cannot overwrite the one Alex just picked.
  const tickets = {};
  const take = (k) => (tickets[k] = (tickets[k] || 0) + 1);
  const busy = (sel, on) => $(sel).classList.toggle("loading", on);
  async function loadHome() {
    const t = take("home");
    busy("#ranked", true);
    const alerts = (await api("/api/alerts?limit=40")).filter((a) => !a.seen);
    const pennies = alerts.filter((a) => a.kind === "penny_on_shelf").length;
    // Price out of the alert message ("..., clearance price $17.99; ...") for the left column.
    const priceOf = (m) => ((m || "").match(/clearance price (\$[\d.,]+)/) || (m || "").match(/register price is (\$[\d.,]+)/) || [])[1] || "";
    $("#alerts").innerHTML = alerts.length ? `<details class="alerts">
      <summary><i>notifications_active</i>${alerts.length} new alert${alerts.length === 1 ? "" : "s"}${pennies ? ` · ${pennies} at $0.01` : ""}<i class="chev">expand_more</i></summary>
      ${alerts.map((a) => `<div class="a-row" data-item="${esc(a.retailer)}/${esc(a.item_id)}">
        <div><div class="a-price">${esc(a.kind === "penny_on_shelf" ? "$0.01" : priceOf(a.message) || "–")}</div><div class="a-kind">${a.kind === "penny_on_shelf" ? "Penny" : "Close"}</div></div>
        <div><div class="a-name">${esc(a.name || a.item_id)}</div><div class="a-where">${esc(SHORT[a.retailer] || a.retailer)} ${esc(storeName(a.retailer, a.store_name) || a.store_id)} · ${esc(ago(a.ts))}</div></div></div>`).join("")}
      </details>` : "";
    if (STATIC && window.pennyStatic.prefs().lat == null) {
      $("#alerts").insertAdjacentHTML("afterbegin", `<div class="alert" data-tab-go="settings"><i>location_on</i><div class="t"><b>Set your location</b><br>
        These are downtown Kansas City stores. Tap to pick yours and set alerts.</div></div>`);
      $("[data-tab-go]").addEventListener("click", () => showTab("settings"));
    }
    $(".alerts")?.addEventListener("toggle", (e) => { if (e.target.open) api("/api/alerts/seen", {}).catch(() => {}); });
    const q = new URLSearchParams();
    if ($("#fRetailer").value) q.set("retailer", $("#fRetailer").value);
    if ($("#fStore").value) q.set("store", $("#fStore").value);
    if ($("#fStage").value) q.set("stage", $("#fStage").value);
    if ($("#fType").value) q.set("ctype", $("#fType").value);
    q.set("min_score", "25");
    q.set("sort", $("#fSort").value || "score");
    q.set("group", "item");
    const rows = await api(`/api/predictions?${q}`).finally(() => t === tickets.home && busy("#ranked", false));
    if (t !== tickets.home) return;
    $("#ranked").innerHTML = rows.length ? rows.map(card).join("")
      : `<div class="empty"><i>sell</i>Nothing near a penny matches these filters.<br>Run <b>Verify</b> or a <b>Sweep</b> from Setup to look again.</div>`;
  }
  ["#fRetailer", "#fStore", "#fStage", "#fSort", "#fType"].forEach((s) => $(s).addEventListener("change", loadHome));

  function card(r) {
    const eff = r.clearance_price ?? r.price;
    const pen = isPenny(eff);
    const reasons = (() => { try { return JSON.parse(r.reasons || "[]"); } catch { return []; } })();
    const why = pen ? reasons.filter((x) => !/register price/.test(x)) : reasons;
    return `<article class="tag ${pen ? "penny" : ""} ${esc(r.stage)}" data-item="${esc(r.retailer)}/${esc(r.item_id)}">
      ${priceBlock(eff, r.msrp)}
      <div class="body">
        <div class="score"><b>${r.score}</b>${meter(r.score)}<div class="stage-label">${esc(STAGE[r.stage] || r.stage)}</div></div>
        <div class="name">${esc(r.name || r.item_id)}</div>
        <div class="where"><b>${esc(SHORT[r.retailer] || r.retailer)}</b> ${esc(storeName(r.retailer, r.store_name) || r.store_id)} · qty ${r.qty ?? "?"} · ${esc(ago(r.observed_at))}</div>
        ${pennyWhen(r)}
        ${why.length ? `<div class="why">${esc(why.slice(0, 3).join(" · "))}</div>` : ""}
        ${codes(r)}
        ${alsoAt(r)}
      </div></article>`;
  }
  // The item's other stores, cheapest first, as receipt lines under the best one.
  const ALSO_MAX = 4;
  function alsoAt(r) {
    const others = r.others || [];
    if (!others.length) return "";
    const lines = others.slice(0, ALSO_MAX).map((o) => {
      const eff = o.clearance_price ?? o.price;
      const cls = isPenny(eff) ? "pen" : (o.qty > 0 ? "hit" : "");
      return `<div class="line ${cls}"><span class="st">${esc((storeName(r.retailer, o.store_name) || o.store_id).toUpperCase())}</span><span class="fill"></span><span>${eff == null ? "NO PRICE" : money(eff)} · QTY ${o.qty ?? "?"}</span></div>`;
    }).join("");
    const more = others.length - ALSO_MAX;
    return `<div class="receipt"><div class="also">Also at ${others.length} more store${others.length === 1 ? "" : "s"}</div>${lines}
      ${more > 0 ? `<div class="line none"><span class="st">AND ${more} MORE · TAP FOR ALL</span></div>` : ""}</div>`;
  }
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-item]");
    if (el) openItem(...el.dataset.item.split("/"));
  });

  // ---- item ----
  async function openItem(retailer, itemId) {
    showTab("item");
    $("#itemView").innerHTML = `<p class="muted mono">Loading…</p>`;
    let d;
    try { d = await api(`/api/item/${retailer}/${itemId}`); }
    catch (e) { $("#itemView").innerHTML = `<div class="empty"><i>help</i>${esc(e.message)}</div>`; return; }
    const it = d.item;
    const latest = d.latest.map((o) => {
      const eff = o.clearance_price ?? o.price;
      const pen = isPenny(eff);
      return `<article class="tag ${pen ? "penny" : ""}" style="cursor:default">
        ${priceBlock(eff, o.msrp ?? o.original)}
        <div class="body">
          <div class="score"><b>${o.score ?? "&ndash;"}</b>${meter(o.score)}<div class="stage-label">${esc(STAGE[o.stage] || "")}</div></div>
          <div class="name">${esc(o.store_name || o.store_id)}</div>
          <div class="where">qty ${o.qty ?? "?"} · ${esc(o.store_status || "")} ${esc(o.promo || "")} · checked ${esc(ago(o.ts))}</div>
          ${pennyWhen({ penny: o.penny, first_reported: d.first_reported })}
          ${(o.reasons || []).length ? `<div class="why">${esc(o.reasons.slice(0, 4).join(" · "))}</div>` : ""}
        </div></article>`;
    }).join("");
    const hist = d.history.slice(0, 80).map((h) => `<tr class="${isPenny(h.price) ? "pen" : ""}"><td>${new Date(h.ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</td>
      <td>${esc(h.store_id)}</td><td>${money(h.price)}</td><td>${money(h.clearance_price)}</td><td>${h.qty ?? ""}</td><td>${esc(h.store_status || "")}</td></tr>`).join("");
    const reps = d.reports.map((r) => `<div class="rep">${esc(r.source)} <span>· ${esc(day(r.first_reported_at || r.reported_at))}${r.store_hint ? ` · ${esc(r.store_hint)}` : ""}</span>
      ${r.url ? ` <a href="${esc(r.url)}" target="_blank">source</a>` : ""}</div>`).join("");
    $("#itemView").innerHTML = `
      <div class="item-head">
        <div class="sec-head"><span class="sec-no">${esc(SHORT[retailer] || retailer)}</span><h2>${esc(LABELS[retailer] || retailer)}</h2></div>
        <h1>${esc(it.name || it.item_id)}</h1>
        <dl class="spec">
          <dt>Item</dt><dd>${esc(it.item_id)}</dd>
          ${it.sku ? `<dt>SKU</dt><dd>${esc(it.sku)}</dd>` : ""}
          ${it.upc ? `<dt>UPC</dt><dd>${esc(it.upc)}</dd>` : ""}
          ${it.model ? `<dt>Model</dt><dd>${esc(it.model)}</dd>` : ""}
          ${it.ctype ? `<dt>Type</dt><dd>${esc(TYPES[it.ctype] || it.ctype)}</dd>` : ""}
          ${it.url ? `<dt>Link</dt><dd><a href="${esc(it.url)}" target="_blank">${esc(it.url.replace(/^https?:\/\/(www\.)?/, ""))}</a></dd>` : ""}
        </dl>
        <div class="actions">${STATIC ? "" : '<button class="btn solid" id="recheck">Re-check now</button>'}<button class="btn" id="watchBtn">${d.watched ? "Stop watching" : "Watch"}</button></div>
      </div>
      <h3>At your stores</h3>${latest || '<div class="empty"><i>storefront</i>Not checked at any of your stores yet.</div>'}
      <h3>Community reports</h3><div class="reports">${reps || '<div class="empty">No community report for this one.</div>'}</div>
      <h3>Price history</h3><table class="hist"><tr><th>When</th><th>Store</th><th>Price</th><th>Clr</th><th>Qty</th><th>Status</th></tr>${hist}</table>`;
    $("#recheck")?.addEventListener("click", async () => {
      snack("Checking the shelves…");
      await api("/api/lookup", { retailer, item_id: itemId });
      openItem(retailer, itemId);
    });
    $("#watchBtn").addEventListener("click", async () => {
      await api("/api/watch", { retailer, item_id: itemId, remove: d.watched });
      openItem(retailer, itemId);
    });
  }

  // ---- scan ----
  async function doLookup(code) {
    code = (code || $("#scanInput").value).replace(/\D/g, "");
    if (!code) return;
    $("#scanInput").value = code;
    $("#scanOut").innerHTML = `<p class="muted mono">Looking up ${esc(code)}…</p>`;
    const body = { retailer: $("#scanRetailer").value || undefined, store_id: $("#scanStore").value || undefined };
    if (code.length >= 11 && code.length <= 13) body.upc = code; else body.sku = code;
    let d;
    try { d = await api("/api/lookup", body); } catch (e) { $("#scanOut").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (!d.ok) { $("#scanOut").innerHTML = `<div class="empty"><i>help</i>${esc(d.why)}</div>`; return; }
    $("#scanOut").innerHTML = d.results.map((o) => {
      if (o.error) return `<div class="empty" style="text-align:left"><b>${esc(LABELS[o.retailer] || o.retailer)} ${esc(o.store_id)}</b> · ${esc(o.error)}</div>`;
      const eff = o.clearance_price ?? o.price;
      const pen = isPenny(eff);
      return `<article class="tag ${pen ? "penny" : ""}" data-item="${esc(o.retailer)}/${esc(o.item_id)}">
        ${priceBlock(eff, o.msrp ?? o.original)}
        <div class="body">
          <div class="score"><b>${o.score ?? "&ndash;"}</b>${meter(o.score)}<div class="stage-label">${esc(STAGE[o.stage] || "")}</div></div>
          <div class="name">${esc(o.name || o.item_id)}</div>
          <div class="where"><b>${esc(SHORT[o.retailer] || o.retailer)}</b> ${esc(storeName(o.retailer, o.store_name) || o.store_id)} · qty ${o.qty ?? "?"}</div>
          ${pennyWhen(o)}
          ${(o.reasons || []).length ? `<div class="why">${esc(o.reasons.slice(0, 3).join(" · "))}</div>` : ""}
        </div></article>`;
    }).join("");
  }
  $("#scanGo").addEventListener("click", () => doLookup());
  $("#scanInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doLookup(); });
  window.pennyScan = (code) => { showTab("scan"); doLookup(code); };

  // ---- lists: report cards with a receipt of local stock ----
  async function loadLists() {
    const t = take("lists");
    busy("#lists", true);
    const q = new URLSearchParams();
    if ($("#lRetailer").value) q.set("retailer", $("#lRetailer").value);
    if ($("#lType").value) q.set("ctype", $("#lType").value);
    q.set("sort", $("#lSort").value || "recent");
    const rows = await api(`/api/reports?${q}`).finally(() => t === tickets.lists && busy("#lists", false));
    if (t !== tickets.lists) return;
    $("#lists").innerHTML = rows.slice(0, 400).map((r) => {
      const carried = (r.local || []).filter((o) => o.price != null);
      const notCarried = (r.local || []).length - carried.length;
      const local = carried.map((o) => {
        const cls = isPenny(o.price) ? "pen" : (o.qty > 0 ? "hit" : "");
        return `<div class="line ${cls}"><span class="st">${esc((o.store_name || "").toUpperCase())}</span><span class="fill"></span><span>${money(o.price)} · QTY ${o.qty ?? "?"}</span></div>`;
      }).join("") + (notCarried ? `<div class="line none"><span class="st">NOT CARRIED AT ${notCarried} STORE${notCarried === 1 ? "" : "S"}</span></div>` : "");
      const link = r.item_id ? `data-item="${esc(r.retailer)}/${esc(r.item_id)}"` : (r.retailer === "dollargeneral" && r.upc ? `data-item="dollargeneral/${esc(r.upc.replace(/^0+/, ""))}"` : "");
      return `<article class="tag" ${link}>
        <div class="price-block">
          <div class="stamp" style="font-family:var(--mono);font-size:9px;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:2px">Pennied</div>
          <div class="now" style="font-size:24px">${esc(day(r.first_reported_at || r.reported_at) || "&mdash;")}</div>
          ${(r.first_reported_at || r.reported_at) ? `<div class="age">${esc(daysOld(r.first_reported_at || r.reported_at) === "today" ? "today" : `${daysOld(r.first_reported_at || r.reported_at)} ago`)}</div>` : ""}
          ${r.retail ? `<div class="msrp"><span>MSRP</span>${money(r.retail)}</div><div class="save"><span>Save</span>${money(r.retail - 0.01)}</div>` : ""}
        </div>
        <div class="body">
          <div class="name" style="margin-right:0">${esc(r.name || r.sku || r.upc)}</div>
          <div class="where"><b>${esc(SHORT[r.retailer] || r.retailer)}</b> ${esc(r.source)} · last seen ${esc(ago(r.reported_at || r.fetched_at))}</div>
          ${r.store_hint ? `<div class="why">${esc(r.store_hint)}</div>` : ""}
          ${codes(r)}
          <div class="receipt">${local || '<span class="muted">Not checked at your stores yet</span>'}</div>
        </div></article>`;
    }).join("") || `<div class="empty"><i>receipt_long</i>No reports pulled yet.</div>`;
  }
  ["#lRetailer", "#lSort", "#lType"].forEach((s) => $(s).addEventListener("change", loadLists));
  if (STATIC) $("#runSources").parentElement.remove();
  else {
    $("#runSources").addEventListener("click", () => runJob("sources"));
    $("#runVerify").addEventListener("click", () => runJob("verify"));
  }
  async function runJob(job, extra = "") {
    const d = await api(`/api/run/${job}${extra}`, {});
    snack(d.ok ? `${job} started` : d.why);
  }

  // ---- settings ----
  async function loadSettings() {
    const [stores, cfg, remote] = await Promise.all([api("/api/stores"), api("/api/config"),
      STATIC ? null : api("/remote/status").catch(() => null)]);
    const byR = {};
    for (const s of stores) (byR[s.retailer] ||= []).push(s);
    const lanes = state.status?.lanes || {};
    const sw = state.status?.sweeps || {};
    const src = state.status?.sources || {};
    const runs = (state.status?.runs || []).slice(0, 6).map((r) => `<div class="row-line"><span class="grow">${esc(r.kind)}</span>
      <span class="sub">${esc(ago(r.started))} · ${r.ok == null ? "running" : (r.ok ? "ok" : "failed")}</span></div>`).join("");
    const localTop = STATIC ? "" : `
      <h3>Run now</h3>
      <div class="actions">
        <button class="btn" data-job="sources">Pull lists</button>
        <button class="btn" data-job="verify">Verify at my stores</button>
        <button class="btn" data-job="sweep">Sweep Home Depot</button>
        <button class="btn" data-job="predict">Re-score</button>
        <button class="btn solid" data-job="all">Everything</button></div>
      <div class="panel">${runs || '<div class="row-line muted">No runs yet.</div>'}</div>
      <h3>Sources</h3>
      <div class="panel">${Object.entries(src).map(([k, v]) => `<div class="row-line"><span class="grow">${esc(k)}</span><span class="sub">${v.seen} seen · ${v.new} new · ${esc(ago(v.at))}</span></div>`).join("") || '<div class="row-line muted">Not pulled yet.</div>'}</div>`;
    $("#settings").innerHTML = `${localTop}
      <h3>Location</h3>
      <div class="panel loc">
        <div class="row-line"><i>location_on</i><span class="grow"><b>${esc(cfg.location?.label || (cfg.zip ? "Zip " + cfg.zip : "Not set: showing downtown Kansas City"))}</b></span>
          <span class="sub">${cfg.zip ? `zip ${esc(cfg.zip)} · ` : ""}${esc(cfg.radius_miles)} mi</span></div>
        <div class="loc-form">
          <button class="btn solid" id="locDetect"><i>my_location</i> Detect location</button>
          <input id="locZip" inputmode="numeric" maxlength="5" placeholder="Zip code" value="">
          <select id="locRadius">${[5, 10, 15, 25, 50].map((m) => `<option value="${m}" ${Number(cfg.radius_miles) === m ? "selected" : ""}>${m} mi</option>`).join("")}</select>
          <button class="btn" id="locSet">Use zip</button>
        </div>
        <p class="lede loc-note">${STATIC
          ? "Your location stays in this browser. A move picks the 3 nearest stores per retailer. This site checks Kansas City area stores only."
          : "A move watches the nearest stores per retailer, as many as you watch now (at least 3). Walmart stores stay as you set them."}</p>
      </div>
      <h3>Stores</h3>
      ${Object.entries(byR).map(([r, list]) => `<div class="panel"><div class="lane-name">${esc(LABELS[r] || r)}${lanes[r]?.can_sweep ? "<small>SWEEPABLE</small>" : ""}</div>
        ${list.map((s) => `<label class="row-line"><input class="check" type="checkbox" data-store="${esc(r)}/${esc(s.store_id)}" ${s.watched ? "checked" : ""}>
          <span class="grow">${esc(s.name)}</span><span class="sub">#${esc(s.store_id)}${s.distance != null ? ` · ${Number(s.distance).toFixed(1)} mi` : ""}${sw[`${r}:${s.store_id}`] ? ` · swept ${esc(ago(sw[`${r}:${s.store_id}`].at))}` : ""}</span></label>`).join("")}</div>`).join("")}
      ${STATIC ? staticPanels() : `<div class="add-store"><select id="addRetailer">${Object.keys(LABELS).map((k) => `<option value="${k}">${esc(LABELS[k])}</option>`).join("")}</select>
        <input id="addStoreId" placeholder="Store #"><input id="addStoreName" placeholder="Name" style="flex:1;min-width:120px">
        <button class="btn" id="addStore">Add store</button></div>
      <h3>Phone</h3>
      ${remote ? `<div class="panel"><div class="row-line"><span class="grow">Tunnel</span><span class="sub">${esc(remote.tunnel?.url || remote.tunnel?.error || "starting…")}</span></div>
        <div class="row-line"><span class="grow">Discovery</span><span class="sub">${remote.discovery ? "published" : "not published"}</span></div>
        <div class="qr"><img id="pairQr" alt="pairing QR" width="180" height="180"></div>
        <p class="lede" style="font-size:13px">Scan with the Penny Lane iPhone app. The code carries the access token, so keep it private.</p></div>` : '<p class="muted">Phone access is not available in this session.</p>'}`}`;
    $$("[data-job]").forEach((b) => b.addEventListener("click", () => runJob(b.dataset.job)));
    $$("[data-store]").forEach((cb) => cb.addEventListener("change", async () => {
      const [retailer, store_id] = cb.dataset.store.split("/");
      await api("/api/stores/watch", { retailer, store_id, watched: cb.checked });
      loadStatus();
    }));
    if (STATIC) bindStaticPanels();
    $("#addStore")?.addEventListener("click", async () => {
      await api("/api/stores/add", { retailer: $("#addRetailer").value, store_id: $("#addStoreId").value.trim(), name: $("#addStoreName").value.trim() });
      await loadStatus(); loadSettings();
    });
    $("#locSet").addEventListener("click", () => {
      const zip = $("#locZip").value.replace(/\D/g, "");
      if (zip.length !== 5) { snack("Enter a 5-digit zip"); return; }
      relocate({ zip });
    });
    $("#locZip").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#locSet").click(); });
    $("#locDetect").addEventListener("click", () => {
      snack("Finding you…");
      // The device position first; if the browser has none or says no, the Mac's network location.
      const fallback = () => relocate({ network: true });
      if (!navigator.geolocation) { fallback(); return; }
      navigator.geolocation.getCurrentPosition(
        (p) => relocate({ lat: p.coords.latitude, lon: p.coords.longitude }),
        fallback, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
    });
    if (remote) {
      const payload = btoa(JSON.stringify({ v: 1, token: remote.pairing?.token, discovery: remote.pairing?.discovery, url: remote.pairing?.url }));
      $("#pairQr").src = `/api/qr?d=${encodeURIComponent(payload)}&light=1`;
    }
  }

  // ---- static site: alert rules and phone alerts, all kept in this browser ----
  function staticPanels() {
    const ps = window.pennyStatic;
    const p = ps.prefs();
    const a = p.alerts;
    const meta = ps.meta() || {};
    const perm = "Notification" in window ? Notification.permission : "unsupported";
    const topics = ps.topics();
    const server = meta.ntfy?.server || "https://ntfy.sh";
    return `
      <h3>Alerts</h3>
      <div class="panel">
        <label class="row-line"><input class="check" type="checkbox" id="alOn" ${a.enabled ? "checked" : ""}><span class="grow">Show alerts for my stores</span></label>
        <label class="row-line"><span class="grow">Only when MSRP is at least</span>
          <select id="alMsrp" class="mini">${[0, 10, 25, 50, 100, 250].map((m) => `<option value="${m}" ${a.minMsrp === m ? "selected" : ""}>${m ? "$" + m : "any"}</option>`).join("")}</select></label>
        <label class="row-line"><input class="check" type="checkbox" id="alImm" ${a.imminent ? "checked" : ""}><span class="grow">Also when an item is close to a penny (score 70+)</span></label>
        ${Object.keys(LABELS).filter((k) => k !== "walmart").map((k) => `<label class="row-line"><input class="check" type="checkbox" data-alr="${k}" ${!a.retailers.length || a.retailers.includes(k) ? "checked" : ""}><span class="grow">${esc(LABELS[k])}</span></label>`).join("")}
        <div class="row-line"><span class="grow">Browser notifications while this page is open</span>
          ${perm === "granted" ? `<label><input class="check" type="checkbox" id="alNotify" ${a.notify ? "checked" : ""}></label>`
            : perm === "denied" ? '<span class="sub">blocked in browser settings</span>'
            : perm === "unsupported" ? '<span class="sub">not supported here</span>'
            : '<button class="btn" id="alAsk">Allow</button>'}</div>
      </div>
      <h3>Phone alerts</h3>
      <div class="panel">
        <p class="lede" style="font-size:13px;margin:10px 0">Get a push the moment a penny is on one of your shelves, with no account. Install the free <a href="https://ntfy.sh" target="_blank" rel="noopener">ntfy</a> app, then subscribe to your stores:</p>
        ${topics.map((t) => `<div class="row-line"><span class="grow">${esc(SHORT[t.retailer] || t.retailer)} ${esc(storeName(t.retailer, t.label))}</span>
          <a class="sub" href="${esc(server)}/${esc(t.topic)}" target="_blank" rel="noopener">${esc(t.topic)}</a></div>`).join("") || '<div class="row-line muted">Pick stores above first.</div>'}
      </div>
      <h3>About this copy</h3>
      <div class="panel"><p class="lede" style="font-size:13px;margin:10px 0">A private server checks Kansas City area shelves and publishes the result here.
        Last update ${esc(ago(meta.generated_at))}. Your location, stores and alert rules stay in this browser.
        Barcode lookups search the published data only.</p></div>`;
  }
  function bindStaticPanels() {
    const ps = window.pennyStatic;
    const upd = (fn) => { const p = ps.prefs(); fn(p.alerts); ps.setPrefs(p); loadStatus(); };
    $("#alOn").addEventListener("change", (e) => upd((a) => { a.enabled = e.target.checked; }));
    $("#alMsrp").addEventListener("change", (e) => upd((a) => { a.minMsrp = Number(e.target.value); }));
    $("#alImm").addEventListener("change", (e) => upd((a) => { a.imminent = e.target.checked; }));
    $$("[data-alr]").forEach((cb) => cb.addEventListener("change", () => upd((a) => {
      a.retailers = $$("[data-alr]").filter((x) => x.checked).map((x) => x.dataset.alr);
    })));
    $("#alNotify")?.addEventListener("change", (e) => upd((a) => { a.notify = e.target.checked; }));
    $("#alAsk")?.addEventListener("click", async () => {
      const r = await Notification.requestPermission();
      if (r === "granted") upd((a) => { a.notify = true; });
      loadSettings();
    });
  }

  async function relocate(where) {
    snack("Finding stores near you…");
    $$(".loc-form button").forEach((b) => (b.disabled = true));
    let d;
    try { d = await api("/api/location", { ...where, radius_miles: Number($("#locRadius")?.value) || undefined }); }
    catch (e) { snack(e.message); return loadSettings(); }
    if (!d.ok) { snack(d.why); return loadSettings(); }
    const n = Object.values(d.lanes).reduce((a, l) => a + (l.watched?.length || 0), 0);
    const failed = Object.entries(d.lanes).filter(([, l]) => l.error).map(([k]) => LABELS[k] || k);
    snack(d.warn || (`${d.place.label || d.place.zip}${d.place.source === "network" ? " (from the Mac's network)" : ""} · watching ${n} stores`
      + (failed.length ? ` · ${failed.join(", ")} locator failed, stores kept` : "")));
    await loadStatus();
    loadSettings();
  }

  // A link like #item/homedepot/204512007 (ntfy click) opens that item.
  const deepLink = () => {
    const m = location.hash.match(/^#item\/([^/]+)\/(.+)$/);
    if (m) { openItem(m[1], decodeURIComponent(m[2])); return true; }
    return false;
  };
  window.addEventListener("hashchange", deepLink);
  loadStatus().then(() => { if (!deepLink()) showTab("home"); if (STATIC) window.pennyStatic.notifyNew(); }).catch((e) => { $("#ticker").textContent = e.message; });
  setInterval(() => { if (!document.hidden) loadStatus().catch(() => {}); }, 60000);
})();
