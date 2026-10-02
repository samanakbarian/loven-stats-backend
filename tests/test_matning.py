"""Mätningen per anrop (S2.1), provad mot riktig FastAPI.

Kräver fastapi och httpx. API:ts egen miljö har fastapi; httpx behövs bara
för testklienten:

    pip install fastapi httpx cachetools
    python3 tests/test_matning.py

Testet bygger en liten app med samma koppling som api/main.py, men tar
`cached_ok` och `fraga_parallellt` ur main.py:s källkod i stället för att
skriva egna kopior. Det är alltså den riktiga koden som provas — main.py
själv går inte att importera här, den drar in BigQuery.

Det viktigaste provet är trådarna. Endpoints körs i Starlettes trådpool och
`fraga_parallellt` startar en egen; contextvars följer inte med av sig
själva. Testet visar både att räkningen fungerar och att den INTE hade
fungerat utan `i_kontext`.
"""
import contextlib
import io
import json
import os
import re
import sys
import time
import functools
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

HAR = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(HAR, "..", "api")
sys.path.insert(0, API)

import matning  # noqa: E402
from cachetools import TTLCache  # noqa: E402
from cachetools.keys import hashkey  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MAIN = open(os.path.join(API, "main.py"), encoding="utf-8").read()


def _kalla(namn: str) -> str:
    m = re.search(rf"^def {namn}\(.*?(?=^def |^@|^[A-Za-z_]+ = )", MAIN, re.S | re.M)
    assert m, f"hittade inte {namn} i main.py"
    return m.group(0)


NS = {
    "functools": functools, "hashkey": hashkey, "matning": matning,
    "ThreadPoolExecutor": ThreadPoolExecutor, "as_completed": as_completed,
    "logging": __import__("logging"),
}
exec(_kalla("cached_ok"), NS)
exec(_kalla("fraga_parallellt"), NS)
cached_ok = NS["cached_ok"]
fraga_parallellt = NS["fraga_parallellt"]


class Rad(dict):
    pass


class Jobb:
    def result(self):
        time.sleep(0.01)
        return [Rad(x=1)]


class FalskKlient:
    def query(self, sql, *a, **k):
        return Jobb()


matning.instrumentera_bigquery(FalskKlient)
BQ = FalskKlient()

app = FastAPI()
cache = TTLCache(maxsize=32, ttl=60)


@app.middleware("http")
async def mat_anropet(request: Request, call_next):
    # Samma kropp som i main.py, med fast intern-flagga.
    m, token = matning.borja()
    status = 500
    try:
        svar = await call_next(request)
        status = svar.status_code
        return svar
    finally:
        try:
            matning.skriv(matning.rad(matning.vagmall(request), status, False, m))
        finally:
            matning.sluta(token)


@app.get("/api/v1/tva")
@cached_ok(cache=cache)
def tva(season: str = None, refresh: bool = False):
    BQ.query("a").result()
    BQ.query("b").result()
    return {"status": "ok", "season": season}


@app.get("/api/v1/inre")
@cached_ok(cache=cache)
def inre(season: str = None, refresh: bool = False):
    BQ.query("inre").result()
    return {"status": "ok"}


@app.get("/api/v1/yttre")
@cached_ok(cache=cache)
def yttre(season: str = None, refresh: bool = False):
    inre(season=season)
    BQ.query("yttre").result()
    return {"status": "ok"}


@app.get("/api/v1/parallellt")
def parallellt():
    ut = fraga_parallellt(BQ, {f"f{i}": "sql" for i in range(4)}, strikt=True)
    return {"status": "ok", "n": len(ut)}


@app.get("/api/v1/utan_kontext")
def utan_kontext():
    # Kontrollen: samma sak som fraga_parallellt men UTAN i_kontext.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for f in [pool.submit(lambda: BQ.query("x").result()) for _ in range(4)]:
            f.result()
    return {"status": "ok"}


@app.get("/api/v1/match/{game_id}")
def match(game_id: int):
    BQ.query("m").result()
    return {"status": "ok", "game_id": game_id}


@app.get("/api/v1/kraschar")
def kraschar():
    raise RuntimeError("avsiktligt")


klient = TestClient(app, raise_server_exceptions=False)


def anropa(vag: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = klient.get(vag)
    rader = [json.loads(x) for x in buf.getvalue().splitlines() if x.startswith("{")]
    rader = [x for x in rader if x.get("message") == "anrop"]
    assert len(rader) == 1, f"{vag}: {len(rader)} loggrader"
    return r, rader[0]


def test_miss_raknar_fragorna():
    cache.clear()
    r, rad = anropa("/api/v1/tva?season=x")
    assert r.status_code == 200
    assert (rad["cache"], rad["bq"], rad["status"]) == ("miss", 2, 200), rad


def test_traff_ger_noll_fragor():
    cache.clear()
    anropa("/api/v1/tva?season=x")
    _, rad = anropa("/api/v1/tva?season=x")
    assert (rad["cache"], rad["bq"]) == ("traff", 0), rad


def test_refresh_markeras_forbi():
    cache.clear()
    anropa("/api/v1/tva?season=x")
    _, rad = anropa("/api/v1/tva?season=x&refresh=true")
    assert (rad["cache"], rad["bq"]) == ("forbi", 2), rad


def test_yttersta_cacheuppslagningen_vinner():
    cache.clear()
    anropa("/api/v1/inre?season=y")          # värmer den inre
    _, rad = anropa("/api/v1/yttre?season=y")
    # Yttre missade, inre träffade: besökaren fick inget färdigt svar.
    assert (rad["cache"], rad["bq"]) == ("miss", 1), rad


def test_fragor_i_tradpool_raknas():
    _, rad = anropa("/api/v1/parallellt")
    assert rad["bq"] == 4, rad


def test_kontrollen_utan_kontext_tappar_fragorna():
    # Utan det här vet vi inte om i_kontext gör något.
    _, rad = anropa("/api/v1/utan_kontext")
    assert rad["bq"] == 0, rad


def test_vagmallen_slar_ihop_matcher():
    _, a = anropa("/api/v1/match/1109972")
    _, b = anropa("/api/v1/match/1109944")
    assert a["vag"] == b["vag"] == "/api/v1/match/{game_id}", (a, b)


def test_svaret_ar_orort():
    r, _ = anropa("/api/v1/match/42")
    assert r.json() == {"status": "ok", "game_id": 42}


def test_krasch_loggas_som_500_och_falls_inte_tyst():
    r, rad = anropa("/api/v1/kraschar")
    assert r.status_code == 500
    assert rad["status"] == 500, rad


def test_tid_finns_och_ar_rimlig():
    cache.clear()
    _, rad = anropa("/api/v1/tva?season=z")
    assert isinstance(rad["ms"], int) and 15 <= rad["ms"] < 5000, rad


def test_instrumenteringen_ar_idempotent():
    matning.instrumentera_bigquery(FalskKlient)
    matning.instrumentera_bigquery(FalskKlient)
    cache.clear()
    _, rad = anropa("/api/v1/tva?season=w")
    assert rad["bq"] == 2, rad   # inte 6


def test_utan_matning_gor_raknaren_ingenting():
    # Utanför ett anrop — skrapan, ett skript — får räknaren inte krascha.
    BQ.query("fri")
    matning.markera_cache("miss")


# -- Sammanfattningen (tests/sammanfatta_matning.py) --

sys.path.insert(0, HAR)
import sammanfatta_matning as sm  # noqa: E402


def test_percentil_narmaste_rang():
    assert sm.percentil([], 50) == 0
    assert sm.percentil([7], 95) == 7
    v = list(range(1, 101))
    assert (sm.percentil(v, 50), sm.percentil(v, 95)) == (50, 95)


def test_sammanfattning_skiljer_intern_och_lasar_loggposter():
    poster = [
        {"jsonPayload": {"message": "anrop", "vag": "/a", "ms": 10, "bq": 2, "cache": "miss", "status": 200, "intern": False}},
        {"jsonPayload": {"message": "anrop", "vag": "/a", "ms": 30, "bq": 0, "cache": "traff", "status": 200, "intern": False}},
        {"jsonPayload": {"message": "anrop", "vag": "/a", "ms": 90, "bq": 3, "cache": "forbi", "status": 502, "intern": True}},
        {"jsonPayload": {"message": "annat"}},
        {"textPayload": "uvicorn"},
    ]
    ut = {(r["vag"], r["intern"]): r for r in sm.sammanfatta(poster)}
    a = ut[("/a", False)]
    assert (a["n"], a["bq"], a["miss"], a["traff"], a["fel"], a["max"]) == (2, 2, 1, 1, 0, 30), a
    b = ut[("/a", True)]
    assert (b["n"], b["miss"], b["fel"]) == (1, 1, 1), b
    assert "intern" in sm.skriv_tabell(list(ut.values()))


if __name__ == "__main__":
    tester = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fel = 0
    for t in tester:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            fel += 1
            print(f"  FEL  {t.__name__}: {e}")
    print(f"\n{len(tester) - fel} av {len(tester)} gick igenom")
    sys.exit(1 if fel else 0)
