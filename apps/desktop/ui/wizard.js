/* First-run installer — Monitoring + Network only. Gateways register after install. */
(function () {
  const STEPS = ["Monitoring", "Network", "Finish"];
  let step = 0;

  const $ = (id) => document.getElementById(id);

  async function api(path, opts) {
    let res;
    try {
      res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
        ...opts,
      });
    } catch (e) {
      throw new Error(
        "Cannot reach the local app server (Failed to fetch). " +
          "Close this window and start IoT Gateway Monitor again."
      );
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  function showStep(n) {
    step = Math.max(0, Math.min(STEPS.length - 1, n));
    document.querySelectorAll(".step").forEach((el) => {
      el.classList.toggle("hidden", Number(el.dataset.step) !== step);
    });
    document.querySelectorAll("#step-tabs button").forEach((btn, i) => {
      btn.classList.toggle("active", i === step);
    });
    $("btn-prev").disabled = step === 0;
    $("btn-next").textContent = step === STEPS.length - 1 ? "Done" : "Next";
  }

  function buildTabs() {
    const nav = $("step-tabs");
    nav.innerHTML = "";
    STEPS.forEach((label, i) => {
      const b = document.createElement("button");
      b.textContent = `${i + 1}. ${label}`;
      b.addEventListener("click", () => showStep(i));
      nav.appendChild(b);
    });
  }

  function badge(el, ok, text) {
    el.innerHTML = `<span class="badge ${ok ? "ok" : "bad"}">${text}</span>`;
  }

  function setTelemetryReadyUi(ready, detail) {
    badge($("telemetry-badge"), !!ready, ready ? "Ready" : "Not installed");
    $("telemetry-hint").textContent =
      detail ||
      (ready
        ? "Loki and Prometheus are running."
        : "Click Install monitoring to continue.");
    $("hdr-status").innerHTML = ready
      ? '<span class="badge ok">Ready</span>'
      : '<span class="badge warn">Setup</span>';
    const early = $("btn-open-mon-early");
    if (early) early.disabled = !ready;
  }

  async function refreshEnv() {
    const data = await api("/api/wizard/env");
    if (data.setup_complete) {
      window.location.replace("monitoring.html#metric");
      return;
    }
    const t = data.telemetry || {};
    const job = t.job || {};
    if (job.state === "running") {
      badge($("telemetry-badge"), false, "Installing…");
      $("telemetry-hint").textContent =
        t.progress || job.detail || "Installing…";
      $("hdr-status").innerHTML = '<span class="badge warn">Setup</span>';
      return;
    }
    setTelemetryReadyUi(
      !!t.ready,
      t.ready
        ? t.detail || "Loki and Prometheus are running."
        : t.progress || t.detail || "Click Install monitoring to continue."
    );
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function prepareTelemetry() {
    $("telemetry-hint").textContent = "Installing…";
    badge($("telemetry-badge"), false, "Installing…");
    const early = $("btn-open-mon-early");
    if (early) early.disabled = true;
    try {
      await api("/api/wizard/telemetry/ensure", {
        method: "POST",
        body: JSON.stringify({ background: true }),
      });
      const deadline = Date.now() + 15 * 60 * 1000;
      let last = null;
      while (Date.now() < deadline) {
        last = await api("/api/wizard/telemetry/status");
        const job = last.job || {};
        $("telemetry-hint").textContent =
          last.progress || job.detail || "Installing…";
        if (job.state === "ok") {
          setTelemetryReadyUi(
            true,
            last.detail || job.detail || "Loki and Prometheus are running."
          );
          return;
        }
        if (job.state === "error") {
          throw new Error(job.detail || last.detail || "Install failed");
        }
        await sleep(900);
      }
      throw new Error(
        (last && (last.detail || (last.job && last.job.detail))) ||
          "Install timed out while downloading or starting monitoring."
      );
    } catch (e) {
      badge($("telemetry-badge"), false, "Failed");
      $("telemetry-hint").textContent = String(e.message || e);
      if (early) early.disabled = true;
    }
  }

  async function markSetupComplete() {
    if (typeof window.iotgwMarkSetupComplete === "function") {
      await window.iotgwMarkSetupComplete();
      return;
    }
    try {
      await api("/api/wizard/setup/complete", {
        method: "POST",
        body: "{}",
      });
    } catch (_) {}
  }

  async function enterApp(path) {
    await markSetupComplete();
    window.location.href = path;
  }

  async function startServices() {
    $("svc-msg").textContent = "Starting…";
    const data = await api("/api/wizard/services/start", {
      method: "POST",
      body: JSON.stringify({ streamlit: false }),
    });
    const ok = data.control && data.control.healthy;
    $("svc-msg").innerHTML = ok
      ? `<div class="okmsg">Control service running.</div>`
      : `<div class="err">Control service failed to start.</div>`;
  }

  async function stopServices() {
    await api("/api/wizard/services/stop", { method: "POST", body: "{}" });
    $("svc-msg").innerHTML = `<div class="okmsg">Stopped.</div>`;
  }

  async function openMonitoring() {
    const hint = $("telemetry-hint");
    const prev = hint ? hint.textContent : "";
    if (hint) hint.textContent = "Starting control service…";
    try {
      await startServices();
    } catch (_) {}
    if (hint && prev) hint.textContent = prev;
    await enterApp("monitoring.html#metric");
  }

  async function stopTelemetry() {
    await api("/api/wizard/telemetry/stop", { method: "POST", body: "{}" });
    await refreshEnv();
  }

  async function refreshNics() {
    const data = await api("/api/wizard/nics");
    const sel = $("nic-select");
    sel.innerHTML = "";
    (data.candidates || []).forEach((c) => {
      const opt = document.createElement("option");
      opt.value = JSON.stringify(c);
      opt.textContent = `${c.name} — ${c.ip}${c.prefix != null ? "/" + c.prefix : ""}`;
      sel.appendChild(opt);
    });
    if (data.default) {
      const want = JSON.stringify(data.default);
      [...sel.options].forEach((o) => {
        if (o.value === want) o.selected = true;
      });
    }
    if (!sel.options.length) {
      const opt = document.createElement("option");
      opt.textContent = "(none found)";
      opt.value = "";
      sel.appendChild(opt);
    }
  }

  async function saveNic() {
    const raw = $("nic-select").value;
    if (!raw) throw new Error("Select a LAN address");
    const c = JSON.parse(raw);
    await api("/api/wizard/nics/select", {
      method: "POST",
      body: JSON.stringify({
        monitoring_ip: c.ip,
        nic_name: c.name,
      }),
    });
    $("nic-msg").innerHTML = `<div class="okmsg">Saved ${c.ip}</div>`;
  }

  function showErr(e) {
    console.error(e);
    alert(e.message || String(e));
  }

  function wire() {
    buildTabs();
    showStep(0);
    $("btn-prev").addEventListener("click", () => showStep(step - 1));
    $("btn-next").addEventListener("click", () => {
      if (step === STEPS.length - 1) {
        openMonitoring().catch((e) => {
          showErr(e);
          enterApp("monitoring.html#metric").catch(() => {
            window.location.href = "monitoring.html#metric";
          });
        });
        return;
      }
      showStep(step + 1);
    });
    $("btn-prepare-telemetry").addEventListener("click", () =>
      prepareTelemetry().catch(showErr)
    );
    $("btn-stop-telemetry").addEventListener("click", () =>
      stopTelemetry().catch(showErr)
    );
    const early = $("btn-open-mon-early");
    if (early) {
      early.addEventListener("click", () => openMonitoring().catch(showErr));
    }
    $("btn-refresh-nics").addEventListener("click", () =>
      refreshNics().catch(showErr)
    );
    $("btn-save-nic").addEventListener("click", () => saveNic().catch(showErr));
    $("btn-start-svc").addEventListener("click", () =>
      startServices().catch(showErr)
    );
    $("btn-stop-svc").addEventListener("click", () =>
      stopServices().catch(showErr)
    );
    $("btn-open-mon").addEventListener("click", () =>
      openMonitoring().catch(showErr)
    );
    const openGw = $("btn-open-gateways");
    if (openGw) {
      openGw.addEventListener("click", () => {
        enterApp("gateways.html#registration").catch(() => {
          window.location.href = "gateways.html#registration";
        });
      });
    }
    refreshEnv().catch(showErr);
    refreshNics().catch(() => {});
  }

  wire();
})();
