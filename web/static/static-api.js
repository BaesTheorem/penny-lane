/* Penny Lane, static edition (GitHub Pages). Answers the same /api/* calls
   app.js makes, from data/site.json, which the Mac publishes after each scan.
   Everything about the visitor (location, stores, alerts, seen alerts,
   watched items) lives in localStorage and never leaves this browser. */
(() => {
  const KEY = "pennylane.prefs";
  const SEEN = "pennylane.seen";
  const DATA_URL = "data/site.json";
  const REFRESH_MS = 10 * 60 * 1000;
  const NEAREST = 3;
  let data = null;
  let loadedAt = 0;

  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
  const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
  const prefs = () => ({ radius: 10, stores: null, lat: null, lon: null, label: "", zip: "", watch: [],
    alerts: { enabled: true, minMsrp: 0, imminent: false, retailers: [], notify: false }, ...load(KEY, {}) });
  const setPrefs = (p) => save(KEY, p);

  const miles = (a, b, c, d) => {
    const r = (x) => (x * Math.PI) / 180;
    const h = Math.sin(r(c - a) / 2) ** 2 + Math.cos(r(a)) * Math.cos(r(c)) * Math.sin(r(d - b) / 2) ** 2;
    return 3958.8 * 2 * Math.asin(Math.sqrt(h));
  };
  const effPrice = (e) => e.clearance_price ?? e.price;

  async function ensure(force = false) {
    if (data && !force && Date.now() - loadedAt < REFRESH_MS) return data;
    const r = await fetch(`${DATA_URL}?t=${Math.floor(Date.now() / 60000)}`);
    if (!r.ok) throw new Error(`site data: ${r.status}`);
    data = await r.json();
    loadedAt = Date.now();
    return data;
  }

  // The visitor's point: their saved location, else the metro center.
  const here = () => {
    const p = prefs();
    return p.lat != null ? { lat: p.lat, lon: p.lon } : { lat: data.meta.center.lat, lon: data.meta.center.lon };
  };
  const withDistance = () => {
    const h = here();
    return data.stores.map((s) => ({ ...s, distance: s.lat != null ? miles(h.lat, h.lon, s.lat, s.lon) : null }));
  };
  // Nearest NEAREST stores per retailer within the radius (or the nearest at all when none is in it).
  function pickNearest(radius) {
    const by = {};
    for (const s of withDistance().sort((a, b) => (a.distance ?? 1e9) - (b.distance ?? 1e9))) (by[s.retailer] ||= []).push(s);
    const out = [];
    for (const list of Object.values(by)) {
      const inside = list.filter((s) => s.distance != null && s.distance <= radius);
      out.push(...(inside.length ? inside : list).slice(0, NEAREST).map((s) => `${s.retailer}/${s.store_id}`));
    }
    return out;
  }
  function myStores() {
    const p = prefs();
    const keys = new Set(p.stores ?? pickNearest(p.radius));
    return withDistance().filter((s) => keys.has(`${s.retailer}/${s.store_id}`))
      .sort((a, b) => a.retailer.localeCompare(b.retailer) || (a.distance ?? 1e9) - (b.distance ?? 1e9))
      .map((s) => ({ ...s, watched: 1 }));
  }
  const storeName = (r, sid) => (data.stores.find((s) => s.retailer === r && s.store_id === sid) || {}).name || sid;

  // One row per (item, my store): the shape /api/predictions returns.
  function rows() {
    const mine = new Set(myStores().map((s) => `${s.retailer}/${s.store_id}`));
    const out = [];
    for (const it of Object.values(data.items)) {
      for (const [sid, e] of Object.entries(it.stores)) {
        if (!mine.has(`${it.retailer}/${sid}`)) continue;
        const eff = effPrice(e);
        out.push({
          retailer: it.retailer, item_id: it.item_id, store_id: sid, store_name: storeName(it.retailer, sid),
          score: e.score ?? 0, stage: e.stage, reasons: JSON.stringify(e.reasons || []),
          name: it.name, brand: it.brand, upc: it.upc, sku: it.sku, url: it.url, ctype: it.ctype,
          price: e.price, original: e.original, clearance_price: e.clearance_price, qty: e.qty,
          store_status: e.store_status, observed_at: e.ts, msrp: it.msrp,
          savings: it.msrp != null && eff != null ? Math.round((it.msrp - eff) * 100) / 100 : null,
          changed_at: e.changed_at, penny: e.penny || {}, first_reported: it.first_reported,
        });
      }
    }
    return out;
  }
  const desc = (k) => (a, b) => (b[k] ?? -Infinity) - (a[k] ?? -Infinity);
  const SORTS = {
    score: (a, b) => desc("score")(a, b) || desc("msrp")(a, b),
    msrp: (a, b) => desc("msrp")(a, b) || desc("score")(a, b),
    savings: (a, b) => desc("savings")(a, b) || desc("score")(a, b),
    recent: (a, b) => desc("changed_at")(a, b) || desc("score")(a, b),
  };
  function predictions(q) {
    let out = rows();
    const min = Number(q.get("min_score") || 0);
    out = out.filter((r) => r.score >= min && r.stage !== "gone"
      && (!q.get("retailer") || r.retailer === q.get("retailer")) && (!q.get("store") || r.store_id === q.get("store"))
      && (!q.get("stage") || r.stage === q.get("stage")) && (!q.get("ctype") || r.ctype === q.get("ctype")));
    out.sort(SORTS[q.get("sort")] || SORTS.score);
    if (q.get("group") === "item") {
      const best = new Map();
      for (const r of out) {
        const k = `${r.retailer}/${r.item_id}`;
        if (!best.has(k)) { best.set(k, { ...r, others: [] }); continue; }
        best.get(k).others.push(r);
      }
      out = [...best.values()];
      for (const r of out) r.others.sort((a, b) => (effPrice(a) ?? 1e9) - (effPrice(b) ?? 1e9));
    }
    return out.slice(0, Number(q.get("limit") || 200));
  }

  // Alerts: the visitor's rules over their stores; "seen" is per browser.
  function alerts() {
    const a = prefs().alerts;
    if (!a.enabled) return [];
    const seen = load(SEEN, {});
    return rows().filter((r) => (r.stage === "penny" || (a.imminent && r.score >= 70))
      && (r.msrp ?? 0) >= a.minMsrp && (!a.retailers.length || a.retailers.includes(r.retailer)))
      .map((r) => {
        const id = `${r.retailer}/${r.item_id}/${r.store_id}/${r.stage}`;
        const penny = r.stage === "penny";
        return { id, kind: penny ? "penny_on_shelf" : "imminent", retailer: r.retailer, item_id: r.item_id,
          store_id: r.store_id, store_name: r.store_name, name: r.name, score: r.score,
          ts: (penny && r.penny.since) || r.changed_at || r.observed_at, seen: seen[id] ? 1 : 0,
          message: penny ? "register price is $0.01" : `clearance price $${Number(effPrice(r) ?? 0).toFixed(2)}` };
      }).sort((x, y) => y.ts - x.ts);
  }
  function markSeen() {
    const seen = load(SEEN, {});
    for (const a of alerts()) seen[a.id] = seen[a.id] || Date.now();
    save(SEEN, seen);
  }
  // Browser notification for alerts not yet notified, while a tab is open.
  function notifyNew() {
    const p = prefs();
    if (!p.alerts.notify || !("Notification" in window) || Notification.permission !== "granted") return;
    const told = load("pennylane.told", {});
    const fresh = alerts().filter((a) => !a.seen && !told[a.id]);
    if (!fresh.length) return;
    for (const a of fresh) told[a.id] = Date.now();
    save("pennylane.told", told);
    const first = fresh[0];
    new Notification(fresh.length === 1 ? "Penny Lane" : `Penny Lane: ${fresh.length} new`, {
      body: `${first.name} at ${first.store_name}: ${first.message}`, icon: "static/icon.png", tag: "pennylane" });
  }

  function reports(q) {
    const mine = myStores();
    let out = data.reports.filter((r) => (!q.get("retailer") || r.retailer === q.get("retailer"))
      && (!q.get("ctype") || r.ctype === q.get("ctype")));
    out = out.map((r) => {
      const it = findItem(r);
      const local = it ? mine.filter((s) => s.retailer === it.retailer).map((s) => {
        const e = it.stores[s.store_id];
        return { store_name: s.name, price: e ? effPrice(e) : null, qty: e ? e.qty : null, original: e?.original };
      }) : [];
      return { ...r, local, retail: r.retail ?? it?.msrp ?? null };
    });
    const sort = q.get("sort") || "recent";
    if (sort === "retail") out.sort((a, b) => (b.retail || 0) - (a.retail || 0));
    else if (sort === "stocked") out.sort((a, b) => b.local.reduce((n, o) => n + (o.qty || 0), 0) - a.local.reduce((n, o) => n + (o.qty || 0), 0));
    else out.sort((a, b) => (b.reported_at || b.fetched_at || 0) - (a.reported_at || a.fetched_at || 0));
    return out;
  }
  function findItem(r) {
    if (r.item_id && data.items[`${r.retailer}/${r.item_id}`]) return data.items[`${r.retailer}/${r.item_id}`];
    return Object.values(data.items).find((it) => it.retailer === r.retailer
      && ((r.sku && it.sku === r.sku) || (r.upc && it.upc && it.upc.replace(/^0+/, "") === String(r.upc).replace(/^0+/, ""))));
  }

  function item(retailer, itemId) {
    const it = data.items[`${retailer}/${itemId}`];
    if (!it) throw new Error("This item is not in the published data.");
    const mine = myStores().filter((s) => s.retailer === retailer);
    const latest = mine.filter((s) => it.stores[s.store_id]).map((s) => {
      const e = it.stores[s.store_id];
      return { ...e, store_id: s.store_id, store_name: s.name, ts: e.ts, msrp: it.msrp,
        promo: e.chain_price ? "chain price" : "" };
    });
    const ids = new Set(mine.map((s) => s.store_id));
    const history = (it.history || []).filter((h) => ids.has(h[0])).map((h) => ({
      store_id: h[0], ts: h[1], price: h[2], clearance_price: h[3], qty: h[4], store_status: h[5] })).sort((a, b) => b.ts - a.ts);
    const reps = data.reports.filter((r) => r.retailer === retailer && (r.item_id === itemId
      || (it.sku && r.sku === it.sku) || (it.upc && r.upc === it.upc)));
    return { item: it, latest, history, reports: reps, watched: prefs().watch.includes(`${retailer}/${itemId}`),
      first_reported: it.first_reported };
  }

  function lookup(body) {
    const code = String(body.upc || body.sku || body.item_id || "").replace(/^0+/, "");
    const hits = Object.values(data.items).filter((it) => (!body.retailer || it.retailer === body.retailer)
      && [it.item_id, it.sku, (it.upc || "").replace(/^0+/, "")].includes(code));
    if (!hits.length) return { ok: false, why: "Not in the published data. This site knows only the items on the community penny lists and the ones checked hourly." };
    const results = [];
    for (const it of hits) {
      for (const s of myStores().filter((x) => x.retailer === it.retailer && (!body.store_id || x.store_id === String(body.store_id)))) {
        const e = it.stores[s.store_id];
        if (!e) { results.push({ retailer: it.retailer, store_id: s.store_id, error: `${s.name}: not on the shelf at the last check` }); continue; }
        results.push({ ...e, retailer: it.retailer, item_id: it.item_id, store_id: s.store_id, store_name: s.name,
          name: it.name, msrp: it.msrp, first_reported: it.first_reported });
      }
    }
    return { ok: true, results };
  }

  async function setLocation(b) {
    const p = prefs();
    if (b.radius_miles) p.radius = Number(b.radius_miles);
    if (b.zip) {
      const r = await fetch(`https://api.zippopotam.us/us/${encodeURIComponent(b.zip)}`);
      if (!r.ok) return { ok: false, why: `zip ${b.zip} not found` };
      const pl = (await r.json()).places[0];
      Object.assign(p, { zip: b.zip, lat: Number(pl.latitude), lon: Number(pl.longitude), label: `${pl["place name"]}, ${pl["state abbreviation"]}` });
    } else if (b.lat != null) {
      Object.assign(p, { zip: "", lat: b.lat, lon: b.lon, label: "Your location" });
    } else {
      return { ok: false, why: "Location is blocked in this browser. Type a zip code." };
    }
    setPrefs(p);
    p.stores = pickNearest(p.radius);
    setPrefs(p);
    const lanes = {};
    for (const k of p.stores) { const [r, sid] = k.split("/"); ((lanes[r] ||= { watched: [] }).watched).push(sid); }
    const near = withDistance().filter((s) => s.distance != null).sort((a, b) => a.distance - b.distance)[0];
    if (near && near.distance > data.meta.radius_miles + 10) {
      return { ok: true, place: { label: p.label, zip: p.zip }, lanes,
        warn: `The nearest store this site checks is ${Math.round(near.distance)} mi away. It covers the Kansas City area only.` };
    }
    return { ok: true, place: { label: p.label, zip: p.zip }, lanes };
  }

  function status() {
    const sources = data.sources || {};
    return { zip: prefs().zip || data.meta.center.zip, location: { label: prefs().label }, stores: myStores(),
      counts: { observations: data.meta.observations, reports: data.reports.length, items: Object.keys(data.items).length,
        alerts_unseen: alerts().filter((a) => !a.seen).length },
      runs: [{ kind: "update", started: data.meta.generated_at, finished: data.meta.generated_at, ok: 1 }],
      sources, sweeps: {}, jobs: {}, lanes: {} };
  }

  async function api(path, body) {
    await ensure();
    const u = new URL(path, location.href);
    const q = u.searchParams;
    const p = u.pathname.replace(/^.*\/api\//, "/api/").replace(/^.*\/remote\//, "/remote/");
    const m = p.match(/^\/api\/item\/([^/]+)\/([^/]+)$/);
    if (m) return item(m[1], decodeURIComponent(m[2]));
    switch (p) {
      case "/api/status": return status();
      case "/api/types": return data.types;
      case "/api/predictions": return predictions(q);
      case "/api/alerts": return alerts().slice(0, Number(q.get("limit") || 100));
      case "/api/alerts/seen": markSeen(); return { ok: true };
      case "/api/reports": return reports(q);
      case "/api/lookup": return lookup(body || {});
      case "/api/location": return setLocation(body || {});
      case "/api/config": { const pr = prefs(); return { zip: pr.zip, radius_miles: pr.radius, location: { label: pr.label } }; }
      case "/api/stores": {
        const mine = new Set(myStores().map((s) => `${s.retailer}/${s.store_id}`));
        return withDistance().map((s) => ({ ...s, watched: mine.has(`${s.retailer}/${s.store_id}`) ? 1 : 0 }))
          .sort((a, b) => a.retailer.localeCompare(b.retailer) || (a.distance ?? 1e9) - (b.distance ?? 1e9));
      }
      case "/api/stores/watch": {
        const pr = prefs();
        const set = new Set(pr.stores ?? myStores().map((s) => `${s.retailer}/${s.store_id}`));
        const k = `${body.retailer}/${body.store_id}`;
        if (body.watched) set.add(k); else set.delete(k);
        pr.stores = [...set];
        setPrefs(pr);
        return { ok: true };
      }
      case "/api/watch": {
        const pr = prefs();
        const k = `${body.retailer}/${body.item_id}`;
        pr.watch = body.remove ? pr.watch.filter((x) => x !== k) : [...new Set([...pr.watch, k])];
        setPrefs(pr);
        return { ok: true };
      }
      default: throw new Error(`${path}: not on the static site`);
    }
  }

  // Re-read the data on a timer so an open tab sees new pennies, and notify.
  setInterval(() => { if (!document.hidden) ensure(true).then(notifyNew).catch(() => {}); }, REFRESH_MS);
  window.pennyStatic = { api, prefs, setPrefs, ensure, notifyNew, meta: () => data?.meta,
    topics: () => myStores().map((s) => ({ store: s, topic: `${data.meta.ntfy.prefix}-${s.retailer}-${s.store_id}` })) };
})();
