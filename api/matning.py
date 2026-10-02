"""Mätning per anrop: tid, BigQuery-frågor och cache (S2.1).

Serveringslagret ska visa att det blev bättre, och dimensioneringen ska
styras av verkliga tal. Det kräver att varje anrop lämnar en rad efter sig:

    {"message": "anrop", "vag": "/api/v1/match/{game_id}", "ms": 412,
     "bq": 3, "cache": "miss", "status": 200, "intern": false}

Raden går till stdout som JSON. Cloud Run läser JSON-rader som strukturerade
loggar, så fälten hamnar under `jsonPayload` och går att filtrera på i Logs
Explorer. Frågan står i docs/serveringslager/MATNING.md.

Hur räkningen går till:

  Anropet får ett eget `Matning`-objekt i en contextvar. Mellanlagret sätter
  det, endpointen fyller det, mellanlagret skriver ut det.

  BigQuery-frågorna räknas genom att `bigquery.Client.query` lindas in en
  gång vid start — inte i var och en av de 29 endpointsen. Frågor som körs i
  `fraga_parallellt` går i andra trådar; därför kör den sina jobb i en kopia
  av kontexten. Kopian pekar på samma objekt, så räkningen hamnar rätt.

  Cachen markeras av `cached_ok`. Endpoints anropar varandra — spelarprofilen
  frågar målvaktslistan — så den YTTERSTA cacheuppslagningen är den som
  räknas, alltså den som avgjorde om besökaren fick ett färdigt svar.

Inget här får ändra ett svar eller fälla ett anrop. Går mätningen fel blir
det en rad mindre i loggen, inte ett fel hos besökaren.
"""
from __future__ import annotations

import contextvars
import functools
import json
import sys
import time
from typing import Any, Callable

TRAFF = "traff"
MISS = "miss"
FORBI = "forbi"  # refresh=1, cachen kringgicks med flit


class Matning:
    __slots__ = ("bq", "cache", "start")

    def __init__(self) -> None:
        self.bq = 0
        self.cache: str | None = None
        self.start = time.perf_counter()


_aktuell: contextvars.ContextVar[Matning | None] = contextvars.ContextVar(
    "loven_matning", default=None
)


def borja() -> tuple[Matning, contextvars.Token]:
    m = Matning()
    return m, _aktuell.set(m)


def sluta(token: contextvars.Token) -> None:
    _aktuell.reset(token)


def aktuell() -> Matning | None:
    return _aktuell.get()


def raknad_fraga() -> None:
    m = _aktuell.get()
    if m is not None:
        m.bq += 1


def markera_cache(utfall: str) -> None:
    """Sätter cacheutfallet om det inte redan är satt — den yttersta vinner."""
    m = _aktuell.get()
    if m is not None and m.cache is None:
        m.cache = utfall


def instrumentera_bigquery(client_cls: type) -> None:
    """Lindar in `client_cls.query` så att varje fråga räknas. Idempotent."""
    original = client_cls.query
    if getattr(original, "_loven_raknad", False):
        return

    @functools.wraps(original)
    def query(self, *args, **kwargs):
        try:
            raknad_fraga()
        except Exception:
            pass
        return original(self, *args, **kwargs)

    query._loven_raknad = True  # type: ignore[attr-defined]
    client_cls.query = query


def i_kontext(fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn` körd i en kopia av anroparens kontext — för trådpooler.

    `concurrent.futures` tar inte med contextvars till sina trådar. Utan den
    här hade frågorna i `fraga_parallellt` räknats i ett tomt sammanhang och
    försvunnit.
    """
    ctx = contextvars.copy_context()
    return functools.partial(ctx.run, fn)


def rad(vag: str, status: int, intern: bool, m: Matning) -> dict:
    return {
        "severity": "INFO",
        "message": "anrop",
        "vag": vag,
        "ms": round((time.perf_counter() - m.start) * 1000),
        "bq": m.bq,
        "cache": m.cache or "ingen",
        "status": status,
        "intern": intern,
    }


def skriv(post: dict, ut=None) -> None:
    try:
        print(json.dumps(post, ensure_ascii=False), file=ut or sys.stdout, flush=True)
    except Exception:
        pass


def vagmall(request: Any) -> str:
    """Routens mall, så att /match/1109972 och /match/1109944 räknas ihop."""
    route = request.scope.get("route")
    mall = getattr(route, "path", None)
    return mall or request.url.path
