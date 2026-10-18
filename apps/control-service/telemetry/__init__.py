"""OS-aware Loki/Prometheus lifecycle, NIC selection, and Windows firewall checklist (K18)."""

from .checklist import (
    ChecklistItem,
    format_checklist_for_display,
    generate_windows_native_checklist,
    generate_wsl2_nat_checklist,
    refresh_checklist_on_wsl_ip_change,
)
from .compose import (
    ComposeProfile,
    generate_compose_yaml,
    windows_wsl2_notes,
    write_stack_files,
)
from .images import IMAGE_SET, required_images
from .lifecycle import BackendStatus, TelemetryManager
from .native import NativeTelemetryManager, NativeTelemetryStatus, default_native_root
from .nic import (
    NicCandidate,
    filter_nic_candidates,
    list_ipv4_candidates,
    pick_default_monitoring_ip,
)
from .readiness import (
    check_loki,
    check_prometheus,
    gateway_loki_ready_curl,
    wait_ready,
)
from .scrape import render_prometheus_scrape

__all__ = [
    "BackendStatus",
    "ChecklistItem",
    "ComposeProfile",
    "IMAGE_SET",
    "NicCandidate",
    "TelemetryManager",
    "NativeTelemetryManager",
    "NativeTelemetryStatus",
    "default_native_root",
    "check_loki",
    "check_prometheus",
    "filter_nic_candidates",
    "format_checklist_for_display",
    "gateway_loki_ready_curl",
    "generate_compose_yaml",
    "generate_windows_native_checklist",
    "generate_wsl2_nat_checklist",
    "list_ipv4_candidates",
    "pick_default_monitoring_ip",
    "refresh_checklist_on_wsl_ip_change",
    "render_prometheus_scrape",
    "required_images",
    "wait_ready",
    "windows_wsl2_notes",
    "write_stack_files",
]
