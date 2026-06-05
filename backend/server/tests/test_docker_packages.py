"""Unit tests for the DockerManager package/sudo/hardening helpers.

These are pure functions / static methods that build env, volumes, and
hardening kwargs without ever touching a real Docker daemon.
"""

from server.docker_manager import _PROOT_SCRIPT_HOST_PATH, DockerManager, _split_packages

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
    assert volumes[_PROOT_SCRIPT_HOST_PATH] == {
        "bind": "/custom-cont-init.d/98-install-proot-apps.sh",
        "mode": "ro",
    }


def test_apply_proot_apps_noop_when_empty():
    env: dict = {}
    volumes: dict = {}
    DockerManager._apply_proot_apps(env, volumes, None)
    assert env == {}
    assert volumes == {}
