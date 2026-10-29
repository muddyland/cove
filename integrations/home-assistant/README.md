# Cove for Home Assistant

A Home Assistant custom integration for Cove. Users get it from **Admin →
Integrations → Home Assistant integration**, a zip that Cove builds from
`custom_components/cove/` in this folder (see `docs/home-assistant.md`).

`custom_components/cove/brand/` holds the icon Home Assistant shows for the
integration (its local brand images, 2026.3+). It is `frontend/public/favicon.svg`
rendered at 256 and 512 px; re-render it if the logo changes:

```bash
docker run --rm -v "$PWD":/repo -w /repo alpine:3.22 sh -c \
  "apk add -q rsvg-convert && cd integrations/home-assistant/custom_components/cove/brand && \
   rsvg-convert -w 256 -h 256 /repo/frontend/public/favicon.svg -o icon.png && \
   rsvg-convert -w 512 -h 512 /repo/frontend/public/favicon.svg -o icon@2x.png"
```

The component talks only to `/api/integration/v1`
(`backend/server/routers/integration.py`). If you change the status document
there, change `tests/conftest.py`'s `STATUS` to match.

## Tests

The tests use `pytest-homeassistant-custom-component`, and its version must
match the Home Assistant version it is tested against. They need Python 3.14:

```bash
docker run --rm -v "$PWD":/repo -w /repo/integrations/home-assistant python:3.14-slim sh -c \
  "pip install -q pytest-homeassistant-custom-component==0.13.375 && python -m pytest -q -p no:cacheprovider"
```

0.13.375 is Home Assistant 2026.10.4. They aren't in CI yet.
