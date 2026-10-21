/* Security configuration, alert review, and report downloads. */
(function () {
  const $ = (id) => document.getElementById(id);
  const REVIEW_KEY = "iotgw.ids.reviewed.v1";
  let gateways = [];
  let alerts = [];
  let selectedGateway = "";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function api(path, options) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data.error || response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function reviewed() {
    try {
      const stored = JSON.parse(localStorage.getItem(REVIEW_KEY) || "{}");
      return stored && typeof stored === "object" ? stored : {};
    } catch (_) {
      return {};
    }
  }

  function setStatus(kind, message) {
    $("security-status").innerHTML =
      `<span class="badge ${kind}">${escapeHtml(message)}</span>`;
  }

  function showMessage(message, error) {
    const el = $("security-message");
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
    el.style.borderLeftColor = error ? "var(--bad)" : "var(--warn)";
  }

  function formatTime(value) {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? String(value || "—") : new Date(parsed).toLocaleString();
  }

  function visibleAlerts() {
    const severity = $("severity-filter").value;
    const text = $("alert-search").value.trim().toLowerCase();
    const hideReviewed = $("hide-reviewed").checked;
    const seen = reviewed();
    return alerts.filter((alert) => {
      if (severity && String(alert.severity || "").toLowerCase() !== severity) return false;
      if (hideReviewed && seen[alert.id]) return false;
      const haystack = [
        alert.device_id, alert.detection, alert.suspicion, alert.suspicion_category,
        alert.message, alert.source_ip, alert.alert_type,
      ].join(" ").toLowerCase();
      return !text || haystack.includes(text);
    });
  }

  function renderStats() {
    const tally = { total: alerts.length, critical: 0, warning: 0, reviewed: 0 };
    const seen = reviewed();
    alerts.forEach((a) => {
      const severity = String(a.severity || "").toLowerCase();
      if (severity in tally && severity !== "total") tally[severity] += 1;
      if (seen[a.id]) tally.reviewed += 1;
    });
    $("alert-stats").innerHTML = [
      ["Alerts", tally.total],
      ["Critical", tally.critical],
      ["Warning", tally.warning],
      ["Reviewed", tally.reviewed],
    ].map(([label, value]) =>
      `<div class="security-stat"><strong>${value}</strong><span>${label}</span></div>`
    ).join("");
  }

  function renderAlerts() {
    renderStats();
    const seen = reviewed();
    const rows = visibleAlerts();
    $("security-alert-rows").innerHTML = rows.map((alert) => {
      const isReviewed = Boolean(seen[alert.id]);
      const severity = String(alert.severity || "info").toLowerCase();
      return `<tr class="${isReviewed ? "reviewed-row" : ""}">
        <td>${escapeHtml(formatTime(alert.occurred_at))}</td>
        <td class="severity-${escapeHtml(severity)}">${escapeHtml(severity)}</td>
        <td>${escapeHtml(alert.device_id || "—")}</td>
        <td>${escapeHtml(alert.suspicion || alert.detection || "Detection")}</td>
        <td class="alert-detail">${escapeHtml(alert.message || alert.suspicion_category || "")}</td>
        <td>${escapeHtml(alert.score == null ? "—" : Number(alert.score).toFixed(3))}</td>
        <td><button class="secondary review-button" data-review-id="${escapeHtml(alert.id)}">${isReviewed ? "Reviewed" : "Mark reviewed"}</button></td>
      </tr>`;
    }).join("");
    $("alert-empty").classList.toggle("hidden", rows.length !== 0);
  }

  async function loadGateways() {
    gateways = await api("/api/v1/gateways");
    const select = $("gateway-id");
    select.replaceChildren();
    gateways.forEach((gateway) => {
      const option = document.createElement("option");
      option.value = gateway.id;
      option.textContent = `${gateway.id} (${gateway.host})`;
      select.appendChild(option);
    });
    if (!gateways.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No registered gateways";
      select.appendChild(option);
      showMessage("Register and provision a gateway to configure IDS and view gateway alerts.", false);
      return;
    }
    selectedGateway = gateways[0].id;
    select.value = selectedGateway;
    showMessage("", false);
  }

  async function loadAlerts() {
    if (!selectedGateway) return;
    setStatus("warn", "Loading alerts…");
    const params = new URLSearchParams({
      range: $("alert-range").value,
      limit: "2000",
      gateway_id: selectedGateway,
    });
    const result = await api(`/api/v1/ids/alerts?${params}`);
    alerts = result.alerts || [];
    renderAlerts();
    setStatus("ok", `${result.count} alert${result.count === 1 ? "" : "s"}`);
    showMessage("", false);
  }

  async function loadConfig() {
    if (!selectedGateway) return;
    const config = await api(
      `/api/v1/gateways/${encodeURIComponent(selectedGateway)}/ids/config`
    );
    $("ids-enabled").checked = Boolean(config.enabled);
    $("rules-enabled").checked = Boolean(config.rules_enabled);
    $("ml-enabled").checked = Boolean(config.ml_enabled);
    $("ml-threshold").value = config.threshold;
    $("rate-threshold").value = config.max_message_rate_per_sec;
    $("clock-threshold").value = config.max_clock_skew_seconds;
    $("connect-ratio").value = config.max_connect_publish_ratio;
    $("cooldown").value = config.alert_cooldown_seconds;
    $("config-status").textContent = "";
  }

  async function changeGateway() {
    selectedGateway = $("gateway-id").value;
    $("config-status").textContent = "";
    try {
      await Promise.all([loadAlerts(), loadConfig()]);
    } catch (error) {
      setStatus("bad", "Gateway unavailable");
      showMessage(error.message, true);
    }
  }

  async function saveConfig() {
    const password = $("sudo-password").value;
    if (!password) {
      $("config-status").textContent = "Enter the gateway sudo password.";
      return;
    }
    $("btn-save-ids").disabled = true;
    $("config-status").textContent = "Saving and restarting protection…";
    try {
      const payload = {
        enabled: $("ids-enabled").checked,
        rules_enabled: $("rules-enabled").checked,
        ml_enabled: $("ml-enabled").checked,
        threshold: Number($("ml-threshold").value),
        max_message_rate_per_sec: Number($("rate-threshold").value),
        max_clock_skew_seconds: Number($("clock-threshold").value),
        max_connect_publish_ratio: Number($("connect-ratio").value),
        alert_cooldown_seconds: Number($("cooldown").value),
        sudo_password: password,
      };
      const updated = await api(
        `/api/v1/gateways/${encodeURIComponent(selectedGateway)}/ids/config`,
        { method: "POST", body: JSON.stringify(payload) }
      );
      $("sudo-password").value = "";
      $("config-status").textContent = updated.ok
        ? "Settings saved. Protection service restarted."
        : "Settings were not applied.";
      setStatus(updated.ok ? "ok" : "bad", updated.ok ? "Protection updated" : "Update failed");
    } catch (error) {
      $("config-status").textContent = error.message;
      setStatus("bad", "Update failed");
    } finally {
      $("btn-save-ids").disabled = false;
    }
  }

  function download(filename, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function reportName(extension) {
    const safeGateway = (selectedGateway || "all-gateways").replace(/[^a-z0-9_-]/gi, "_");
    return `iot-security-report-${safeGateway}-${new Date().toISOString().slice(0, 10)}.${extension}`;
  }

  function exportJSON() {
    download(reportName("json"), JSON.stringify({
      title: "IoT SecureGate Security Report",
      gateway_id: selectedGateway,
      range: $("alert-range").value,
      generated_at: new Date().toISOString(),
      alert_count: alerts.length,
      alerts,
    }, null, 2), "application/json");
  }

  function exportCSV() {
    const columns = ["occurred_at", "severity", "device_id", "source_ip", "alert_type",
      "detection", "suspicion", "score", "score_type", "message"];
    const quote = (value) => `"${String(value == null ? "" : value).replace(/"/g, '""')}"`;
    const lines = [columns.map(quote).join(",")];
    alerts.forEach((alert) => lines.push(columns.map((key) =>
      quote(alert[key] == null && key === "occurred_at" ? alert["@timestamp"] : alert[key])
    ).join(",")));
    download(reportName("csv"), `\ufeff${lines.join("\r\n")}`, "text/csv;charset=utf-8");
  }

  function exportHTML() {
    const counts = alerts.reduce((result, alert) => {
      const key = String(alert.severity || "info").toLowerCase();
      result[key] = (result[key] || 0) + 1;
      return result;
    }, {});
    const rows = alerts.map((alert) => `<tr>
      <td>${escapeHtml(formatTime(alert.occurred_at))}</td>
      <td>${escapeHtml(alert.severity || "info")}</td>
      <td>${escapeHtml(alert.device_id || "—")}</td>
      <td>${escapeHtml(alert.suspicion || alert.detection || "Detection")}</td>
      <td>${escapeHtml(alert.message || "")}</td>
      <td>${escapeHtml(alert.score == null ? "—" : Number(alert.score).toFixed(3))}</td>
    </tr>`).join("");
    const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>IoT SecureGate Security Report</title>
      <style>body{font:14px system-ui,sans-serif;margin:2rem;color:#17202a}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:.5rem;border-bottom:1px solid #ccd2d8;vertical-align:top}th{background:#edf1f5}.summary{display:flex;gap:2rem;margin:1rem 0}.muted{color:#52606d}@media print{body{margin:1cm}}</style>
      <h1>IoT SecureGate Security Report</h1><p class="muted">Gateway: ${escapeHtml(selectedGateway || "All")} · Range: ${escapeHtml($("alert-range").value)} · Generated: ${escapeHtml(new Date().toLocaleString())}</p>
      <div class="summary"><span>Total alerts: <b>${alerts.length}</b></span><span>Critical: <b>${counts.critical || 0}</b></span><span>Warning: <b>${counts.warning || 0}</b></span></div>
      <table><thead><tr><th>Time</th><th>Severity</th><th>Device</th><th>Detection</th><th>Details</th><th>Score</th></tr></thead><tbody>${rows || "<tr><td colspan='6'>No alerts in this period.</td></tr>"}</tbody></table></html>`;
    download(reportName("html"), html, "text/html;charset=utf-8");
  }

  function setTabFromHash() {
    const tab = location.hash === "#config" ? "config" : "alerts";
    $("alerts-section").classList.toggle("hidden", tab !== "alerts");
    $("config-section").classList.toggle("hidden", tab !== "config");
  }

  function wire() {
    window.addEventListener("hashchange", setTabFromHash);
    setTabFromHash();
    $("gateway-id").addEventListener("change", () => changeGateway().catch(console.error));
    $("alert-range").addEventListener("change", () => loadAlerts().catch((e) => showMessage(e.message, true)));
    $("btn-alert-refresh").addEventListener("click", () => loadAlerts().catch((e) => showMessage(e.message, true)));
    $("severity-filter").addEventListener("change", renderAlerts);
    $("alert-search").addEventListener("input", renderAlerts);
    $("hide-reviewed").addEventListener("change", renderAlerts);
    $("security-alert-rows").addEventListener("click", (event) => {
      const button = event.target.closest("[data-review-id]");
      if (!button) return;
      const seen = reviewed();
      const id = button.dataset.reviewId;
      if (seen[id]) delete seen[id];
      else seen[id] = new Date().toISOString();
      localStorage.setItem(REVIEW_KEY, JSON.stringify(seen));
      renderAlerts();
    });
    $("btn-save-ids").addEventListener("click", () => saveConfig());
    $("btn-report-html").addEventListener("click", exportHTML);
    $("btn-report-csv").addEventListener("click", exportCSV);
    $("btn-report-json").addEventListener("click", exportJSON);
    loadGateways()
      .then(() => Promise.all([loadAlerts(), loadConfig()]))
      .catch((error) => {
        setStatus("bad", "Unavailable");
        showMessage(error.message, true);
      });
  }

  wire();
})();
