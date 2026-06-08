"""Unit tests for server.catalog._build_specs (pure, no network)."""

from server import catalog


def _fake_images():
    """Mimic the LinuxServer API image shape."""
    return [
        {
            "name": "webtop",
            "deprecated": False,
            "description": "Ubuntu/Alpine XFCE desktop in the browser",
            "tags": [
                {"tag": "latest", "desc": "Alpine XFCE"},
                {"tag": "ubuntu-kde", "desc": "Ubuntu KDE"},
                {"tag": "fedora-xfce", "desc": "Fedora XFCE"},
            ],
        },
        {
            "name": "kali-linux",
            "deprecated": False,
            "description": "Kali Linux desktop",
            "tags": [
                {"tag": "latest", "desc": "Kali"},
            ],
        },
        {
            "name": "chromium",
            "deprecated": False,
            "description": "Chromium browser",
            "tags": [{"tag": "latest", "desc": "Chromium"}],
        },
        {
            "name": "brave",
            "deprecated": False,
            "description": "Brave browser",
            "tags": [{"tag": "latest", "desc": "Brave"}],
        },
        {
            "name": "firefox",
            "deprecated": False,
            "description": "Firefox browser",
            "tags": [{"tag": "latest", "desc": "Firefox"}],
        },
        {
            "name": "msedge",
            "deprecated": False,
            "description": "Microsoft Edge browser",
            "tags": [{"tag": "latest", "desc": "Edge"}],
        },
        {
            # Should be skipped entirely.
            "name": "deprecated-thing",
            "deprecated": True,
            "description": "old",
            "tags": [{"tag": "latest", "desc": "old"}],
        },
    ]


def test_webtop_one_spec_per_tag_including_latest():
    specs = catalog._build_specs(_fake_images())
    webtop = [s for s in specs if s["docker_image"].startswith("lscr.io/linuxserver/webtop:")]
    # 3 tags -> 3 specs, none dropped (latest/Alpine-XFCE preserved).
    assert len(webtop) == 3
    tags = {s["docker_image"].rsplit(":", 1)[1] for s in webtop}
    assert "latest" in tags
    assert tags == {"latest", "ubuntu-kde", "fedora-xfce"}


def test_desktop_specs_shape():
    specs = catalog._build_specs(_fake_images())
    desktops = [s for s in specs if s["image_type"] == "desktop"]
    assert desktops, "expected desktop specs"
    for s in desktops:
        assert s["image_type"] == "desktop"
        assert s["url_env"] is None
        assert s["internal_port"] == catalog.WEBTOP_PORT
    webtops = [s for s in desktops if "webtop" in s["docker_image"]]
    for s in webtops:
        assert s["docker_image"].startswith("lscr.io/linuxserver/webtop:")


def test_browser_specs_have_correct_url_env():
    specs = catalog._build_specs(_fake_images())
    by_name = {s["name"]: s for s in specs}
    assert by_name["Chromium"]["image_type"] == "browser"
    assert by_name["Chromium"]["url_env"] == "CHROME_CLI"
    assert by_name["Brave"]["image_type"] == "browser"
    assert by_name["Brave"]["url_env"] == "BRAVE_CLI"
    assert by_name["Firefox"]["image_type"] == "browser"
    assert by_name["Firefox"]["url_env"] == "FIREFOX_CLI"
    assert by_name["Edge"]["image_type"] == "browser"
    assert by_name["Edge"]["url_env"] == "MSEDGE_CLI"
    assert by_name["Edge"]["docker_image"] == "lscr.io/linuxserver/msedge:latest"
    for name in ("Chromium", "Brave", "Firefox"):
        assert by_name[name]["docker_image"] == f"lscr.io/linuxserver/{name.lower()}:latest"


def test_no_duplicate_names():
    specs = catalog._build_specs(_fake_images())
    names = [s["name"] for s in specs]
    assert len(names) == len(set(names))


def test_deprecated_images_skipped():
    specs = catalog._build_specs(_fake_images())
    names = [s["name"] for s in specs]
    assert not any("old" in n.lower() for n in names)
    assert not any("deprecated-thing" in s["docker_image"] for s in specs)


def test_missing_image_is_ignored():
    # Only a browser present; webtop/kali absent -> no desktop specs, no error.
    specs = catalog._build_specs(
        [{"name": "chromium", "deprecated": False, "description": "c",
          "tags": [{"tag": "latest", "desc": "Chromium"}]}]
    )
    assert all(s["image_type"] == "browser" for s in specs)


def test_linuxserver_base_name_variants():
    f = catalog.linuxserver_base_name
    assert f("lscr.io/linuxserver/handbrake:latest") == "handbrake"
    assert f("linuxserver/webtop") == "webtop"
    assert f("ghcr.io/linuxserver/kali-linux:rolling") == "kali-linux"
    assert f("docker.io/library/nginx:1.25") is None
    assert f("nginx") is None
    assert f("") is None
