"""Unit tests for the DockerManager package/sudo/hardening helpers.

These are pure functions / static methods that build env, volumes, and
hardening kwargs without ever touching a real Docker daemon.
"""

from types import SimpleNamespace

from server.docker_manager import (
    DockerManager,
    _build_browser_cli,
    _dns_list,
    _helper_script_path,
    _parse_stats,
    _split_packages,
)

# ── _dns_list (custom DNS resolution) ──────────────────────────────────────────

def test_dns_list_disabled_returns_none():
    assert _dns_list(SimpleNamespace(custom_dns=False, dns_servers="1.1.1.1")) is None


def test_dns_list_enabled_empty_uses_public_defaults():
    assert _dns_list(SimpleNamespace(custom_dns=True, dns_servers=None)) == ["1.1.1.1", "9.9.9.9"]
    assert _dns_list(SimpleNamespace(custom_dns=True, dns_servers="  ")) == ["1.1.1.1", "9.9.9.9"]


def test_dns_list_parses_custom_servers():
    assert _dns_list(SimpleNamespace(custom_dns=True, dns_servers="8.8.8.8, 9.9.9.9")) == [
        "8.8.8.8",
        "9.9.9.9",
    ]

# ── _parse_stats (docker stats reduction) ──────────────────────────────────────

def test_parse_stats_computes_cpu_and_mem():
    raw = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2_000},
            "system_cpu_usage": 20_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000},
            "system_cpu_usage": 10_000,
        },
        "memory_stats": {
            "usage": 600 * 1024 * 1024,
            "limit": 1024 * 1024 * 1024,
            "stats": {"inactive_file": 100 * 1024 * 1024},
        },
    }
    out = _parse_stats(raw)
    # cpu_delta/system_delta = 1000/10000 = 0.1, * 4 cpus * 100 = 40%
    assert out["cpu_pct"] == 40.0
    # 600MB usage - 100MB cache = 500MB used
    assert out["mem_used"] == 500 * 1024 * 1024
    assert out["mem_limit"] == 1024 * 1024 * 1024
    assert out["mem_pct"] == round(500 / 1024 * 100, 1)


def test_parse_stats_handles_zero_system_delta():
    raw = {
        "cpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 100},
        "precpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 100},
        "memory_stats": {"usage": 1024, "limit": 2048, "stats": {}},
    }
    out = _parse_stats(raw)
    assert out["cpu_pct"] == 0.0
    assert out["mem_used"] == 1024


def test_parse_stats_returns_none_on_incomplete():
    assert _parse_stats({}) is None
    assert _parse_stats({"cpu_stats": {}}) is None

# ── get_tailscale_ip (exec into the sidecar) ───────────────────────────────────

def _dm_with_sidecar(*, status="running", code=0, out=b""):
    """A DockerManager whose client returns a fake sidecar container."""
    sidecar = SimpleNamespace(status=status, exec_run=lambda cmd: (code, out))
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: sidecar))
    dm = DockerManager.__new__(DockerManager)
    dm._client = client
    return dm


def test_tailscale_ip_returns_address():
    dm = _dm_with_sidecar(out=b"100.101.102.103\n")
    assert dm.get_tailscale_ip(7) == "100.101.102.103"


def test_tailscale_ip_takes_first_line_only():
    dm = _dm_with_sidecar(out=b"100.64.0.1\nfd7a:115c::1\n")
    assert dm.get_tailscale_ip(7) == "100.64.0.1"


def test_tailscale_ip_none_when_not_running():
    assert _dm_with_sidecar(status="created").get_tailscale_ip(7) is None


def test_tailscale_ip_none_on_nonzero_or_empty():
    assert _dm_with_sidecar(code=1, out=b"no ip").get_tailscale_ip(7) is None
    assert _dm_with_sidecar(out=b"  \n").get_tailscale_ip(7) is None

# ── _build_browser_cli (kiosk / dark-mode flags) ───────────────────────────────

def _ws(**kw):
    base = dict(target_url="https://x.io", kiosk=False, kiosk_dark=False, kiosk_menu=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_browser_cli_plain_url():
    assert _build_browser_cli(_ws()) == "https://x.io"


def test_browser_cli_kiosk_locked():
    assert _build_browser_cli(_ws(kiosk=True)) == "--kiosk https://x.io"


def test_browser_cli_kiosk_menu_uses_fullscreen():
    # The right-click/refresh menu needs functional full-screen, not locked kiosk.
    assert _build_browser_cli(_ws(kiosk=True, kiosk_menu=True)) == "--start-fullscreen https://x.io"


def test_browser_cli_kiosk_dark_mode():
    assert _build_browser_cli(_ws(kiosk=True, kiosk_dark=True)) == (
        "--kiosk --force-dark-mode --enable-features=WebContentsForceDark https://x.io"
    )

# ── _split_packages ────────────────────────────────────────────────────────────

def test_split_packages_variants():
    assert _split_packages("htop vim curl") == ["htop", "vim", "curl"]
    assert _split_packages("htop,vim, curl") == ["htop", "vim", "curl"]
    assert _split_packages("  htop \n vim ") == ["htop", "vim"]
    assert _split_packages("") == []
    assert _split_packages(None) == []
    assert _split_packages("   ") == []


# ── _build_hardening (no-new-privileges rule) ──────────────────────────────────

def test_hardening_allow_sudo_no_setting_no_flag():
    """allow_sudo=True + admin setting False => sudo works (no flag)."""
    h = DockerManager._build_hardening(no_new_privileges_setting=False, allow_sudo=True)
    assert "security_opt" not in h
    assert h["cap_drop"] == ["ALL"]


def test_hardening_no_sudo_sets_flag():
    """allow_sudo=False => no-new-privileges applied."""
    h = DockerManager._build_hardening(no_new_privileges_setting=False, allow_sudo=False)
    assert h["security_opt"] == ["no-new-privileges:true"]


def test_hardening_admin_setting_forces_flag():
    """admin setting True => flag even when the workspace requests sudo."""
    h = DockerManager._build_hardening(no_new_privileges_setting=True, allow_sudo=True)
    assert h["security_opt"] == ["no-new-privileges:true"]


# ── _apply_package_env (distro packages) ───────────────────────────────────────

def test_apply_package_env_sets_mod_and_packages():
    env: dict = {"TZ": "UTC"}
    DockerManager._apply_package_env(env, "htop, vim curl")
    assert env["DOCKER_MODS"] == "linuxserver/mods:universal-package-install"
    assert env["INSTALL_PACKAGES"] == "htop|vim|curl"


def test_apply_package_env_appends_existing_mod():
    env: dict = {"DOCKER_MODS": "linuxserver/mods:other"}
    DockerManager._apply_package_env(env, "htop")
    assert env["DOCKER_MODS"] == (
        "linuxserver/mods:other|linuxserver/mods:universal-package-install"
    )
    assert env["INSTALL_PACKAGES"] == "htop"


def test_apply_package_env_noop_when_empty():
    env: dict = {}
    DockerManager._apply_package_env(env, "  ")
    assert env == {}
    DockerManager._apply_package_env(env, None)
    assert env == {}


# ── _apply_proot_apps ──────────────────────────────────────────────────────────

def test_apply_proot_apps_sets_env_and_mount():
    env: dict = {}
    volumes: dict = {}
    DockerManager._apply_proot_apps(env, volumes, "firefox, libreoffice")
    assert env["PROOT_APPS"] == "firefox libreoffice"
    # The bind source is the staged (host-resolvable) copy under the storage tree.
    key = _helper_script_path("install-proot-apps.sh")
    assert key.endswith("/.cove-scripts/install-proot-apps.sh")
    assert volumes[key] == {
        "bind": "/custom-cont-init.d/98-install-proot-apps.sh",
        "mode": "ro",
    }


def test_apply_proot_apps_noop_when_empty():
    env: dict = {}
    volumes: dict = {}
    DockerManager._apply_proot_apps(env, volumes, None)
    assert env == {}
    assert volumes == {}

# ── _apply_appimages ───────────────────────────────────────────────────────────

def test_apply_appimages_sets_env_and_mount():
    env: dict = {}
    volumes: dict = {}
    DockerManager._apply_appimages(
        env, volumes, "https://x.io/A.AppImage\nhttps://y.io/B.AppImage"
    )
    assert env["COVE_APPIMAGES"] == "https://x.io/A.AppImage https://y.io/B.AppImage"
    key = _helper_script_path("install-appimages.sh")
    assert key.endswith("/.cove-scripts/install-appimages.sh")
    assert volumes[key] == {
        "bind": "/custom-cont-init.d/97-install-appimages.sh",
        "mode": "ro",
    }


def test_apply_appimages_noop_when_empty():
    env: dict = {}
    volumes: dict = {}
    DockerManager._apply_appimages(env, volumes, None)
    assert env == {}
    assert volumes == {}

# ── _resource_limits (admin CPU/memory caps) ───────────────────────────────────

def test_resource_limits_empty_when_zero(monkeypatch):
    import server.docker_manager as dm
    monkeypatch.setattr(dm, "get_workspace_cpu_limit", lambda _db: 0.0)
    monkeypatch.setattr(dm, "get_workspace_memory_limit_mb", lambda _db: 0)
    assert dm._resource_limits(None) == {}


def test_resource_limits_builds_kwargs(monkeypatch):
    import server.docker_manager as dm
    monkeypatch.setattr(dm, "get_workspace_cpu_limit", lambda _db: 2.5)
    monkeypatch.setattr(dm, "get_workspace_memory_limit_mb", lambda _db: 4096)
    assert dm._resource_limits(None) == {"nano_cpus": 2_500_000_000, "mem_limit": "4096m"}


def test_resource_limits_partial(monkeypatch):
    import server.docker_manager as dm
    monkeypatch.setattr(dm, "get_workspace_cpu_limit", lambda _db: 0.0)
    monkeypatch.setattr(dm, "get_workspace_memory_limit_mb", lambda _db: 512)
    assert dm._resource_limits(None) == {"mem_limit": "512m"}

# ── _build_egress_rules (firewall policy) ──────────────────────────────────────

def test_egress_rules_non_tailscale_blocks_all_internal():
    script = DockerManager._build_egress_rules(tailscale=False, lan_subnets=[])
    # Metadata + docker-internal always dropped.
    assert "-d 169.254.0.0/16 -j DROP" in script
    assert "-d 172.16.0.0/12 -j DROP" in script
    # Remaining private + CGNAT dropped when nothing is granted.
    assert "-d 10.0.0.0/8 -j DROP" in script
    assert "-d 192.168.0.0/16 -j DROP" in script
    assert "-d 100.64.0.0/10 -j DROP" in script
    # No tailscale carve-out for a plain workspace.
    assert "tailscale0" not in script
    # Loopback + embedded DNS + established are accepted.
    assert "-o lo -j ACCEPT" in script
    assert "-d 127.0.0.11 -j ACCEPT" in script


def test_egress_rules_tailscale_allows_tailnet_interface_first():
    script = DockerManager._build_egress_rules(tailscale=True, lan_subnets=[])
    # tailnet interface is accepted, and BEFORE the internal DROP rules so
    # tailnet/subnet-router/exit-node traffic is never dropped.
    assert "-o tailscale0 -j ACCEPT" in script
    assert script.index("tailscale0") < script.index("169.254.0.0/16")
    # Docker-internal still blocked even for tailscale (container isolation).
    assert "-d 172.16.0.0/12 -j DROP" in script


def test_egress_rules_lan_subnets_accepted_before_lan_block():
    script = DockerManager._build_egress_rules(
        tailscale=True, lan_subnets=["10.12.0.0/24"]
    )
    # The granted subnet is ACCEPTed, and that ACCEPT precedes the broad
    # 10.0.0.0/8 DROP so the carve-out wins.
    assert "-d 10.12.0.0/24 -j ACCEPT" in script
    assert script.index("10.12.0.0/24") < script.index("-d 10.0.0.0/8 -j DROP")
    # ...but docker-internal/metadata are dropped BEFORE the LAN accepts, so a
    # granted subnet can never re-open the protected ranges.
    assert script.index("172.16.0.0/12") < script.index("10.12.0.0/24")
