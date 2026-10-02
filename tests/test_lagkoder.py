"""Lagkoderna i matchrapporten ska ha samma ordning på varje server.

Hittat av jämförelseverktyget när den första kandidaten provades: `team_codes`
byggdes ur en mängd, och Python slumpar strängarnas hashvärden per process.
Två Cloud Run-instanser svarade därför med olika ordning för samma match.

Testet kör funktionen i separata processer med olika PYTHONHASHSEED, vilket
är precis det som skiljer två instanser åt. Samma prov körs mot den gamla
koden för att visa att det faktiskt fångar felet.

    python3 tests/test_lagkoder.py
"""
import json
import os
import re
import subprocess
import sys

HAR = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HAR, "..", "api", "main.py")
FROE = range(12)

HANDELSER = [
    {"team_code": "DIF"}, {"team_code": "IFB"}, {"team_code": "DIF"},
    {"team_code": None}, {"team_code": "IFB"}, {"team_code": ""},
]


def _kalla(namn: str) -> str:
    """Plockar ut en funktion ur main.py utan att importera filen.

    main.py drar in BigQuery och miljövariabler; funktionerna här gör inte det.
    """
    src = open(MAIN, encoding="utf-8").read()
    m = re.search(rf"^def {namn}\(.*?(?=^def |^@|^[A-Z_]+ = )", src, re.S | re.M)
    assert m, f"hittade inte {namn} i main.py"
    return m.group(0)


NY = _kalla("_is_ours") + "\n" + _kalla("_lagkoder") + \
    f"\nimport json\nprint(json.dumps(_lagkoder({HANDELSER!r})))\n"

GAMMAL = f"""
import json
events = {HANDELSER!r}
print(json.dumps([c for c in {{e.get("team_code") for e in events}} if c]))
"""


def kor(kod: str, frö: int) -> list:
    miljo = dict(os.environ, PYTHONHASHSEED=str(frö))
    ut = subprocess.run([sys.executable, "-c", kod], env=miljo,
                        capture_output=True, text=True, check=True)
    return json.loads(ut.stdout)


def test_samma_ordning_oavsett_hashfro():
    svar = {tuple(kor(NY, f)) for f in FROE}
    assert svar == {("IFB", "DIF")}, svar


def test_vart_lag_forst_aven_nar_det_kommer_sist_alfabetiskt():
    kod = _kalla("_is_ours") + "\n" + _kalla("_lagkoder") + \
        "\nimport json\nprint(json.dumps(_lagkoder([{'team_code':'AIK'},{'team_code':'IFB'}])))\n"
    assert kor(kod, 0) == ["IFB", "AIK"]


def test_tomma_och_saknade_koder_utelamnas():
    assert kor(NY, 0) == ["IFB", "DIF"]


def test_provet_fangar_den_gamla_buggen():
    # Utan det här vet vi inte om testet ovan bevisar något.
    svar = {tuple(kor(GAMMAL, f)) for f in FROE}
    assert len(svar) > 1, "den gamla koden gav samma ordning varje gång — provet är för svagt"


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
