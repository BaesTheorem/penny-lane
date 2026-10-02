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

  const api = async (path, body) => {
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
  const snack = (msg) => { const s = $("#snack"); s.textContent = msg; s.classList.add("show"); setTimeout(() => s.classList.remove("show"), 2500); };
  const isPenny = (p) => p != null && p <= 0.01;
  // Store names from DG already start with "DG"; do not print the retailer twice.
  const storeName = (retailer, name) => String(name || "").replace(new RegExp(`^${SHORT[retailer] || "@@"}\\s+`), "");

  // A price set like a shelf tag: superscript dollar sign, condensed numerals.
  const bigPrice = (v) => (v == null ? `<div class="now">&mdash;</div>` : `<div class="now"><sup>$</sup>${Number(v).toFixed(2)}</div>`);
  const meter = (score) => {
    const on = Math.round((Math.max(0, Math.min(100, score || 0)) / 100) * 5);
    return `<div class="bars">${[0, 1, 2, 3, 4].map((i) => `<span class="${i < on ? "on" : ""}"></span>`).join("")}</div>`;
  };
  // "When did it go to a penny?": our own first sighting at this store (bounded by the
  // last higher price we saw), plus the earliest community report anywhere.
  const pennyWhen = (r) => {
    const parts = [];
    const p = r.penny || {};
    if (p.since) parts.push(p.after ? `Pennied here between ${day(p.after)} and ${day(p.since)}` : `$0.01 here since at least ${day(p.since)}`);
    if (r.first_reported) parts.push(`first reported ${day(r.first_reported)}`);
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
  async function loadHome() {
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
    $(".alerts")?.addEventListener("toggle", (e) => { if (e.target.open) api("/api/alerts/seen", {}).catch(() => {}); });
    const q = new URLSearchParams();
    if ($("#fRetailer").value) q.set("retailer", $("#fRetailer").value);
    if ($("#fStore").value) q.set("store", $("#fStore").value);
    if ($("#fStage").value) q.set("stage", $("#fStage").value);
    if ($("#fType").value) q.set("ctype", $("#fType").value);
    q.set("min_score", "25");
    q.set("sort", $("#fSort").value || "score");
    const rows = await api(`/api/predictions?${q}`);
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
      <div class="price-block">
        ${pen ? `<div class="stamp">Rings up</div>` : ""}
        ${bigPrice(eff)}
        ${r.msrp && r.msrp !== eff ? `<div class="was-label">Was</div><div class="was">${money(r.msrp)}</div>` : ""}
      </div>
      <div class="body">
        <div class="score"><b>${r.score}</b>${meter(r.score)}<div class="stage-label">${esc(STAGE[r.stage] || r.stage)}</div></div>
        <div class="name">${esc(r.name || r.item_id)}</div>
        <div class="where"><b>${esc(SHORT[r.retailer] || r.retailer)}</b> ${esc(storeName(r.retailer, r.store_name) || r.store_id)} · qty ${r.qty ?? "?"} · ${esc(ago(r.observed_at))}</div>
        ${pennyWhen(r)}
        ${why.length ? `<div class="why">${esc(why.slice(0, 3).join(" · "))}</div>` : ""}
        ${codes(r)}
      </div></article>`;
  }
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-item]");
    if (el) openItem(...el.dataset.item.split("/"));
  });

  // ---- item ----
  async function openItem(retailer, itemId) {
    showTab("item");
    $("#itemView").innerHTML = `<p class="muted mono">Loading…</p>`;
    const d = await api(`/api/item/${retailer}/${itemId}`);
    const it = d.item;
    const latest = d.latest.map((o) => {
      const eff = o.clearance_price ?? o.price;
      const pen = isPenny(eff);
      return `<article class="tag ${pen ? "penny" : ""}" style="cursor:default">
        <div class="price-block">${pen ? `<div class="stamp">Rings up</div>` : ""}${bigPrice(eff)}${o.original ? `<div class="was-label">Was</div><div class="was">${money(o.original)}</div>` : ""}</div>
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
        <div class="actions"><button class="btn solid" id="recheck">Re-check now</button><button class="btn" id="watchBtn">${d.watched ? "Stop watching" : "Watch"}</button></div>
      </div>
      <h3>At your stores</h3>${latest || '<div class="empty"><i>storefront</i>Not checked at any of your stores yet.</div>'}
      <h3>Community reports</h3><div class="reports">${reps || '<div class="empty">No community report for this one.</div>'}</div>
      <h3>Price history</h3><table class="hist"><tr><th>When</th><th>Store</th><th>Price</th><th>Clr</th><th>Qty</th><th>Status</th></tr>${hist}</table>`;
    $("#recheck").addEventListener("click", async () => {
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
        <div class="price-block">${pen ? `<div class="stamp">Rings up</div>` : ""}${bigPrice(eff)}${o.original ? `<div class="was-label">Was</div><div class="was">${money(o.original)}</div>` : ""}</div>
        <div class="body">
          <div class="score"><b>${o.score ?? "&ndash;"}</b>${meter(o.score)}<div class="stage-label">${esc(STAGE[o.stage] || "")}</div></div>
          <div class="name">${esc(o.name || o.item_id)}</div>
          <div class="where"><b>${esc(SHORT[o.retailer] || o.retailer)}</b> ${esc(storeName(o.retailer, o.store_name) || o.store_id)} · qty ${o.qty ?? "?"}</div>
          ${(o.reasons || []).length ? `<div class="why">${esc(o.reasons.slice(0, 3).join(" · "))}</div>` : ""}
        </div></article>`;
    }).join("");
  }
  $("#scanGo").addEventListener("click", () => doLookup());
  $("#scanInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doLookup(); });
  window.pennyScan = (code) => { showTab("scan"); doLookup(code); };

  // ---- lists: report cards with a receipt of local stock ----
  async function loadLists() {
    const q = new URLSearchParams();
    if ($("#lRetailer").value) q.set("retailer", $("#lRetailer").value);
    if ($("#lType").value) q.set("ctype", $("#lType").value);
    q.set("sort", $("#lSort").value || "recent");
    const rows = await api(`/api/reports?${q}`);
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
          ${r.retail ? `<div class="was-label">Was</div><div class="was">${money(r.retail)}</div>` : ""}
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
  $("#runSources").addEventListener("click", () => runJob("sources"));
  $("#runVerify").addEventListener("click", () => runJob("verify"));
  async function runJob(job, extra = "") {
    const d = await api(`/api/run/${job}${extra}`, {});
    snack(d.ok ? `${job} started` : d.why);
  }

  // ---- settings ----
  async function loadSettings() {
    const [stores, cfg, remote] = await Promise.all([api("/api/stores"), api("/api/config"), api("/remote/status").catch(() => null)]);
    const byR = {};
    for (const s of stores) (byR[s.retailer] ||= []).push(s);
    const lanes = state.status?.lanes || {};
    const sw = state.status?.sweeps || {};
    const src = state.status?.sources || {};
    const runs = (state.status?.runs || []).slice(0, 6).map((r) => `<div class="row-line"><span class="grow">${esc(r.kind)}</span>
      <span class="sub">${esc(ago(r.started))} · ${r.ok == null ? "running" : (r.ok ? "ok" : "failed")}</span></div>`).join("");
    $("#settings").innerHTML = `
      <h3>Run now</h3>
      <div class="actions">
        <button class="btn" data-job="sources">Pull lists</button>
        <button class="btn" data-job="verify">Verify at my stores</button>
        <button class="btn" data-job="sweep">Sweep Home Depot</button>
        <button class="btn" data-job="predict">Re-score</button>
        <button class="btn solid" data-job="all">Everything</button></div>
      <div class="panel">${runs || '<div class="row-line muted">No runs yet.</div>'}</div>
      <h3>Sources</h3>
      <div class="panel">${Object.entries(src).map(([k, v]) => `<div class="row-line"><span class="grow">${esc(k)}</span><span class="sub">${v.seen} seen · ${v.new} new · ${esc(ago(v.at))}</span></div>`).join("") || '<div class="row-line muted">Not pulled yet.</div>'}</div>
      <h3>Stores · zip ${esc(cfg.zip)} · ${esc(cfg.radius_miles)} mi</h3>
      ${Object.entries(byR).map(([r, list]) => `<div class="panel"><div class="lane-name">${esc(LABELS[r] || r)}${lanes[r]?.can_sweep ? "<small>SWEEPABLE</small>" : ""}</div>
        ${list.map((s) => `<label class="row-line"><input class="check" type="checkbox" data-store="${esc(r)}/${esc(s.store_id)}" ${s.watched ? "checked" : ""}>
          <span class="grow">${esc(s.name)}</span><span class="sub">#${esc(s.store_id)}${s.distance != null ? ` · ${Number(s.distance).toFixed(1)} mi` : ""}${sw[`${r}:${s.store_id}`] ? ` · swept ${esc(ago(sw[`${r}:${s.store_id}`].at))}` : ""}</span></label>`).join("")}</div>`).join("")}
      <div class="add-store"><select id="addRetailer">${Object.keys(LABELS).map((k) => `<option value="${k}">${esc(LABELS[k])}</option>`).join("")}</select>
        <input id="addStoreId" placeholder="Store #"><input id="addStoreName" placeholder="Name" style="flex:1;min-width:120px">
        <button class="btn" id="addStore">Add store</button></div>
      <h3>Phone</h3>
      ${remote ? `<div class="panel"><div class="row-line"><span class="grow">Tunnel</span><span class="sub">${esc(remote.tunnel?.url || remote.tunnel?.error || "starting…")}</span></div>
        <div class="row-line"><span class="grow">Discovery</span><span class="sub">${remote.discovery ? "published" : "not published"}</span></div>
        <div class="qr"><img id="pairQr" alt="pairing QR" width="180" height="180"></div>
        <p class="lede" style="font-size:13px">Scan with the Penny Lane iPhone app. The code carries the access token, so keep it private.</p></div>` : '<p class="muted">Phone access is not available in this session.</p>'}`;
    $$("[data-job]").forEach((b) => b.addEventListener("click", () => runJob(b.dataset.job)));
    $$("[data-store]").forEach((cb) => cb.addEventListener("change", async () => {
      const [retailer, store_id] = cb.dataset.store.split("/");
      await api("/api/stores/watch", { retailer, store_id, watched: cb.checked });
      loadStatus();
    }));
    $("#addStore").addEventListener("click", async () => {
      await api("/api/stores/add", { retailer: $("#addRetailer").value, store_id: $("#addStoreId").value.trim(), name: $("#addStoreName").value.trim() });
      await loadStatus(); loadSettings();
    });
    if (remote) {
      const payload = btoa(JSON.stringify({ v: 1, token: remote.pairing?.token, discovery: remote.pairing?.discovery, url: remote.pairing?.url }));
      $("#pairQr").src = `/api/qr?d=${encodeURIComponent(payload)}&light=1`;
    }
  }

  loadStatus().then(() => showTab("home")).catch((e) => { $("#ticker").textContent = e.message; });
  setInterval(() => { if (!document.hidden) loadStatus().catch(() => {}); }, 60000);
})();
