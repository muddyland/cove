"""Global application settings stored in the `app_setting` table.

These are runtime-configurable settings (managed by admins via the API),
distinct from the environment-driven `server.config.Settings`.
"""

from typing import Optional

from sqlalchemy.orm import Session

from server.models import AppSetting

# Setting keys.
KEY_TAILSCALE_IMAGE = "tailscale_image"
KEY_WORKSPACE_LAN_ACCESS = "workspace_lan_access"
KEY_WORKSPACE_NO_NEW_PRIVILEGES = "workspace_no_new_privileges"
KEY_WORKSPACE_MAX_RUNTIME_HOURS = "workspace_max_runtime_hours"

# Defaults.
DEFAULT_TAILSCALE_IMAGE = "tailscale/tailscale:latest"
DEFAULT_WORKSPACE_LAN_ACCESS = False
# Off by default: webtop desktops expect in-container sudo, which the
# no-new-privileges flag blocks. Admins can enable it to harden.
DEFAULT_WORKSPACE_NO_NEW_PRIVILEGES = False
# Auto-stop running workspaces after this many hours (0 = unlimited).
DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS = 24


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _to_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def get_tailscale_image(db: Session) -> str:
    return get_setting(db, KEY_TAILSCALE_IMAGE, DEFAULT_TAILSCALE_IMAGE) or DEFAULT_TAILSCALE_IMAGE


def get_workspace_lan_access(db: Session) -> bool:
    return _to_bool(get_setting(db, KEY_WORKSPACE_LAN_ACCESS), DEFAULT_WORKSPACE_LAN_ACCESS)


def get_workspace_no_new_privileges(db: Session) -> bool:
    return _to_bool(
        get_setting(db, KEY_WORKSPACE_NO_NEW_PRIVILEGES), DEFAULT_WORKSPACE_NO_NEW_PRIVILEGES
    )


def get_workspace_max_runtime_hours(db: Session) -> int:
    raw = get_setting(db, KEY_WORKSPACE_MAX_RUNTIME_HOURS)
    if raw is None:
        return DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WORKSPACE_MAX_RUNTIME_HOURS


def get_all(db: Session) -> dict:
    return {
        "tailscale_image": get_tailscale_image(db),
        "workspace_lan_access": get_workspace_lan_access(db),
        "workspace_no_new_privileges": get_workspace_no_new_privileges(db),
        "workspace_max_runtime_hours": get_workspace_max_runtime_hours(db),
    }
