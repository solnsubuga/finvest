"""The API process. Connect it as `finvest_app`.

uvicorn app.api_main:app --host 0.0.0.0 --port 8000 --no-access-log --no-proxy-headers
"""

from app.api.app import create_app
from app.infrastructure.logging import configure_logging
from app.infrastructure.settings import Settings

_settings = Settings()
configure_logging(_settings, "api")
app = create_app(_settings)
