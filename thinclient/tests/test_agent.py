"""Unit tests for the on-device agent's pure logic (no root, no devices)."""

import importlib.util
import json
import os
import types

import pytest

_PATH = os.path.join(os.path.dirname(__file__), "..", "agent", "cove_thin_client.py")
_spec = importlib.util.spec_from_file_location("cove_thin_client", _PATH)
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)


def _redirect_open(monkeypatch, path, replacement):
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real_open(replacement if p == path else p, *a, **k))


def test_chromium_policy_blocks_everything_but_cove():
    p = agent.chromium_policy([".cove.example.com", "ws.example.com", ".auth.example.com"], "https://cove.example.com")
    assert p["URLBlocklist"][0] == "*"
    assert "chrome://*" in p["URLBlocklist"] and "file://*" in p["URLBlocklist"]
    assert p["URLAllowlist"][:3] == [".cove.example.com", "ws.example.com", ".auth.example.com"]
    # Only the agent's own offline page is reachable on disk.
    assert p["URLAllowlist"][-1] == f"file://{agent.SHARE}/"
    assert not any(h.endswith("*") for h in p["URLAllowlist"])
    assert p["ClipboardAllowedForUrls"] == [
        "https://cove.example.com", "https://[*.]ws.example.com", "https://auth.example.com",
    ]
    for key, value in {"DeveloperToolsAvailability": 2, "PasswordManagerEnabled": False,
                       "DownloadRestrictions": 3, "IncognitoModeAvailability": 1}.items():
        assert p[key] == value
    json.dumps(p)


def test_sway_config_has_no_bindings_and_no_vt_switching(monkeypatch):
    monkeypatch.setattr(agent, "keyboard_layout", lambda: ("de", "nodeadkeys"))
    conf = agent.sway_config()
    assert "bindsym" not in conf and "bindcode" not in conf
    assert "xkb_options srvrkeys:none" in conf
    assert "xkb_layout de" in conf and "xkb_variant nodeadkeys" in conf
    assert f"include {agent.SWAY_OUTPUTS}" in conf
    assert "xwayland disable" in conf


def test_keyboard_layout_parsing(tmp_path, monkeypatch):
    f = tmp_path / "keyboard"
    f.write_text('XKBMODEL="pc105"\nXKBLAYOUT="gb"\nXKBVARIANT=""\nXKBOPTIONS=""\n')
    _redirect_open(monkeypatch, "/etc/default/keyboard", f)
    assert agent.keyboard_layout() == ("gb", "")


def test_input_devices_only_keyboards_and_pointers(monkeypatch, tmp_path):
    text = (
        "I: Bus=0019\nN: Name=\"Power Button\"\nH: Handlers=kbd event0\n\n"
        "I: Bus=0003\nN: Name=\"Logitech Mouse\"\nH: Handlers=mouse0 event3\n\n"
        "I: Bus=0000\nN: Name=\"HDA Intel HDMI\"\nH: Handlers=event7\n\n"
        "I: Bus=0003\nN: Name=\"Keyboard\"\nH: Handlers=sysrq kbd leds event4\n"
    )
    f = tmp_path / "devices"
    f.write_text(text)
    _redirect_open(monkeypatch, "/proc/bus/input/devices", f)
    assert agent.Activity.input_devices() == ["/dev/input/event0", "/dev/input/event3", "/dev/input/event4"]


@pytest.mark.parametrize("start,end,now,expected", [
    ("07:00", "23:00", "12:00", True),
    ("07:00", "23:00", "23:30", False),
    ("22:00", "06:00", "23:30", True),   # wraps midnight
    ("22:00", "06:00", "05:59", True),
    ("22:00", "06:00", "12:00", False),
    (None, None, "03:00", True),
])
def test_on_hours(monkeypatch, start, end, now, expected):
    fake = types.SimpleNamespace(policy={"on_hours_start": start, "on_hours_end": end})
    h, m = map(int, now.split(":"))

    class FakeDT(agent.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return agent.dt.datetime(2026, 1, 1, h, m)

    monkeypatch.setattr(agent.dt, "datetime", FakeDT)
    assert agent.Agent.in_on_hours(fake) is expected


def test_identify_text_cannot_reach_the_shell(monkeypatch):
    sent = []
    fake = types.SimpleNamespace(
        forced_off=True,
        activity=types.SimpleNamespace(touch=lambda: None),
        set_display=lambda on: None,
        cfg={"name": 'bench"; rm -rf / #$(reboot)`id`'},
        sway=types.SimpleNamespace(command=sent.append),
    )
    monkeypatch.setattr(agent, "local_ips", lambda: ["10.0.0.5"])
    monkeypatch.setattr(agent.threading, "Timer", lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    agent.Agent.identify(fake, 5)
    msg = sent[0].split('-m "', 1)[1].split('"', 1)[0]
    for bad in ('"', ";", "$", "`", "#"):
        assert bad not in msg
    assert "BENCH" in msg and "10.0.0.5" in msg


def test_parse_ts_handles_naive_server_times():
    t = agent.parse_ts("2026-10-24T12:00:00")
    assert t.tzinfo is not None and t.hour == 12
    assert agent.parse_ts(None) is None and agent.parse_ts("garbage") is None


def test_write_if_changed(tmp_path):
    p = tmp_path / "sub" / "f.conf"
    assert agent.write_if_changed(str(p), "a\n") is True
    assert agent.write_if_changed(str(p), "a\n") is False
    assert agent.write_if_changed(str(p), "b\n", mode=0o600) is True
    assert p.read_text() == "b\n" and oct(p.stat().st_mode & 0o777) == "0o600"


def test_output_mode():
    assert agent.output_mode({"width": 1920, "height": 1080, "refresh": 59951}) == "1920x1080@60"
    assert agent.output_mode({"width": 1280, "height": 720, "refresh": 0}) == "1280x720"
    assert agent.output_mode({}) is None
