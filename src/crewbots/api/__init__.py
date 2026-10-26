"""The daemon's API: HTTP for commands, one WebSocket for live updates. The desktop app and `crewbots` both use it.

- `app.py` makes the app: who may call it, and how errors answer.
- `routes/` has one module of routes per area, each a function of the daemon's context (`context.py`).
- `schemas/` has the models, by area, which the app's TypeScript types are generated from.
- `daemon.py` runs it.
"""

from crewbots.api.app import API_VERSION, DEFAULT_PORT, create_app, openapi_schema
from crewbots.api.daemon import DAEMON_FILE, DaemonInfo, read_daemon_info, serve
from crewbots.api.routes.crew import RESTART_EXIT
from crewbots.api.schemas.crew import RESTART_TIMEOUT_S

__all__ = ["API_VERSION", "DAEMON_FILE", "DEFAULT_PORT", "RESTART_EXIT", "RESTART_TIMEOUT_S", "DaemonInfo",
           "create_app", "openapi_schema", "read_daemon_info", "serve"]
