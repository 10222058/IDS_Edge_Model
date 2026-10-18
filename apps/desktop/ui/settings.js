/* Settings → Remove — uninstall the monitoring panel (cascades all gateways). */
(function () {
  const $ = (id) => document.getElementById(id);

  let targets = {
    gateways: [],
    provisioned: false,
    default_gateway_id: "gateway",
  };

  async function api(path, opts) {
    let res;
    try {
      res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
        ...opts,
      });
    } catch (e) {
      throw new Error(
        "Cannot reach the local app server. Close this window and start IoT Gateway Monitor again."
      );
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function updateSummary() {
    const list = targets.gateways || [];
    const item = $("rm-gateways-item");
    if (list.length) {
      item.textContent =
        "Registered gateways: " +
        list.map((g) => (g.host ? `${g.id} @ ${g.host}` : g.id)).join(", ");
      item.hidden = false;
    } else if (targets.provisioned) {
      item.textContent =
        "Provisioned gateway agent" +
        (targets.ssh_host ? ` (${targets.ssh_host})` : "");
      item.hidden = false;
    } else {
      item.hidden = true;
    }
    $("sudo-wrap").classList.toggle(
      "hidden",
      !(list.length > 0 || targets.provisioned)
    );
  }

  async function refreshTargets() {
    targets = await api("/api/wizard/uninstall/targets");
    updateSummary();
    $("hdr-status").innerHTML = '<span class="badge ok">Ready</span>';
  }

  async function confirmRemove() {
    $("rm-msg").textContent = "Removing…";
    $("btn-rm-confirm").disabled = true;
    try {
      const data = await api("/api/wizard/uninstall", {
        method: "POST",
        body: JSON.stringify({
          gateway: true,
          monitoring: true,
          sudo_password: $("rm-sudo").value || undefined,
        }),
      });
      const notes = (data.notes || []).join(" · ");
      $("rm-msg").innerHTML = `<div class="okmsg">${escapeHtml(
        data.ok === false
          ? "Finished with some errors. " + notes
          : "Monitoring panel removed. " + notes
      )}</div>`;
      $("rm-sudo").value = "";
      if (typeof window.iotgwUnmountShell === "function") {
        window.iotgwUnmountShell();
      }
      setTimeout(() => {
        window.location.href = "wizard.html";
      }, 900);
    } catch (e) {
      $("rm-msg").innerHTML = `<div class="err">${escapeHtml(e.message || e)}</div>`;
      $("btn-rm-confirm").disabled = false;
    }
  }

  function wire() {
    $("btn-rm-confirm").addEventListener("click", () =>
      confirmRemove().catch((e) => {
        $("rm-msg").innerHTML = `<div class="err">${escapeHtml(e.message || e)}</div>`;
        $("btn-rm-confirm").disabled = false;
      })
    );
    refreshTargets().catch((e) => {
      $("hdr-status").innerHTML = `<span class="badge bad">${escapeHtml(e.message || e)}</span>`;
      $("rm-msg").innerHTML = `<div class="err">${escapeHtml(e.message || e)}</div>`;
    });
  }

  wire();
})();
