/* Penny Lane web UI. Plain JS over the JSON API; the iPhone shell renders this
   same page and injects scanned barcodes through window.pennyScan(code). */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const state = { status: null, stores: [], tab: "home" };
  // The MD3 scheme is baked into app.css (seed #b86a2b); Beer only needs the mode.
  if (window.ui) ui("mode", "light");
  const LABELS = { homedepot: "Home Depot", lowes: "Lowe's", dollargeneral: "Dollar General", walmart: "Walmart" };
  const TYPES = {};

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
    if (d < 60) return `${Math.round(d)}m ago`;
    if (d < 1440) return `${Math.round(d / 60)}h ago`;
    return `${Math.round(d / 1440)}d ago`;
  };
  const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");
  // "When did it go to a penny?": our own first sighting at this store (bounded by the
  // last higher price we saw), plus the earliest community report anywhere.
  const pennyWhen = (r) => {
    const parts = [];
    const p = r.penny || {};
    if (p.since) parts.push(p.after ? `pennied here between ${day(p.after)} and ${day(p.since)}` : `$0.01 here since at least ${day(p.since)}`);
    if (r.first_reported) parts.push(`first reported pennied ${day(r.first_reported)}`);
    return parts.length ? `<div class="when"><i>schedule</i>${esc(parts.join(" · "))}</div>` : "";
  };
  const snack = (msg) => { const s = $("#snack"); s.textContent = msg; s.classList.add("show"); setTimeout(() => s.classList.remove("show"), 2500); };

  // ---- tabs ----
  function showTab(name) {
    state.tab = name;
    $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${name}`));
    $$("nav.bottom a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
    if (name === "home") loadHome();
    if (name === "lists") loadLists();
    if (name === "settings") loadSettings();
    if (name === "scan") setTimeout(() => $("#scanInput").focus(), 50);
  }
  $$("[data-tab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.tab); }));
  $("#scanBtn").addEventListener("click", () => (window.webkit?.messageHandlers?.scan ? window.webkit.messageHandlers.scan.postMessage("open") : showTab("scan")));
  $("#refreshBtn").addEventListener("click", () => { loadStatus().then(() => showTab(state.tab)); });

  // ---- status / stores ----
  async function loadStatus() {
    state.status = await api("/api/status");
    state.stores = state.status.stores;
    const unseen = state.status.counts.alerts_unseen;
    $("#unseen").textContent = unseen;
    $("#unseen").classList.toggle("hidden", !unseen);
    const retailers = [...new Set(state.stores.map((s) => s.retailer))];
    for (const sel of ["#fRetailer", "#scanRetailer", "#lRetailer"]) fillSelect(sel, retailers.map((r) => [r, LABELS[r] || r]), "All retailers");
    const types = await api("/api/types").catch(() => []);
    for (const t of types) TYPES[t.key] = t.label;
    for (const sel of ["#fType", "#lType"]) fillSelect(sel, types.filter((t) => t.items || t.key === "food").map((t) => [t.key, `${t.label}${t.items ? ` (${t.items})` : ""}`]), "Any type");
    for (const sel of ["#fStore", "#scanStore"]) fillSelect(sel, state.stores.map((s) => [s.store_id, `${LABELS[s.retailer] || s.retailer}: ${s.name}`]), sel === "#fStore" ? "All stores" : "All watched stores");
    const c = state.status.counts;
    const running = Object.entries(state.status.jobs || {}).filter(([, j]) => j.running).map(([k]) => k);
    const pennies = await api("/api/predictions?stage=penny&limit=500").then((r) => r.length).catch(() => 0);
    const hour = new Date().getHours();
    const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    $("#hero").innerHTML = `<div class="big">${hello}. ${pennies ? `${pennies} penn${pennies === 1 ? "y is" : "ies are"} on a shelf near you.` : "Nothing is at a penny near you right now."}</div>
      <div class="sub">${state.stores.length} stores watched · lists and shelves re-checked hourly${running.length ? ` · running: ${esc(running.join(", "))}` : ""}</div>
      <div class="stats"><div class="stat"><b>${c.items}</b>items tracked</div><div class="stat"><b>${c.observations}</b>price checks</div><div class="stat"><b>${c.reports}</b>community reports</div></div>`;
  }
  function fillSelect(sel, pairs, first) {
    const el = $(sel); const cur = el.value;
    el.innerHTML = `<option value="">${esc(first)}</option>` + pairs.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    el.value = cur;
  }

  // ---- home ----
  async function loadHome() {
    const alerts = await api("/api/alerts?limit=8");
    $("#alerts").innerHTML = alerts.filter((a) => !a.seen).map((a) => `
      <div class="alert ${esc(a.kind === "penny_on_shelf" ? "penny" : "imminent")}" data-item="${esc(a.retailer)}/${esc(a.item_id)}">
        <i>${a.kind === "penny_on_shelf" ? "paid" : "trending_down"}</i><div><b>${esc(a.kind === "penny_on_shelf" ? "Penny on the shelf" : "Getting close")}</b> · ${esc(ago(a.ts))}<br>${esc(a.message)}</div></div>`).join("");
    if (alerts.some((a) => !a.seen)) api("/api/alerts/seen", {}).catch(() => {});
    const q = new URLSearchParams();
    if ($("#fRetailer").value) q.set("retailer", $("#fRetailer").value);
    if ($("#fStore").value) q.set("store", $("#fStore").value);
    if ($("#fStage").value) q.set("stage", $("#fStage").value);
    if ($("#fType").value) q.set("ctype", $("#fType").value);
    q.set("min_score", "25");
    q.set("sort", $("#fSort").value || "score");
    const rows = await api(`/api/predictions?${q}`);
    $("#ranked").innerHTML = rows.length ? rows.map(card).join("") : `<div class="empty"><i>search</i>Nothing scored yet. Run <b>Verify</b> or a <b>Sweep</b> from Settings.</div>`;
  }
  ["#fRetailer", "#fStore", "#fStage", "#fSort", "#fType"].forEach((s) => $(s).addEventListener("change", loadHome));

  function card(r) {
    const eff = r.clearance_price ?? r.price;
    const reasons = (() => { try { return JSON.parse(r.reasons || "[]"); } catch { return []; } })();
    return `<article class="card" data-item="${esc(r.retailer)}/${esc(r.item_id)}">
      <div class="head"><span class="score ${esc(r.stage)}">${r.score}</span><span class="title">${esc(r.name || r.item_id)}</span></div>
      <div class="meta">${esc(LABELS[r.retailer] || r.retailer)} · ${esc(r.store_name || r.store_id)} ·
        <span class="price ${eff != null && eff <= 0.01 ? "penny" : ""}">${money(eff)}</span>${r.msrp && r.msrp !== eff ? `<span class="strike" title="MSRP">${money(r.msrp)}</span>` : ""}
        · qty ${r.qty ?? "?"} · ${esc(ago(r.observed_at))}
        ${r.store_status === "CLEARANCE" ? '<span class="tag clr">CLEARANCE</span>' : ""}${r.store_status === "PENNY" ? '<span class="tag penny">PENNY</span>' : ""}
        ${r.ctype && r.ctype !== "other" ? `<span class="tag type">${esc(TYPES[r.ctype] || r.ctype)}</span>` : ""}
        ${r.sku ? `<span class="tag">SKU ${esc(r.sku)}</span>` : ""}${r.upc ? `<span class="tag">UPC ${esc(r.upc)}</span>` : ""}</div>
      ${pennyWhen(r)}
      <div class="reasons">${esc(reasons.slice(0, 4).join(" · "))}</div></article>`;
  }
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-item]");
    if (el) openItem(...el.dataset.item.split("/"));
  });

  // ---- item ----
  async function openItem(retailer, itemId) {
    showTab("item");
    $("#itemView").innerHTML = `<p class="muted">Loading…</p>`;
    const d = await api(`/api/item/${retailer}/${itemId}`);
    const it = d.item;
    const latest = d.latest.map((o) => `<article class="card">
      <div class="head"><span class="score ${esc(o.stage || "watch")}">${o.score ?? "–"}</span><span class="title">${esc(o.store_name || o.store_id)}</span></div>
      <div class="meta"><span class="price ${o.price != null && o.price <= 0.01 ? "penny" : ""}">${money(o.clearance_price ?? o.price)}</span>${o.original ? `<span class="strike">${money(o.original)}</span>` : ""}
        · qty ${o.qty ?? "?"} · ${esc(o.store_status || "")} ${esc(o.promo || "")} · ${esc(ago(o.ts))}</div>
      ${pennyWhen({ penny: o.penny, first_reported: d.first_reported })}
      <div class="reasons">${esc((o.reasons || []).join(" · "))}</div></article>`).join("");
    const hist = d.history.slice(0, 60).map((h) => `<tr><td>${new Date(h.ts * 1000).toLocaleString()}</td><td>${esc(h.store_id)}</td>
      <td class="price">${money(h.price)}</td><td>${money(h.clearance_price)}</td><td>${h.qty ?? ""}</td><td>${esc(h.store_status || "")}</td></tr>`).join("");
    const reps = d.reports.map((r) => `<div class="small-text">${esc(r.source)} · ${esc(ago(r.reported_at || r.fetched_at))} · ${esc(r.store_hint || "")} ${r.url ? `<a href="${esc(r.url)}" target="_blank">source</a>` : ""}</div>`).join("");
    $("#itemView").innerHTML = `
      <h5>${esc(it.name || it.item_id)}</h5>
      <div class="kv"><span>Retailer</span><span>${esc(LABELS[retailer] || retailer)}</span>
        <span>Item</span><span>${esc(it.item_id)}${it.sku ? ` · SKU ${esc(it.sku)}` : ""}${it.upc ? ` · UPC ${esc(it.upc)}` : ""}${it.model ? ` · ${esc(it.model)}` : ""}</span>
        ${it.url ? `<span>Link</span><span><a href="${esc(it.url)}" target="_blank">${esc(it.url)}</a></span>` : ""}</div>
      <div class="row" style="margin:8px 0">
        <button class="border" id="recheck">Re-check now</button>
        <button class="border" id="watchBtn">${d.watched ? "Unwatch" : "Watch"}</button></div>
      <h6>At your stores</h6>${latest || '<div class="empty"><i>storefront</i>No observation yet.</div>'}
      <h6>Community reports</h6>${reps || '<div class="empty"><i>forum</i>No community report for this one.</div>'}
      <h6>History</h6><table class="hist"><tr><th>When</th><th>Store</th><th>Price</th><th>Clearance</th><th>Qty</th><th>Status</th></tr>${hist}</table>`;
    $("#recheck").addEventListener("click", async () => {
      snack("Checking…");
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
    $("#scanOut").innerHTML = `<p class="muted">Looking up ${esc(code)}…</p>`;
    const body = { retailer: $("#scanRetailer").value || undefined, store_id: $("#scanStore").value || undefined };
    if (code.length >= 11 && code.length <= 13) body.upc = code; else body.sku = code;
    let d;
    try { d = await api("/api/lookup", body); } catch (e) { $("#scanOut").innerHTML = `<p class="tag err">${esc(e.message)}</p>`; return; }
    if (!d.ok) { $("#scanOut").innerHTML = `<p><span class="tag err">${esc(d.why)}</span></p>`; return; }
    $("#scanOut").innerHTML = d.results.map((o) => o.error
      ? `<article class="card"><div class="title">${esc(LABELS[o.retailer] || o.retailer)} ${esc(o.store_id)}</div><div class="meta"><span class="tag err">${esc(o.error)}</span></div></article>`
      : `<article class="card" data-item="${esc(o.retailer)}/${esc(o.item_id)}">
          <div class="head"><span class="score ${esc(o.stage || "watch")}">${o.score ?? "–"}</span><span class="title">${esc(o.name || o.item_id)}</span></div>
          <div class="meta">${esc(LABELS[o.retailer] || o.retailer)} · ${esc(o.store_name || o.store_id)} ·
            <span class="price ${o.price != null && o.price <= 0.01 ? "penny" : ""}">${money(o.clearance_price ?? o.price)}</span>${o.original ? `<span class="strike">${money(o.original)}</span>` : ""}
            · qty ${o.qty ?? "?"} ${o.store_status ? `<span class="tag ${o.store_status === "PENNY" ? "penny" : "clr"}">${esc(o.store_status)}</span>` : ""}</div>
          <div class="reasons">${esc((o.reasons || []).join(" · "))}</div></article>`).join("");
  }
  $("#scanGo").addEventListener("click", () => doLookup());
  $("#scanInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doLookup(); });
  window.pennyScan = (code) => { showTab("scan"); doLookup(code); };

  // ---- lists ----
  async function loadLists() {
    const q = new URLSearchParams();
    if ($("#lRetailer").value) q.set("retailer", $("#lRetailer").value);
    if ($("#lType").value) q.set("ctype", $("#lType").value);
    q.set("sort", $("#lSort").value || "recent");
    const rows = await api(`/api/reports?${q}`);
    $("#lists").innerHTML = rows.slice(0, 400).map((r) => {
      const local = (r.local || []).map((o) => `<span class="tag ${o.price != null && o.price <= 0.01 ? "penny" : (o.qty > 0 ? "ok" : "")}">${esc(o.store_name)}: ${money(o.price)} · qty ${o.qty ?? "?"}</span>`).join("");
      const link = r.item_id ? `data-item="${esc(r.retailer)}/${esc(r.item_id)}"` : (r.retailer === "dollargeneral" && r.upc ? `data-item="dollargeneral/${esc(r.upc.replace(/^0+/, ""))}"` : "");
      return `<article class="card" ${link}>
        <div class="title">${esc(r.name || r.sku || r.upc)}</div>
        <div class="meta">${esc(LABELS[r.retailer] || r.retailer)} · ${esc(r.source)} · ${esc(ago(r.reported_at || r.fetched_at))}${r.retail ? ` · <span title="MSRP">MSRP ${money(r.retail)}</span>` : ""}
          ${r.ctype && r.ctype !== "other" ? `<span class="tag type">${esc(TYPES[r.ctype] || r.ctype)}</span>` : ""}
          ${r.sku ? `<span class="tag">SKU ${esc(r.sku)}</span>` : ""}${r.upc ? `<span class="tag">UPC ${esc(r.upc)}</span>` : ""} ${esc(r.store_hint || "")}</div>
        ${r.first_reported_at ? `<div class="when"><i>schedule</i>first reported pennied ${esc(day(r.first_reported_at))}${r.reported_at && day(r.reported_at) !== day(r.first_reported_at) ? `, last seen ${esc(day(r.reported_at))}` : ""}</div>` : ""}
        <div class="reasons">${local || '<span class="muted">not checked at your stores yet</span>'}</div></article>`;
    }).join("") || `<div class="empty"><i>list_alt</i>No reports pulled yet.</div>`;
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
    const runs = (state.status?.runs || []).slice(0, 6).map((r) => `<div class="small-text">${esc(r.kind)} · ${esc(ago(r.started))} · ${r.ok == null ? "running" : (r.ok ? "ok" : "failed")}</div>`).join("");
    $("#settings").innerHTML = `
      <h6>Jobs</h6>
      <div class="group"><div class="row wrap">
        <button class="border small" data-job="sources">Pull lists</button>
        <button class="border small" data-job="verify">Verify reports at my stores</button>
        <button class="border small" data-job="sweep">Sweep Home Depot clearance</button>
        <button class="border small" data-job="predict">Re-score</button>
        <button class="border small" data-job="all">Everything</button></div>
      ${runs}</div>
      <h6>Sources</h6>
      ${Object.entries(src).map(([k, v]) => `<div class="small-text">${esc(k)}: ${v.seen} seen, ${v.new} new · ${esc(ago(v.at))}</div>`).join("") || '<p class="muted">Not pulled yet.</p>'}
      <h6>Stores (zip ${esc(cfg.zip)}, ${esc(cfg.radius_miles)} mi)</h6>
      ${Object.entries(byR).map(([r, list]) => `<div class="group"><b>${esc(LABELS[r] || r)}</b>${lanes[r]?.can_sweep ? ' <span class="tag">sweepable</span>' : ""}
        ${list.map((s) => `<div class="store-row"><input type="checkbox" data-store="${esc(r)}/${esc(s.store_id)}" ${s.watched ? "checked" : ""}>
          <label>${esc(s.name)} <span class="muted">${esc(s.store_id)}${s.distance != null ? ` · ${Number(s.distance).toFixed(1)} mi` : ""}${sw[`${r}:${s.store_id}`] ? ` · swept ${esc(ago(sw[`${r}:${s.store_id}`].at))} (${sw[`${r}:${s.store_id}`].n})` : ""}</span></label></div>`).join("")}</div>`).join("")}
      <div class="row" style="margin-top:8px"><select id="addRetailer" class="field small border">${Object.keys(LABELS).map((k) => `<option value="${k}">${esc(LABELS[k])}</option>`).join("")}</select>
        <input id="addStoreId" class="field small border" placeholder="store id" style="max-width:120px"><input id="addStoreName" class="field small border" placeholder="name">
        <button class="border small" id="addStore">Add store</button></div>
      <h6>Phone access</h6>
      ${remote ? `<div class="group"><div class="kv"><span>Tunnel</span><span>${esc(remote.tunnel?.url || remote.tunnel?.error || "starting…")}</span>
        <span>Discovery</span><span>${esc(remote.discovery || "not published (no Cloudflare creds)")}</span></div>
        <div style="margin:8px 0"><img id="pairQr" alt="pairing QR" width="200" height="200"></div>
        <p class="small-text muted">Scan with the Penny Lane iPhone app. The QR carries the token; keep it private.</p></div>` : '<p class="muted">Remote access not available in this session.</p>'}`;
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

  loadStatus().then(() => showTab("home")).catch((e) => { $("#statusLine").textContent = e.message; });
  setInterval(() => { if (!document.hidden) loadStatus().catch(() => {}); }, 60000);
})();
