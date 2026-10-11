"""Minimal, anonymous Docker Registry v2 client: "is my local tag current?".

Only what the Images admin page needs — resolve the manifest digest a tag points
to right now, so it can be compared with the RepoDigests of the local copy. No
credentials are used, so private images report "unknown" rather than failing.
"""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_DOCKER_HUB = "registry-1.docker.io"
# Index + single-arch manifest types, newest first. The digest a pull records in
# RepoDigests is the one for whatever the registry serves at the tag (normally
# the multi-arch index), so ask for the same thing a pull would.
_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
_CHALLENGE_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_ref(ref: str) -> tuple[str, str, str]:
    """Split an image ref into (registry host, repository, tag)."""
    name, tag = ref, "latest"
    last = ref.rsplit("/", 1)[-1]
    if "@" in last:
        name = ref.split("@", 1)[0]
    elif ":" in last:
        name, tag = ref.rsplit(":", 1)
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, repo = first, rest
    else:
        host, repo = _DOCKER_HUB, name
    if host in ("docker.io", "index.docker.io"):
        host = _DOCKER_HUB
    if host == _DOCKER_HUB and "/" not in repo:
        repo = f"library/{repo}"
    return host, repo, tag


def local_digest(repo_digests: list[str], ref: str) -> str | None:
    """The sha256 digest of ``ref``'s repository among an image's RepoDigests."""
    want_host, want_repo, _ = parse_ref(ref)
    for entry in repo_digests or []:
        name, _, digest = entry.partition("@")
        host, repo, _ = parse_ref(name)
        if (host, repo) == (want_host, want_repo) and digest:
            return digest
    # Fall back to any digest: a mirror/alias of the same image still matches
    # if the registry serves identical content.
    for entry in repo_digests or []:
        if "@" in entry:
            return entry.partition("@")[2]
    return None


def remote_digest(ref: str, timeout: float = 10.0) -> str | None:
    """Digest the registry currently serves for ``ref``'s tag, or None."""
    host, repo, tag = parse_ref(ref)
    url = f"https://{host}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": _ACCEPT}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.head(url, headers=headers)
            if resp.status_code == 401:
                token = _anonymous_token(client, resp.headers.get("www-authenticate", ""), repo)
                if not token:
                    return None
                headers["Authorization"] = f"Bearer {token}"
                resp = client.head(url, headers=headers)
            if resp.status_code != 200:
                logger.info("registry check %s: HTTP %s", ref, resp.status_code)
                return None
            return resp.headers.get("docker-content-digest")
    except httpx.HTTPError as exc:
        logger.info("registry check %s failed: %s", ref, exc)
        return None


def _anonymous_token(client: httpx.Client, challenge: str, repo: str) -> str | None:
    if not challenge.lower().startswith("bearer"):
        return None
    params = dict(_CHALLENGE_RE.findall(challenge))
    realm = params.pop("realm", None)
    if not realm:
        return None
    params.setdefault("scope", f"repository:{repo}:pull")
    resp = client.get(realm, params=params)
    if resp.status_code != 200:
        return None
    body = resp.json()
    return body.get("token") or body.get("access_token")
