from app.core.config import get_settings
from app.domains.market.connectors.base import MarketConnector
from app.domains.market.connectors.sipsa import SipsaCiudadConnector


def get_connector(code: str) -> MarketConnector:
    if code == SipsaCiudadConnector.code:
        return SipsaCiudadConnector(get_settings().sipsa_url)
    raise KeyError(f"No hay un conector registrado para la fuente '{code}'")
