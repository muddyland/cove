"""Unit tests for the Tailscale TS_EXTRA_ARGS builder.

The builder is a pure function, so these tests don't need Docker or the DB.
"""

from server.docker_manager import build_ts_extra_args


def test_exit_node_adds_allow_lan_access():
    """An exit node must also enable LAN access so Traefik can reach the stream."""
    args = build_ts_extra_args(
        exit_node="100.64.0.1",
        accept_routes=True,
        accept_dns=True,
        login_server=None,
    )
    assert "--exit-node=100.64.0.1" in args
    assert "--exit-node-allow-lan-access" in args


def test_no_exit_node_omits_exit_flags():
    args = build_ts_extra_args(
        exit_node=None,
        accept_routes=True,
        accept_dns=True,
        login_server=None,
    )
    assert not any(a.startswith("--exit-node") for a in args)
    assert "--exit-node-allow-lan-access" not in args


def test_accept_routes_toggles():
    on = build_ts_extra_args(
        exit_node=None, accept_routes=True, accept_dns=True, login_server=None
    )
    off = build_ts_extra_args(
        exit_node=None, accept_routes=False, accept_dns=True, login_server=None
    )
    assert "--accept-routes" in on
    assert "--accept-routes" not in off


def test_accept_dns_reflects_flag():
    on = build_ts_extra_args(
        exit_node=None, accept_routes=True, accept_dns=True, login_server=None
    )
    off = build_ts_extra_args(
        exit_node=None, accept_routes=True, accept_dns=False, login_server=None
    )
    assert "--accept-dns=true" in on
    assert "--accept-dns=false" in off


def test_login_server_appended_when_set():
    args = build_ts_extra_args(
        exit_node=None,
        accept_routes=True,
        accept_dns=True,
        login_server="https://hs.example.com",
    )
    assert "--login-server=https://hs.example.com" in args
