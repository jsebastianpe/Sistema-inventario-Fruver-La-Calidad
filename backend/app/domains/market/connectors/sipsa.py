"""Conector SIPSA (DANE) — operación `promediosSipsaCiudad`.

Verificado contra el servicio real el 2026-10-16 (ver docs/02-investigacion-sipsa.md):
  * SOAP 1.2, sin parámetros, sin credenciales. Devuelve TODO el histórico desde 2020
    (~114 MB, ~60 s), por eso la respuesta se descarga en streaming a un archivo temporal
    y se procesa incrementalmente.
  * Campos por registro: ciudad, codProducto, producto, fechaCaptura, fechaCreacion,
    precioPromedio, regId, enviado.
  * PENDIENTE: la documentación oficial no indica la unidad de `precioPromedio`.
    El conector NO supone ninguna; la unidad se confirma por producto en la aplicación.
"""
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterator
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx
from defusedxml import ElementTree as SafeET

from app.domains.market.connectors.base import FetchStats, RawQuote, SourceError

OPERATION = "promediosSipsaCiudad"
NAMESPACE = "http://servicios.sipsa.co.gov.dane/"
ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
    f'xmlns:s="{NAMESPACE}"><soap:Body><s:{OPERATION}/></soap:Body></soap:Envelope>'
)
USER_AGENT = "FruverGestion/0.1 (sistema interno de gestion comercial; consulta diaria)"

# Nombres de ciudad que la fuente publica de dos formas (verificado en datos reales).
CITY_ALIASES = {"SAN JOSE DE CUCUTA": "CÚCUTA", "CUCUTA": "CÚCUTA"}

REQUIRED = ("ciudad", "codProducto", "producto", "fechaCaptura", "precioPromedio")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_market(city: str) -> str:
    clean = " ".join(city.split()).upper()
    return CITY_ALIASES.get(_strip_accents(clean), clean)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SipsaCiudadConnector:
    code = "sipsa_ciudad"

    def __init__(self, url: str, *, timeout_connect: float = 30, timeout_read: float = 300,
                 retries: int = 3, backoff: tuple[float, ...] = (10, 30, 90),
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self.timeout = httpx.Timeout(timeout_read, connect=timeout_connect)
        self.retries = retries
        self.backoff = backoff
        self.transport = transport   # solo las pruebas inyectan un transporte simulado
        self.sleep = sleep

    # ---------------------------------------------------------------- descarga

    def _download(self, dest, stats: FetchStats) -> None:
        headers = {"Content-Type": "application/soap+xml; charset=utf-8", "User-Agent": USER_AGENT}
        with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=True) as client:
            with client.stream("POST", self.url, content=ENVELOPE.encode(), headers=headers) as r:
                if r.status_code == 429 or r.status_code >= 500:
                    raise SourceError(f"La fuente respondió HTTP {r.status_code}", transient=True)
                if r.status_code >= 400:
                    # SOAP 1.2 devuelve 500/400 con Fault; 4xx distinto es un error no recuperable.
                    raise SourceError(f"La fuente rechazó la solicitud (HTTP {r.status_code})",
                                      transient=False)
                for chunk in r.iter_bytes(1 << 20):
                    dest.write(chunk)
                    stats.bytes_downloaded += len(chunk)

    def _download_with_retries(self, dest, stats: FetchStats) -> None:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            dest.seek(0)
            dest.truncate()
            stats.bytes_downloaded = 0
            try:
                self._download(dest, stats)
                return
            except SourceError as e:
                if not e.transient:
                    raise
                last = e
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last = SourceError(f"Error de red al consultar SIPSA: {type(e).__name__}", transient=True)
            if attempt < self.retries:
                self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        assert last is not None
        raise last

    # ---------------------------------------------------------------- lectura

    def fetch(self, since: date, stats: FetchStats) -> Iterator[RawQuote]:
        with tempfile.TemporaryFile() as tmp:
            self._download_with_retries(tmp, stats)
            if stats.bytes_downloaded == 0:
                raise SourceError("La fuente devolvió una respuesta vacía", transient=True)
            tmp.seek(0)
            yield from self._parse(tmp, since, stats)

    def _parse(self, stream, since: date, stats: FetchStats) -> Iterator[RawQuote]:
        saw_response = False
        root = None
        try:
            for event, el in SafeET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    if root is None:
                        root = el
                    continue
                tag = el.tag.rsplit("}", 1)[-1]
                if tag == "Fault":
                    text = " ".join(t.strip() for t in el.itertext() if t.strip())[:300]
                    raise SourceError(f"La fuente devolvió un error SOAP: {text}", transient=False)
                if tag == f"{OPERATION}Response":
                    saw_response = True
                if tag != "return":
                    continue
                stats.records_read += 1
                rec = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in el}
                el.clear()
                if stats.records_read % 5000 == 0 and root is not None:
                    root.clear()
                quote = self._to_quote(rec, since, stats)
                if quote is not None:
                    yield quote
        except SafeET.ParseError as e:
            raise SourceError(f"Respuesta XML inválida o incompleta: {e}", transient=True) from e
        if not saw_response:
            raise SourceError("La respuesta no contiene la operación esperada", transient=False)

    @staticmethod
    def _to_quote(rec: dict, since: date, stats: FetchStats) -> RawQuote | None:
        missing = [f for f in REQUIRED if not rec.get(f)]
        if missing:
            stats.invalid(f"regId={rec.get('regId')}: faltan {', '.join(missing)}")
            return None
        try:
            price_date = _parse_dt(rec["fechaCaptura"]).date()
        except ValueError:
            stats.invalid(f"regId={rec.get('regId')}: fecha inválida '{rec['fechaCaptura']}'")
            return None
        if price_date < since:
            stats.records_out_of_scope += 1
            return None
        try:
            price = Decimal(rec["precioPromedio"]).quantize(Decimal("0.01"))
        except InvalidOperation:
            stats.invalid(f"regId={rec.get('regId')}: precio no numérico")
            return None
        if price <= 0:
            stats.invalid(f"regId={rec.get('regId')}: precio no positivo ({price})")
            return None
        published = None
        if rec.get("fechaCreacion"):
            try:
                published = _parse_dt(rec["fechaCreacion"])
            except ValueError:
                published = None
        try:
            code = str(int(Decimal(rec["codProducto"])))
        except (InvalidOperation, ValueError):
            stats.invalid(f"regId={rec.get('regId')}: código de producto inválido")
            return None
        return RawQuote(external_code=code, name=rec["producto"].strip(),
                        market=normalize_market(rec["ciudad"]), price_date=price_date, price=price,
                        external_id=rec.get("regId") or None, published_at=published)
