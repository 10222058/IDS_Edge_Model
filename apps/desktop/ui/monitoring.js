/* Native Monitoring tabs — talks to control-service via wizard /api/v1 proxy. */
(function () {
  const TABS = [
    { id: "metric", label: "Gateway Metric" },
    { id: "activities", label: "Gateway Activities" },
  ];
  const WIZARD_MONITORING = "http://127.0.0.1:9138/monitoring.html";
  let tab = "metric";
  let timer = null;
  let lastWarnings = [];

  function tabFromHash() {
    const h = (location.hash || "").replace(/^#/, "").toLowerCase();
    if (h === "activities" || h === "gateway") return "activities";
    if (h === "metric" || h === "raspi" || h === "host") return "metric";
    return "metric";
  }

  const $ = (id) => document.getElementById(id);

  /** Wizard shell proxies /api/v1 and injects IOTGW_API_TOKEN — not Tauri asset origin. */
  function onWizardOrigin() {
    if (location.protocol !== "http:" && location.protocol !== "https:") {
      return false;
    }
    const host = location.hostname;
    return host === "127.0.0.1" || host === "localhost";
  }

  async function control(path, opts) {
    if (!onWizardOrigin()) {
      throw new Error(
        "Open Monitoring from the Setup Wizard at " +
          WIZARD_MONITORING +
          " (same-origin /api/v1 proxy)."
      );
    }
    const headers = { "Content-Type": "application/json", ...(opts && opts.headers) };
    const res = await fetch(path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail || data.error || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function showWarnings(warnings) {
    lastWarnings = Array.isArray(warnings) ? warnings : [];
    const banner = $("warn-banner");
    if (!banner) return;
    if (!lastWarnings.length) {
      banner.classList.add("hidden");
      banner.textContent = "";
      return;
    }
    banner.classList.remove("hidden");
    banner.textContent =
      "Backend warnings (" +
      lastWarnings.length +
      "): " +
      String(lastWarnings[0]).slice(0, 240);
  }

  function setStatusBadge(kind, text) {
    $("hdr-status").innerHTML =
      `<span class="badge ${kind}">${escapeHtml(text)}</span>`;
  }

  const METRIC_LINE_CHARTS = [
    "cpu_pct",
    "mem_used_pct",
    "load1",
    "fs_used_pct",
    "net_rx_mbps",
    "net_tx_mbps",
    "temp_c",
  ];

  function sparkSvg(values, opts) {
    const w = (opts && opts.w) || 96;
    const h = (opts && opts.h) || 28;
    if (!values || !values.length) {
      return `<svg viewBox="0 0 ${w} ${h}" class="spark"><text x="4" y="${h / 2 + 3}" fill="#8b9bb4" font-size="10">—</text></svg>`;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const pts = values
      .map((v, i) => {
        const x = (i / Math.max(values.length - 1, 1)) * (w - 2) + 1;
        const y = h - 2 - ((v - min) / span) * (h - 4);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" class="spark" preserveAspectRatio="none"><polyline fill="none" stroke="#4da3ff" stroke-width="1.5" points="${pts}" /></svg>`;
  }

  function chartEmptySvg(w, h, message) {
    const msg = message || "No data in range";
    return (
      `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="${escapeHtml(msg)}">` +
      `<rect x="36" y="10" width="${w - 46}" height="${h - 28}" fill="none" stroke="#243044" stroke-dasharray="4 4"/>` +
      `<line x1="36" y1="${h - 18}" x2="${w - 10}" y2="${h - 18}" stroke="#243044"/>` +
      `<line x1="36" y1="10" x2="36" y2="${h - 18}" stroke="#243044"/>` +
      `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" fill="#8b9bb4" font-size="12">${escapeHtml(msg)}</text>` +
      `</svg>`
    );
  }

  function lineChartSvg(values, opts) {
    const w = (opts && opts.w) || 360;
    const h = (opts && opts.h) || 140;
    const padL = 36;
    const padR = 10;
    const padT = 10;
    const padB = 18;
    if (!values || !values.length) {
      return chartEmptySvg(w, h, (opts && opts.empty) || "No data in range");
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const pts = values
      .map((v, i) => {
        const x = padL + (i / Math.max(values.length - 1, 1)) * plotW;
        const y = padT + plotH - ((v - min) / span) * plotH;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    const area =
      `${padL},${padT + plotH} ` + pts + ` ${padL + plotW},${padT + plotH}`;
    const yTop = max.toFixed(max >= 10 ? 0 : 1);
    const yBot = min.toFixed(min >= 10 ? 0 : 1);
    return (
      `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#0b1118"/>` +
      `<line x1="${padL}" y1="${padT + plotH}" x2="${padL + plotW}" y2="${padT + plotH}" stroke="#243044"/>` +
      `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" stroke="#243044"/>` +
      `<text x="4" y="${padT + 8}" fill="#8b9bb4" font-size="9">${yTop}</text>` +
      `<text x="4" y="${padT + plotH}" fill="#8b9bb4" font-size="9">${yBot}</text>` +
      `<polygon fill="rgba(77,163,255,0.14)" points="${area}"/>` +
      `<polyline fill="none" stroke="#4da3ff" stroke-width="1.8" points="${pts}"/>` +
      `</svg>`
    );
  }

  function barChartSvg(rows, opts) {
    const w = (opts && opts.w) || 360;
    const h = (opts && opts.h) || 160;
    const padL = 78;
    const padR = 12;
    const padT = 8;
    const padB = 12;
    if (!rows || !rows.length) {
      return chartEmptySvg(w, h, (opts && opts.empty) || "No data in range");
    }
    const max = Math.max(...rows.map((r) => Number(r.value) || 0), 0.001);
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const rowH = plotH / rows.length;
    const bars = rows
      .map((r, i) => {
        const val = Number(r.value) || 0;
        const bw = (val / max) * plotW;
        const y = padT + i * rowH + rowH * 0.18;
        const bh = rowH * 0.64;
        const label = String(r.name || "?").slice(0, 14);
        const tip = val >= 10 ? val.toFixed(0) : val.toFixed(2);
        return (
          `<text x="4" y="${(y + bh / 2 + 3).toFixed(1)}" fill="#8b9bb4" font-size="9">${escapeHtml(label)}</text>` +
          `<rect x="${padL}" y="${y.toFixed(1)}" width="${Math.max(bw, 1).toFixed(1)}" height="${bh.toFixed(1)}" fill="#4da3ff" rx="2"/>` +
          `<text x="${(padL + Math.max(bw, 1) + 4).toFixed(1)}" y="${(y + bh / 2 + 3).toFixed(1)}" fill="#c9d4e5" font-size="9">${tip}</text>`
        );
      })
      .join("");
    return (
      `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
      `<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#0b1118"/>` +
      bars +
      `</svg>`
    );
  }

  function paintLineChart(id, values) {
    const el = $(id);
    if (!el) return;
    el.innerHTML = lineChartSvg(values || [], { w: 420, h: 150 });
  }

  function paintBarChart(id, rows) {
    const el = $(id);
    if (!el) return;
    el.innerHTML = barChartSvg(rows || [], { w: 420, h: 170 });
  }

  function paintEmptyMetricCharts() {
    METRIC_LINE_CHARTS.forEach((key) => paintLineChart("chart-" + key, []));
    paintBarChart("chart-container_cpu", []);
    paintBarChart("chart-container_mem", []);
  }

  function fmtBytes(n) {
    if (n == null || !isFinite(n)) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let v = Number(n);
    let i = 0;
    while (v >= 1024 && i < u.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
  }

  function fmtUptime(seconds) {
    const s = Number(seconds) || 0;
    if (!s) return "—";
    const days = s / 86400;
    if (days >= 1) return `${days.toFixed(1)} d`;
    const hours = s / 3600;
    if (hours >= 1) return `${hours.toFixed(1)} h`;
    return `${Math.round(s / 60)} m`;
  }

  function gaugeTone(pct) {
    if (pct == null || !isFinite(pct)) return "";
    if (pct >= 90) return "bad";
    if (pct >= 70) return "warn";
    return "";
  }

  function renderGauges(el, items) {
    if (!el) return;
    el.innerHTML = items
      .map(([label, display, pct]) => {
        const width = pct == null || !isFinite(pct) ? 0 : Math.max(0, Math.min(100, pct));
        const tone = gaugeTone(pct);
        return (
          `<div class="gauge-card">` +
          `<div class="label">${escapeHtml(label)}</div>` +
          `<div class="value">${escapeHtml(display)}</div>` +
          `<div class="gauge-track"><div class="gauge-fill ${tone}" style="width:${width}%"></div></div>` +
          `</div>`
        );
      })
      .join("");
  }

  function renderFsBar(el, usedPct, usedLabel, totalLabel) {
    if (!el) return;
    const pct = usedPct == null || !isFinite(usedPct) ? null : usedPct;
    const width = pct == null ? 0 : Math.max(0, Math.min(100, pct));
    const tone = gaugeTone(pct);
    el.innerHTML =
      `<div class="fs-meta"><strong>Root filesystem</strong>` +
      `<span class="muted">${escapeHtml(usedLabel)} / ${escapeHtml(totalLabel)}` +
      `${pct == null ? "" : ` · ${pct.toFixed(0)}% used`}</span></div>` +
      `<div class="gauge-track"><div class="gauge-fill ${tone}" style="width:${width}%"></div></div>`;
  }

  function metricCards(el, items) {
    el.innerHTML = items
      .map(
        ([label, value]) =>
          `<div class="metric-card"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>`
      )
      .join("");
  }

  function fillTable(table, headers, rows) {
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    thead.innerHTML = `<tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr>`;
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="${headers.length}" class="muted">No data</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
      .join("");
  }

  function buildTabs() {
    const nav = $("mon-tabs");
    nav.innerHTML = "";
    TABS.forEach((t) => {
      const b = document.createElement("button");
      b.textContent = t.label;
      b.dataset.tab = t.id;
      b.classList.toggle("active", t.id === tab);
      b.addEventListener("click", () => showTab(t.id, { syncHash: true }));
      nav.appendChild(b);
    });
  }

  function showTab(id, opts) {
    const next = id === "activities" ? "activities" : "metric";
    tab = next;
    document.querySelectorAll(".mon-panel").forEach((el) => {
      el.classList.toggle("hidden", el.dataset.tab !== tab);
    });
    document.querySelectorAll("#mon-tabs button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    const wantHash = "#" + tab;
    if ((opts || {}).syncHash !== false && location.hash !== wantHash) {
      history.replaceState(null, "", wantHash);
    }
    if (typeof window.iotgwRefreshNav === "function") {
      window.iotgwRefreshNav();
    }
  }

  async function loadGateways() {
    const sel = $("gateway-id");
    try {
      const rows = await control("/api/v1/gateways");
      sel.innerHTML = "";
      (rows || []).forEach((g) => {
        const opt = document.createElement("option");
        opt.value = g.id;
        opt.textContent = `${g.id} (${g.host})`;
        sel.appendChild(opt);
      });
      if (!rows || !rows.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "(no gateways in registry)";
        sel.appendChild(opt);
      }
    } catch (e) {
      sel.innerHTML = `<option value="">control API offline</option>`;
      $("hdr-status").innerHTML = `<span class="badge bad">${escapeHtml(e.message)}</span>`;
      throw e;
    }
  }

  async function refreshGateway() {
    const range = $("range").value;
    const data = await control(`/api/v1/query/summaries/gateway?range=${encodeURIComponent(range)}`);
    const m = data.metrics || {};
    metricCards($("gw-metrics"), [
      ["Sensor msgs", String(m.total_sensor_messages ?? "—")],
      ["Msgs / min", String(m.messages_per_minute ?? "—")],
      ["Denied", String(m.denied_publishes ?? "—")],
      ["IDS", String(m.ids_detections ?? "—")],
      ["Allowed IPs", String(m.allowed_ips ?? "—")],
    ]);
    fillTable(
      $("gw-connected"),
      ["Device", "Client", "Source IP"],
      (data.connected_sensors || []).map((r) => [
        escapeHtml(r.device_id),
        escapeHtml(r.client_id),
        escapeHtml(r.source_ip),
      ])
    );
    fillTable(
      $("gw-delay"),
      ["Device", "Latest", "Sparkline"],
      (data.delay_by_device || []).map((r) => [
        escapeHtml(r.device_id),
        r.latest == null ? "—" : Number(r.latest).toFixed(4),
        sparkSvg(r.sparkline || []),
      ])
    );
    $("gw-msg").textContent = `range=${data.range}`;
    showWarnings(data.warnings);
  }

  async function refreshRaspi() {
    const range = $("range").value;
    const data = await control(
      `/api/v1/query/summaries/raspi?range=${encodeURIComponent(range)}`
    );
    const m = data.metrics || {};
    const charts = data.charts || {};
    const containers = data.containers || {};

    metricCards($("raspi-metrics"), [
      ["Status", data.up ? "up" : "down"],
      ["Temp", m.temp_c == null ? "—" : `${m.temp_c} °C`],
      ["Uptime", fmtUptime(m.uptime_s)],
      ["CPU busy (cores)", String(m.cpu_cores_busy ?? "—")],
      ["Cores", String(m.cpu_cores ?? "—")],
      ["Load1", String(m.load1 ?? "—")],
      ["Containers", String(m.active_containers ?? "—")],
    ]);
    metricCards($("raspi-metrics-extra"), [
      ["Mem used", `${m.mem_used_pct ?? "—"}%`],
      ["Mem", `${fmtBytes(m.mem_used_bytes)} / ${fmtBytes(m.mem_total_bytes)}`],
      ["Swap", `${m.swap_pct ?? "—"}%`],
      ["IOWait", String(m.iowait_cores ?? "—")],
      [
        "Procs",
        `R:${m.procs_running ?? 0} B:${m.procs_blocked ?? 0}`,
      ],
      ["Disk used", `${m.fs_used_pct ?? "—"}%`],
    ]);

    const cpuPct =
      m.cpu_cores && m.cpu_cores_busy != null
        ? Math.min(100, (Number(m.cpu_cores_busy) / Number(m.cpu_cores)) * 100)
        : null;
    renderGauges($("raspi-gauges"), [
      [
        "CPU load",
        cpuPct == null ? "—" : `${cpuPct.toFixed(0)}%`,
        cpuPct,
      ],
      [
        "RAM used",
        m.mem_used_pct == null ? "—" : `${m.mem_used_pct}%`,
        m.mem_used_pct,
      ],
      [
        "Swap used",
        m.swap_pct == null ? "—" : `${m.swap_pct}%`,
        m.swap_pct,
      ],
    ]);
    renderFsBar(
      $("raspi-fs-bar"),
      m.fs_used_pct,
      fmtBytes(m.fs_size_bytes != null && m.fs_avail_bytes != null
        ? m.fs_size_bytes - m.fs_avail_bytes
        : null),
      fmtBytes(m.fs_size_bytes)
    );

    METRIC_LINE_CHARTS.forEach((key) => {
      const series =
        charts[key] ||
        (key === "cpu_pct" ? data.cpu_sparkline_pct : null) ||
        [];
      paintLineChart("chart-" + key, series);
    });

    metricCards($("raspi-container-metrics"), [
      ["Active containers", String(m.active_containers ?? "—")],
    ]);
    paintBarChart("chart-container_cpu", containers.cpu || []);
    paintBarChart("chart-container_mem", containers.mem_mib || []);

    $("raspi-msg").textContent = `node=${data.node} job=${data.job} range=${data.range || range}`;
    showWarnings(data.warnings);
  }

  async function refresh() {
    setStatusBadge("warn", "loading…");
    try {
      if (tab === "metric") {
        await refreshRaspi();
      } else {
        await refreshGateway();
      }
      if (lastWarnings.length) {
        setStatusBadge("warn", "degraded");
      } else {
        setStatusBadge("ok", "live");
      }
    } catch (e) {
      showWarnings([]);
      setStatusBadge("bad", e.message);
    }
  }

  function scheduleAuto() {
    if (timer) clearInterval(timer);
    timer = null;
    if ($("auto-refresh").checked) {
      timer = setInterval(() => refresh().catch(() => {}), 15000);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function wire() {
    buildTabs();
    showTab(tabFromHash(), { syncHash: true });
    paintEmptyMetricCharts();
    window.addEventListener("hashchange", () => {
      showTab(tabFromHash(), { syncHash: false });
      refresh().catch(console.error);
    });
    const originBanner = $("origin-banner");
    if (!onWizardOrigin() && originBanner) {
      originBanner.classList.remove("hidden");
      originBanner.innerHTML =
        `Monitoring must be opened from the app shell so <code>/api/v1</code> is proxied ` +
        `(with optional <code>IOTGW_API_TOKEN</code>). Use ` +
        `<a class="link" href="${WIZARD_MONITORING}">${WIZARD_MONITORING}</a>.`;
      setStatusBadge("bad", "wrong origin");
    }
    $("btn-refresh").addEventListener("click", () => refresh().catch(console.error));
    $("auto-refresh").addEventListener("change", scheduleAuto);
    $("range").addEventListener("change", () => refresh().catch(console.error));
    $("gateway-id").addEventListener("change", () => {
      if (tab === "activities") refresh().catch(console.error);
    });
    $("mon-tabs").addEventListener("click", () => refresh().catch(console.error));
    if (!onWizardOrigin()) return;
    loadGateways()
      .catch(() => {})
      .finally(() => {
        refresh().catch(console.error);
        scheduleAuto();
      });
  }

  wire();
})();
