"""System info for the stream page: /info, /disk-usage, /public-ip, and the
Docker-side parsing behind them."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

from server.db import SessionLocal
from server.docker_manager import (
    DockerManager,
    _docker_time,
    _parse_ipinfo,
    _parse_stats_detail,
    measure_dir,
)
from server.models import Workspace
from server.tests.helpers import add_image, set_workspace_status, setup_admin

STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 1000, "online_cpus": 8},
    "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 0},
    "memory_stats": {"usage": 600, "limit": 2000, "stats": {"inactive_file": 100}},
    "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 200}, "eth1": {"rx_bytes": 5, "tx_bytes": 1}},
    "blkio_stats": {"io_service_bytes_recursive": [
        {"op": "read", "value": 4096}, {"op": "write", "value": 512}, {"op": "Read", "value": 4},
    ]},
    "pids_stats": {"current": 42, "limit": 512},
}


def test_stats_detail_totals():
    d = _parse_stats_detail(STATS)
    assert d == {"net_rx": 1005, "net_tx": 201, "blk_read": 4100, "blk_write": 512,
                 "pids": 42, "pids_limit": 512, "online_cpus": 8}


def test_stats_detail_missing_and_unlimited():
    d = _parse_stats_detail({"pids_stats": {"current": 3, "limit": 2**64 - 1}})
    assert d["net_rx"] is None and d["blk_read"] is None
    assert d["pids"] == 3 and d["pids_limit"] is None


def test_docker_time():
    t = _docker_time("2026-10-12T01:02:03.123456789Z")
    assert t == datetime(2026, 10, 12, 1, 2, 3, 123456, tzinfo=timezone.utc)
    assert _docker_time("0001-01-01T00:00:00Z") is None
    assert _docker_time(None) is None


def test_parse_ipinfo_keeps_only_known_fields():
    out = _parse_ipinfo(
        '{"ip":"185.65.1.2","city":"Stockholm","country":"SE",'
        '"org":"AS39351 31173 Services","loc":"59,18","readme":"x"}'
    )
    assert out == {"ip": "185.65.1.2", "city": "Stockholm", "country": "SE", "org": "AS39351 31173 Services"}
    assert _parse_ipinfo("<html>") is None
    assert _parse_ipinfo('{"error": "rate limited"}') is None


def test_measure_dir_counts_files_and_skips_symlinks(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f1").write_bytes(b"x" * 100)
    (tmp_path / "f2").write_bytes(b"y" * 10)
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    (outside / "big").write_bytes(b"z" * 10_000)
    os.symlink(outside, tmp_path / "link")
    assert measure_dir(tmp_path) == {"bytes": 110, "files": 2, "partial": False}
    assert measure_dir(tmp_path, budget_s=-1)["partial"] is True


class _Ctr:
    def __init__(self, attrs, stats=None, status="running"):
        self.attrs, self._stats, self.status = attrs, stats, status

    def stats(self, stream=False):
        return self._stats

    def exec_run(self, cmd):
        return self._exec


def _dm(containers):
    import docker

    dm = DockerManager.__new__(DockerManager)

    def get(name):
        if name not in containers:
            raise docker.errors.NotFound("nope")
        return containers[name]

    dm._client = SimpleNamespace(containers=SimpleNamespace(get=get))
    return dm


def test_runtime_info_reads_limits_and_takes_traffic_from_the_sidecar():
    ws_stats = {k: v for k, v in STATS.items() if k != "networks"}  # shares the sidecar's netns
    ctr = _Ctr({
        "Id": "abcdef1234567890", "Name": "/cove-ws-7", "Image": "sha256:0123456789abcdef",
        "RestartCount": 1,
        "State": {"Status": "running", "StartedAt": "2026-10-12T00:00:00.5Z"},
        "HostConfig": {"NanoCpus": 2_000_000_000, "Memory": 4 << 30, "PidsLimit": 512,
                       "SecurityOpt": ["no-new-privileges:true"], "ShmSize": 1 << 30},
    }, ws_stats)
    side = _Ctr({"State": {"Status": "running", "Health": {"Status": "healthy"},
                           "StartedAt": "2026-10-12T00:00:00Z"}},
                {"networks": {"eth0": {"rx_bytes": 7000, "tx_bytes": 300}}})
    dm = _dm({"c7": ctr, "cove-gluetun-7": side})
    ws = SimpleNamespace(id=7, container_id="c7", use_gluetun=True, use_tailscale=False)
    info = dm.runtime_info(ws)
    c = info["container"]
    assert c["id"] == "abcdef123456" and c["name"] == "cove-ws-7" and c["image_id"] == "0123456789ab"
    assert c["cpu_limit"] == 2.0 and c["mem_limit"] == 4 << 30 and c["pids_limit"] == 512
    assert c["no_new_privileges"] is True and c["privileged"] is False and c["restart_count"] == 1
    assert info["stats"]["mem_used"] == 500
    assert info["detail"]["net_rx"] == 7000 and info["detail"]["net_tx"] == 300
    assert info["sidecar"]["health"] == "healthy"


def test_runtime_info_missing_sidecar_and_container():
    ctr = _Ctr({"State": {"Status": "running"}, "HostConfig": {}}, None)
    dm = _dm({"c1": ctr})
    info = dm.runtime_info(SimpleNamespace(id=1, container_id="c1", use_gluetun=False, use_tailscale=True))
    assert info["sidecar"]["state"] == "missing"
    assert info["stats"] is None
    gone = SimpleNamespace(id=1, container_id="gone", use_gluetun=False, use_tailscale=False)
    assert _dm({}).runtime_info(gone)["container"] is None


def test_public_ip_parses_and_handles_failure():
    ctr = _Ctr({}, status="running")
    ctr._exec = (0, b'{"ip":"100.1.2.3","country":"NL"}')
    dm = _dm({"c1": ctr})
    ws = SimpleNamespace(id=1, container_id="c1")
    assert dm.public_ip(ws) == {"ip": "100.1.2.3", "country": "NL"}
    ctr._exec = (6, b"")
    assert dm.public_ip(ws) is None


# ── API ─────────────────────────────────────────────────────────────────────

def _running_ws(client, **extra):
    image_id = add_image(name="Desktop", image_type="desktop")
    ws = client.post("/api/workspaces", json={"name": "box", "image_id": image_id, **extra}).json()
    set_workspace_status(ws["id"], "running")
    return ws


def test_info_combines_config_and_runtime(client, fake_docker_manager):
    setup_admin(client)
    vid = client.post("/api/users/me/vpn-profiles", json={
        "name": "Mullvad SE", "mode": "provider", "provider": "mullvad", "vpn_type": "wireguard",
        "wireguard_private_key": "K", "wireguard_addresses": "10.0.0.2/32", "server_countries": "Sweden",
    }).json()["id"]
    ws = _running_ws(client, use_gluetun=True, gluetun_profile_id=vid)
    db = SessionLocal()
    try:
        w = db.get(Workspace, ws["id"])
        w.started_at = datetime(2026, 10, 12, 0, 0, 0)
        db.commit()
    finally:
        db.close()
    fake_docker_manager.runtime_info.return_value = {
        "container": {"id": "abc", "name": "cove-ws-1", "state": "running", "started_at": None,
                      "restart_count": 0, "image_id": "123", "cpu_limit": 2.0, "mem_limit": 1024,
                      "pids_limit": None, "shm_size": None, "privileged": False, "no_new_privileges": True},
        "stats": {"cpu_pct": 5.0, "mem_used": 10, "mem_limit": 100, "mem_pct": 10.0},
        "detail": {"net_rx": 1, "net_tx": 2, "blk_read": None, "blk_write": None,
                   "pids": 3, "pids_limit": None, "online_cpus": 4},
        "sidecar": {"name": "cove-gluetun-1", "state": "running", "health": "healthy",
                    "started_at": None, "restart_count": 0},
        "bridge_ip": "172.20.0.2",
    }
    resp = client.get(f"/api/workspaces/{ws['id']}/info")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["network"]["mode"] == "gluetun"
    assert body["network"]["profile_name"] == "Mullvad SE"
    assert body["network"]["vpn_provider"] == "Mullvad"
    assert body["network"]["vpn_type"] == "WireGuard"
    assert body["network"]["vpn_servers"] == "Sweden"
    assert body["network"]["sidecar"]["health"] == "healthy"
    assert body["network"]["bridge_ip"] is None  # not meaningful behind a sidecar
    assert body["resources"]["cpu_pct"] == 5.0 and body["resources"]["pids"] == 3
    assert body["container"]["no_new_privileges"] is True
    assert body["auto_stop_at"].startswith("2026-10-13T00:00:00")  # default 24 h limit
    assert body["storage"] == {"kind": "persistent", "measurable": True}
    assert "K" not in resp.text.replace("Kind", "")  # no secrets


def test_info_for_a_stopped_workspace_has_no_runtime(client, fake_docker_manager):
    setup_admin(client)
    ws = _running_ws(client, custom_dns=True, dns_servers="9.9.9.9")
    set_workspace_status(ws["id"], "stopped")
    body = client.get(f"/api/workspaces/{ws['id']}/info").json()
    fake_docker_manager.runtime_info.assert_not_called()
    assert body["container"] is None and body["resources"] is None and body["auto_stop_at"] is None
    assert body["network"]["mode"] == "direct" and body["network"]["custom_dns"] == "9.9.9.9"


def test_disk_usage(client, fake_docker_manager, monkeypatch, tmp_path):
    setup_admin(client)
    ws = _running_ws(client)
    home = tmp_path / "home"
    home.mkdir()
    (home / "f").write_bytes(b"x" * 1234)
    monkeypatch.setattr("server.docker_manager.workspace_home_path", lambda w: home)
    resp = client.get(f"/api/workspaces/{ws['id']}/disk-usage")
    assert resp.json() == {"bytes": 1234, "files": 1, "partial": False}
    monkeypatch.setattr("server.docker_manager.workspace_home_path", lambda w: None)
    assert client.get(f"/api/workspaces/{ws['id']}/disk-usage").status_code == 404


def test_public_ip_is_cached_and_reports_failure(client, fake_docker_manager):
    from server.routers import workspaces as wsr

    wsr._PUBLIC_IP_CACHE.clear()
    setup_admin(client)
    ws = _running_ws(client)
    fake_docker_manager.public_ip.return_value = {"ip": "185.65.1.2", "country": "SE"}
    r1 = client.post(f"/api/workspaces/{ws['id']}/public-ip")
    r2 = client.post(f"/api/workspaces/{ws['id']}/public-ip")
    assert r1.json()["ip"] == "185.65.1.2" and r1.json()["source"] == "ipinfo.io"
    assert r2.json() == r1.json()
    assert fake_docker_manager.public_ip.call_count == 1

    wsr._PUBLIC_IP_CACHE.clear()
    fake_docker_manager.public_ip.return_value = None
    assert client.post(f"/api/workspaces/{ws['id']}/public-ip").status_code == 502
    set_workspace_status(ws["id"], "stopped")
    assert client.post(f"/api/workspaces/{ws['id']}/public-ip").status_code == 409


def test_other_users_cannot_read_info(client, fake_docker_manager):
    from server.tests.helpers import create_user_via_admin, login

    token, _ = setup_admin(client)
    ws = _running_ws(client)
    create_user_via_admin(client, token, "bob")
    client.cookies.clear()
    login(client, "bob", "password123")
    for path in ("info", "disk-usage"):
        assert client.get(f"/api/workspaces/{ws['id']}/{path}").status_code == 403
    assert client.post(f"/api/workspaces/{ws['id']}/public-ip").status_code == 403
    fake_docker_manager.runtime_info.assert_not_called()
    fake_docker_manager.public_ip.assert_not_called()
