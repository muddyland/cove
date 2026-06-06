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
