// Coldreel — front sin dependencias. Estado: cámara (MAC) + día (zona horaria del navegador).
(() => {
  const $ = (s) => document.querySelector(s);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const state = { mac: null, day: null, files: [], active: null, coverage: new Set(), av1ok: false };
  const fmtTime = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const fmtDur = (ms) => { if (ms == null) return "—"; const s = Math.round(ms / 1000); const m = Math.floor(s / 60); return m ? `${m} min ${String(s % 60).padStart(2, "0")} s` : `${s} s`; };
  const fmtBytes = (b) => b >= 1e12 ? (b / 1e12).toFixed(2) + " TB" : b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(0) + " MB";
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const dayBounds = (iso) => { const [y, m, d] = iso.split("-").map(Number); const a = new Date(y, m - 1, d); const b = new Date(y, m - 1, d + 1); return [a.getTime(), b.getTime()]; };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).detail || msg; } catch {} throw new Error(`${r.status}: ${msg}`); }
    return r.json();
  }

  function setDay(iso) {
    state.day = iso; $("#day").value = iso;
    const u = new URL(location); u.searchParams.set("day", iso); if (state.mac) u.searchParams.set("mac", state.mac); history.replaceState(null, "", u);
    loadTimeline();
  }
  function shiftDay(n) { const [y, m, d] = state.day.split("-").map(Number); setDay(isoDay(new Date(y, m - 1, d + n))); }

  async function loadHealth() {
    try { const h = await api("/api/health"); state.av1ok = !!(h.remux && h.remux.av1_1004); $("#health").textContent = `${h.index.files} ficheros · ${h.index.files_indexed} indexados · caché ${fmtBytes(h.cache.bytes)}${state.av1ok ? "" : " · sin AV1 nuevo"}`; } catch (e) { $("#health").textContent = e.message; }
  }

  async function loadCameras() {
    const cams = await api("/api/cameras");
    const ul = $("#cameras"); ul.innerHTML = "";
    for (const c of cams) {
      const li = document.createElement("li"); li.dataset.mac = c.mac;
      const from = c.first_ms ? new Date(c.first_ms).toLocaleDateString() : "—"; const to = c.last_ms ? new Date(c.last_ms).toLocaleDateString() : "—";
      li.innerHTML = `<div class="name">${c.name}</div><div class="meta">${c.files} fich · ${fmtBytes(c.bytes || 0)}<br>${from} → ${to}<br><span class="muted">${c.mac}</span></div>`;
      li.onclick = () => selectCamera(c.mac);
      ul.appendChild(li);
    }
    const wanted = new URL(location).searchParams.get("mac");
    if (wanted && cams.some((c) => c.mac === wanted)) selectCamera(wanted); else if (cams.length) selectCamera(cams[0].mac);
  }

  async function selectCamera(mac) {
    state.mac = mac;
    document.querySelectorAll("#cameras li").forEach((li) => li.classList.toggle("active", li.dataset.mac === mac));
    try { const cov = await api(`/api/coverage?mac=${encodeURIComponent(mac)}&tz=${encodeURIComponent(tz)}`); state.coverage = new Set(cov.map((d) => d.date)); }
    catch { state.coverage = new Set(); }
    const wanted = new URL(location).searchParams.get("day");
    if (!state.day) setDay(wanted || [...state.coverage].pop() || isoDay(new Date())); else loadTimeline();
  }

  function drawHours() {
    const hours = $("#hours"); hours.innerHTML = "";
    for (let h = 0; h <= 24; h += 2) { const s = document.createElement("span"); s.style.left = (h / 24 * 100) + "%"; s.textContent = String(h).padStart(2, "0"); hours.appendChild(s); }
  }

  async function loadTimeline() {
    if (!state.mac || !state.day) return;
    const [a, b] = dayBounds(state.day);
    const track = $("#track"); track.innerHTML = "";
    for (let h = 1; h < 24; h++) { const t = document.createElement("div"); t.className = "tick"; t.style.left = (h / 24 * 100) + "%"; track.appendChild(t); }
    let data;
    try { data = await api(`/api/timeline?mac=${encodeURIComponent(state.mac)}&from_ms=${a}&to_ms=${b}`); }
    catch (e) { $("#summary").textContent = e.message; return; }
    state.files = data.files;
    const pct = (ms) => Math.max(0, Math.min(100, (ms - a) / (b - a) * 100));
    let nParts = 0, nUnindexed = 0, totalMs = 0;
    const rows = [];
    for (const f of data.files) {
      if (!f.indexed) {
        nUnindexed++;
        const el = document.createElement("div"); el.className = "file";
        el.style.left = pct(f.start_ms) + "%"; el.style.width = Math.max(0.2, pct(f.end_ms) - pct(f.start_ms)) + "%";
        el.title = `Fichero sin indexar: ${f.rel}\n${fmtTime(f.start_ms)} → ${fmtTime(f.end_ms)} · pulsa para indexarlo`;
        el.onclick = () => indexWindow(f.start_ms, f.end_ms);
        track.appendChild(el);
        continue;
      }
      for (const p of f.partitions) {
        if (p.t_first_ms == null || p.t_last_ms < a || p.t_first_ms > b) continue;
        nParts++; totalMs += p.duration_ms || 0;
        const el = document.createElement("div"); el.className = "seg" + (p.video_track === 1004 && !state.av1ok ? " av1new" : "");
        el.style.left = pct(p.t_first_ms) + "%"; el.style.width = Math.max(0.15, pct(p.t_last_ms) - pct(p.t_first_ms)) + "%";
        el.title = `${fmtTime(p.t_first_ms)} → ${fmtTime(p.t_last_ms)} · ${fmtDur(p.duration_ms)} · ${p.codec || "?"}`;
        el.dataset.key = `${f.id}/${p.ordinal}`;
        el.onclick = () => play(f, p);
        track.appendChild(el);
        rows.push({ f, p });
      }
    }
    rows.sort((x, y) => x.p.t_first_ms - y.p.t_first_ms);
    const tbody = $("#clips"); tbody.innerHTML = "";
    for (const { f, p } of rows) {
      const tr = document.createElement("tr"); tr.dataset.key = `${f.id}/${p.ordinal}`;
      const codec = p.video_track === 1004 && !state.av1ok ? `<span class="badge warn">av1 (pista 1004)</span>` : `<span class="badge">${p.codec || "?"}</span>`;
      tr.innerHTML = `<td>${fmtTime(p.t_first_ms)}</td><td>${fmtDur(p.duration_ms)}</td><td>${codec}</td><td class="muted">${f.rel.split("/").pop()} · #${p.ordinal}${p.smart_events ? ` · ${p.smart_events} ev` : ""}</td><td>${p.cached ? '<span class="badge ok">mp4</span>' : ""}</td>`;
      tr.onclick = () => play(f, p);
      tbody.appendChild(tr);
    }
    const parts = [`${data.files.length} ficheros`, `${nParts} grabaciones`, `${fmtDur(totalMs)} de vídeo`];
    if (nUnindexed) parts.push(`<span class="badge warn">${nUnindexed} sin indexar</span>`);
    $("#summary").innerHTML = parts.join(" · ");
    $("#indexDay").disabled = nUnindexed === 0;
    if (state.active) highlight(state.active);
  }

  function highlight(key) {
    document.querySelectorAll("[data-key]").forEach((el) => el.classList.toggle("active", el.dataset.key === key));
  }

  async function play(f, p) {
    const key = `${f.id}/${p.ordinal}`; state.active = key; highlight(key);
    const v = $("#video"); const info = $("#clipInfo");
    info.textContent = `Preparando ${fmtTime(p.t_first_ms)} (${fmtDur(p.duration_ms)}, ${p.codec || "?"})…`;
    if (p.video_track === 1004 && !state.av1ok) { info.innerHTML = `<span class="badge warn">AV1 de firmware nuevo</span> el remux configurado no convierte esta partición (hace falta un build de main y <code>remux_av1_1004 = true</code>). El .ubv está íntegro.`; v.removeAttribute("src"); v.load(); return; }
    try {
      // HEAD primero: dispara el remux (puede tardar unos segundos) y muestra el error si lo hay.
      const r = await fetch(p.clip_url, { method: "HEAD" });
      if (!r.ok) { let msg = r.statusText; try { msg = (await fetch(p.clip_url).then((x) => x.json())).detail || msg; } catch {} throw new Error(`${r.status}: ${msg}`); }
      v.src = p.clip_url; v.play().catch(() => {});
      info.innerHTML = `${new Date(p.t_first_ms).toLocaleString()} → ${fmtTime(p.t_last_ms)} · ${fmtDur(p.duration_ms)} · <span class="badge">${p.codec}</span> · ${p.frames_video} frames${p.smart_events ? ` · ${p.smart_events} smart events` : ""} · <a class="muted" href="${p.clip_url}" download>descargar</a>`;
      p.cached = true; const tr = document.querySelector(`tr[data-key="${key}"] td:last-child`); if (tr) tr.innerHTML = '<span class="badge ok">mp4</span>';
    } catch (e) { info.innerHTML = `<span class="badge bad">error</span> ${e.message}`; }
  }

  async function indexWindow(a, b) {
    const btn = $("#indexDay"); btn.disabled = true; const old = btn.textContent; btn.textContent = "Indexando…";
    try { const r = await api(`/api/index?mac=${encodeURIComponent(state.mac)}&from_ms=${a}&to_ms=${b}`, { method: "POST" }); if (r.errors.length) alert(r.errors.map((e) => e.error).join("\n")); }
    catch (e) { alert(e.message); }
    finally { btn.textContent = old; await loadTimeline(); loadHealth(); }
  }

  function nextClip(dir) {
    const rows = [...document.querySelectorAll("#clips tr")]; const i = rows.findIndex((r) => r.dataset.key === state.active);
    const r = rows[i + dir]; if (r) r.click();
  }

  $("#prevDay").onclick = () => shiftDay(-1);
  $("#nextDay").onclick = () => shiftDay(1);
  $("#today").onclick = () => setDay(isoDay(new Date()));
  $("#day").onchange = (e) => setDay(e.target.value);
  $("#indexDay").onclick = () => { const [a, b] = dayBounds(state.day); indexWindow(a, b); };
  document.addEventListener("keydown", (e) => { if (e.target.tagName === "INPUT") return; if (e.key === "ArrowRight" && e.shiftKey) shiftDay(1); else if (e.key === "ArrowLeft" && e.shiftKey) shiftDay(-1); else if (e.key === "ArrowRight") nextClip(1); else if (e.key === "ArrowLeft") nextClip(-1); });
  $("#video").addEventListener("ended", () => nextClip(1));

  drawHours(); loadHealth(); loadCameras(); setInterval(loadHealth, 60000);
})();
