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

# Defaults.
DEFAULT_TAILSCALE_IMAGE = "tailscale/tailscale:latest"
DEFAULT_WORKSPACE_LAN_ACCESS = False


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


def get_all(db: Session) -> dict:
    return {
        "tailscale_image": get_tailscale_image(db),
        "workspace_lan_access": get_workspace_lan_access(db),
    }
