/* Setup Wizard — /api/wizard/* */
(function () {
  const FULL_STEPS = ["Monitoring", "Network", "SSH", "Firewall", "Finish"];
  const MONITOR_STEPS = ["Monitoring", "Network", "Finish"];
  let step = 0;
  let hostKey = null;
  let hostKeyPinned = false;
  let setupPath = "monitoring"; // monitoring | full

  const $ = (id) => document.getElementById(id);

  function steps() {
    return setupPath === "full" ? FULL_STEPS : MONITOR_STEPS;
  }

  function panelForLogical(i) {
    // Map logical step index → data-step on panels.
    if (setupPath === "full") return i;
    // monitoring: 0 Monitoring, 1 Network, 2 Finish(panel 4)
    return [0, 1, 4][i];
  }

  async function api(path, opts) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  function applyPathVisibility() {
    document.querySelectorAll(".gateway-only").forEach((el) => {
      el.classList.toggle("hidden-path", setupPath !== "full");
    });
  }

  function showStep(n) {
    const list = steps();
    step = Math.max(0, Math.min(list.length - 1, n));
    const panelStep = panelForLogical(step);
    document.querySelectorAll(".step").forEach((el) => {
      const ds = Number(el.dataset.step);
      let visible = ds === panelStep;
      if (setupPath !== "full" && el.classList.contains("gateway-only") && ds !== 4) {
        visible = false;
      }
      el.classList.toggle("hidden", !visible);
    });
    document.querySelectorAll("#step-tabs button").forEach((btn, i) => {
      btn.classList.toggle("active", i === step);
    });
    $("btn-prev").disabled = step === 0;
    $("btn-next").textContent = step === list.length - 1 ? "Done" : "Next";
  }

  function buildTabs() {
    const nav = $("step-tabs");
    nav.innerHTML = "";
    steps().forEach((label, i) => {
      const b = document.createElement("button");
      b.textContent = `${i + 1}. ${label}`;
      b.addEventListener("click", () => showStep(i));
      nav.appendChild(b);
    });
  }

  function setSetupPath(path) {
    setupPath = path === "full" ? "full" : "monitoring";
    try {
      localStorage.setItem("iotgw_setup_path", setupPath);
    } catch (_) {}
    applyPathVisibility();
    buildTabs();
    showStep(0);
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
      const gw = ($("gateway-ip") && $("gateway-ip").value) || "";
      // Background job + poll: large Loki/Prometheus downloads must not block
      // a single HTTP request (common cause of "Install failed" on slow links).
      await api("/api/wizard/telemetry/ensure", {
        method: "POST",
        body: JSON.stringify({
          gateway_ip: gw || undefined,
          background: true,
        }),
      });
      const deadline = Date.now() + 15 * 60 * 1000;
      let last = null;
      while (Date.now() < deadline) {
        last = await api("/api/wizard/telemetry/status");
        const job = last.job || {};
        const progress = last.progress || job.detail || "Installing…";
        $("telemetry-hint").textContent = progress;
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
        // Job still running (or idle right after kick-off) — keep polling.
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

  async function openMonitoring() {
    // Monitoring UI needs the control service query API in front of Loki/Prometheus.
    const hint = $("telemetry-hint");
    const prev = hint ? hint.textContent : "";
    if (hint) hint.textContent = "Starting control service…";
    try {
      await startServices();
    } catch (_) {
      /* startServices paints svc-msg on Finish; continue to Monitoring anyway */
    }
    if (hint && prev) hint.textContent = prev;
    window.location.href = "monitoring.html";
  }

  async function stopTelemetry() {
    await api("/api/wizard/telemetry/stop", { method: "POST", body: "{}" });
    await refreshEnv();
  }

  async function refreshNics() {
    const mode = $("net-mode") ? $("net-mode").value : "";
    const q =
      mode === "" || mode == null
        ? ""
        : `?windows_net_mode=${encodeURIComponent(mode)}`;
    const data = await api("/api/wizard/nics" + q);
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
    const body = {
      monitoring_ip: c.ip,
      nic_name: c.name,
      gateway_ip: ($("gateway-ip") && $("gateway-ip").value.trim()) || undefined,
      windows_net_mode: ($("net-mode") && $("net-mode").value) || undefined,
    };
    await api("/api/wizard/nics/select", { method: "POST", body: JSON.stringify(body) });
    $("nic-msg").innerHTML = `<div class="okmsg">Saved ${c.ip}</div>`;
  }

  async function fetchHostKey() {
    hostKeyPinned = false;
    $("btn-install-key").disabled = true;
    hostKey = await api("/api/wizard/ssh/fetch-host-key", {
      method: "POST",
      body: JSON.stringify({
        host: $("ssh-host").value.trim(),
        port: Number($("ssh-port").value) || 22,
      }),
    });
    $("hk-info").textContent = `${hostKey.key_type}  ${hostKey.fingerprint_sha256}`;
    $("btn-pin-hk").disabled = false;
    $("ssh-msg").innerHTML = `<div class="okmsg">Verify fingerprint, then Pin.</div>`;
  }

  async function pinHostKey() {
    if (!hostKey) throw new Error("Fetch the host key first");
    await api("/api/wizard/ssh/pin-host-key", {
      method: "POST",
      body: JSON.stringify(hostKey),
    });
    hostKeyPinned = true;
    $("btn-install-key").disabled = false;
    $("ssh-msg").innerHTML = `<div class="okmsg">Pinned.</div>`;
  }

  async function installKey() {
    if (!hostKeyPinned || !hostKey) throw new Error("Pin the host key first");
    const body = {
      host: $("ssh-host").value.trim(),
      port: Number($("ssh-port").value) || 22,
      username: $("ssh-user").value.trim(),
      password: $("ssh-pass").value,
      gateway_id: $("ssh-gid").value.trim() || "gateway",
      host_key_base64: hostKey.base64,
    };
    await api("/api/wizard/ssh/install-key", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("ssh-pass").value = "";
    $("ssh-msg").innerHTML = `<div class="okmsg">SSH key installed.</div>`;
  }

  async function provisionAgent() {
    const msg = $("provision-msg");
    const sudoPass = ($("sudo-pass") && $("sudo-pass").value) || "";
    if (!sudoPass.trim()) {
      msg.innerHTML = `<div class="err">Sudo password required.</div>`;
      throw new Error("sudo password required");
    }
    msg.innerHTML = `<div class="okmsg">Installing…</div>`;
    try {
      const data = await api("/api/wizard/provision", {
        method: "POST",
        body: JSON.stringify({
          host: $("ssh-host").value.trim() || undefined,
          username: $("ssh-user").value.trim() || undefined,
          port: Number($("ssh-port").value) || undefined,
          gateway_id: $("ssh-gid").value.trim() || undefined,
          gateway_ip: $("gateway-ip").value.trim() || undefined,
          sudo_password: sudoPass,
        }),
      });
      if ($("sudo-pass")) $("sudo-pass").value = "";
      const notes = (data.notes || []).slice(0, 4).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
      msg.innerHTML = `<div class="okmsg">Gateway installed.</div><ul>${notes}</ul>`;
    } catch (e) {
      if ($("sudo-pass")) $("sudo-pass").value = "";
      msg.innerHTML = `<div class="err">${escapeHtml(String(e.message || e))}</div>`;
      throw e;
    }
  }

  async function refreshChecklist() {
    const data = await api("/api/wizard/checklist");
    const wrap = $("checklist-wrap");
    wrap.innerHTML = "";
    const note = $("localhost-note");
    if (note) {
      // Keep firewall guidance short — full WSL essay is not shown.
      note.textContent = "";
      note.classList.add("hidden");
    }
    if (!data.applicable) {
      wrap.innerHTML = `<p>${escapeHtml(data.reason || "Not required on this host.")}</p>`;
      return;
    }
    (data.items || []).forEach((item, idx) => {
      const div = document.createElement("div");
      div.className = "checklist-item";
      div.innerHTML = `<strong>${idx + 1}. ${escapeHtml(item.title)}</strong>
        ${
          item.command
            ? `<div class="row"><code class="mono">${escapeHtml(item.command)}</code>
        <button class="secondary btn-copy" data-cmd="${escapeAttr(item.command)}">Copy</button></div>`
            : ""
        }`;
      wrap.appendChild(div);
    });
    wrap.querySelectorAll(".btn-copy").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(btn.dataset.cmd || "");
          btn.textContent = "Copied";
        } catch (_) {
          btn.textContent = "Failed";
        }
      });
    });
    if (data.confirmed) {
      $("cl-msg").innerHTML = `<div class="okmsg">Confirmed.</div>`;
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  async function confirmChecklist() {
    await api("/api/wizard/checklist/confirm", { method: "POST", body: "{}" });
    $("cl-msg").innerHTML = `<div class="okmsg">Confirmed.</div>`;
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

  function wire() {
    try {
      const saved = localStorage.getItem("iotgw_setup_path");
      if (saved === "full" || saved === "monitoring") setupPath = saved;
    } catch (_) {}
    document.querySelectorAll('input[name="setup-path"]').forEach((el) => {
      el.checked = el.value === setupPath;
      el.addEventListener("change", () => {
        if (el.checked) setSetupPath(el.value);
      });
    });
    applyPathVisibility();
    buildTabs();

    $("btn-prev").addEventListener("click", () => showStep(step - 1));
    $("btn-next").addEventListener("click", () => {
      if (step === steps().length - 1) {
        openMonitoring().catch((e) => {
          showErr(e);
          window.location.href = "monitoring.html";
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
    const earlyMon = $("btn-open-mon-early");
    if (earlyMon) {
      earlyMon.addEventListener("click", () => openMonitoring().catch(showErr));
    }
    $("btn-refresh-nics").addEventListener("click", () => refreshNics().catch(showErr));
    $("btn-save-nic").addEventListener("click", () => saveNic().catch(showErr));
    $("btn-fetch-hk").addEventListener("click", () =>
      fetchHostKey().catch((e) => {
        $("ssh-msg").innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      })
    );
    $("btn-pin-hk").addEventListener("click", () =>
      pinHostKey().catch((e) => {
        $("ssh-msg").innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      })
    );
    $("btn-install-key").addEventListener("click", () =>
      installKey().catch((e) => {
        $("ssh-msg").innerHTML = `<div class="err">${escapeHtml(e.message)}</div>`;
      })
    );
    $("btn-refresh-cl").addEventListener("click", () => refreshChecklist().catch(showErr));
    $("btn-confirm-cl").addEventListener("click", () => confirmChecklist().catch(showErr));
    $("btn-start-svc").addEventListener("click", () => startServices().catch(showErr));
    $("btn-stop-svc").addEventListener("click", () => stopServices().catch(showErr));
    $("btn-provision-agent").addEventListener("click", () =>
      provisionAgent().catch(showErr)
    );
    $("btn-open-mon").addEventListener("click", () =>
      openMonitoring().catch(showErr)
    );
    const openActions = $("btn-open-actions");
    if (openActions) {
      openActions.addEventListener("click", () => {
        window.location.href = "actions.html";
      });
    }
    showStep(0);
    refreshEnv().catch(showErr);
    refreshNics().catch(() => {});
    refreshChecklist().catch(() => {});
  }

  function showErr(e) {
    console.error(e);
    alert(e.message || String(e));
  }

  wire();
})();
