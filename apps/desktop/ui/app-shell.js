/* Shared left-rail navigation for the post-install app shell. */
(function () {
  const SECTIONS = [
    {
      id: "monitoring",
      label: "Monitoring",
      items: [
        {
          id: "metric",
          label: "Gateway Metric",
          href: "monitoring.html#metric",
          page: "monitoring",
          hash: "metric",
        },
        {
          id: "activities",
          label: "Gateway Activities",
          href: "monitoring.html#activities",
          page: "monitoring",
          hash: "activities",
        },
        {
          id: "control",
          label: "Control Plane",
          href: "actions.html",
          page: "actions",
        },
      ],
    },
    {
      id: "gateways",
      label: "Gateways",
      items: [
        {
          id: "registration",
          label: "Registration",
          href: "gateways.html#registration",
          page: "gateways",
          hash: "registration",
        },
        {
          id: "gw-settings",
          label: "Settings",
          href: "gateways.html#settings",
          page: "gateways",
          hash: "settings",
        },
        {
          id: "cleanup",
          label: "Clean up",
          href: "gateways.html#cleanup",
          page: "gateways",
          hash: "cleanup",
        },
      ],
    },
    {
      id: "settings",
      label: "Settings",
      items: [
        { id: "remove", label: "Remove", href: "settings.html", page: "settings" },
        { id: "admin", label: "Admin", href: "admin.html", page: "admin" },
      ],
    },
  ];

  function currentPage() {
    const body = document.body;
    if (body && body.dataset.page) return body.dataset.page;
    const name = (location.pathname.split("/").pop() || "").toLowerCase();
    if (name.indexOf("wizard") === 0) return "wizard";
    if (name.indexOf("monitoring") === 0) return "monitoring";
    if (name.indexOf("actions") === 0) return "actions";
    if (name.indexOf("gateways") === 0) return "gateways";
    if (name.indexOf("settings") === 0) return "settings";
    if (name.indexOf("admin") === 0) return "admin";
    return "";
  }

  function currentHash() {
    return (location.hash || "").replace(/^#/, "").toLowerCase();
  }

  function isActive(item, page, hash) {
    if (item.page !== page) return false;
    if (page === "monitoring" || page === "gateways") {
      const want = item.hash || "";
      const have = hash || (page === "monitoring" ? "metric" : "registration");
      return want === have;
    }
    return true;
  }

  function renderSidebar(mount) {
    const page = currentPage();
    const hash = currentHash();
    const brand = document.createElement("div");
    brand.className = "app-brand";
    brand.innerHTML =
      "<strong>IoT Gateway</strong><span>Monitor</span>";
    mount.appendChild(brand);

    SECTIONS.forEach((section) => {
      const sec = document.createElement("div");
      sec.className = "app-nav-section";
      const title = document.createElement("div");
      title.className = "app-nav-section-label";
      title.textContent = section.label;
      sec.appendChild(title);

      const list = document.createElement("nav");
      list.className = "app-nav-list";
      list.setAttribute("aria-label", section.label);

      section.items.forEach((item) => {
        const a = document.createElement("a");
        a.className = "app-nav-item";
        a.href = item.href;
        a.dataset.navId = item.id;
        a.textContent = item.label;
        if (isActive(item, page, hash)) {
          a.classList.add("active");
          a.setAttribute("aria-current", "page");
        }
        list.appendChild(a);
      });

      sec.appendChild(list);
      mount.appendChild(sec);
    });
  }

  function ensureFrame(sidebar) {
    const body = document.body;
    let frame = document.querySelector(".app-frame");
    if (frame) return frame;
    frame = document.createElement("div");
    frame.className = "app-frame";
    const nodes = [];
    Array.from(body.children).forEach((child) => {
      if (child === sidebar || child.id === "app-sidebar") return;
      if (child.tagName === "SCRIPT") return;
      nodes.push(child);
    });
    nodes.forEach((n) => frame.appendChild(n));
    body.insertBefore(frame, sidebar.nextSibling);
    return frame;
  }

  function mountShell() {
    const body = document.body;
    if (!body || body.classList.contains("first-run")) return;

    body.classList.add("has-app-shell");

    let sidebar = document.getElementById("app-sidebar");
    if (!sidebar) {
      sidebar = document.createElement("aside");
      sidebar.id = "app-sidebar";
      sidebar.className = "app-sidebar";
      sidebar.setAttribute("aria-label", "Application");
      body.insertBefore(sidebar, body.firstChild);
    } else {
      sidebar.innerHTML = "";
    }
    renderSidebar(sidebar);
    ensureFrame(sidebar);
  }

  function unmountShell() {
    const body = document.body;
    if (!body) return;
    body.classList.add("first-run");
    body.classList.remove("has-app-shell");
    const sidebar = document.getElementById("app-sidebar");
    if (sidebar) sidebar.remove();
    const frame = document.querySelector(".app-frame");
    if (frame) {
      while (frame.firstChild) {
        body.insertBefore(frame.firstChild, frame);
      }
      frame.remove();
    }
  }

  function refreshActiveNav() {
    const sidebar = document.getElementById("app-sidebar");
    if (!sidebar || document.body.classList.contains("first-run")) return;
    sidebar.innerHTML = "";
    renderSidebar(sidebar);
  }

  /** Mark setup finished so the next launch skips the installer. */
  window.iotgwMarkSetupComplete = async function iotgwMarkSetupComplete() {
    try {
      await fetch("/api/wizard/setup/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_) {
      /* best-effort; telemetry.ready still routes returning users */
    }
  };

  window.iotgwMountShell = mountShell;
  window.iotgwUnmountShell = unmountShell;
  window.iotgwRefreshNav = refreshActiveNav;
  window.iotgwNavSections = SECTIONS;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountShell);
  } else {
    mountShell();
  }

  window.addEventListener("hashchange", refreshActiveNav);
})();
