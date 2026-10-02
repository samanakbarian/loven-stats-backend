"""Enhetstester för jämförelseverktyget (S1.2).

Verktyget är grinden för allt i serveringslagret. Ett fel här betyder att
grinden släpper igenom regressioner, så den testas för sig.

    python3 -m pytest tests/test_jamfor_svar.py -q
    python3 tests/test_jamfor_svar.py          # utan pytest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jamfor_svar import jamfor, stada  # noqa: E402


def test_identiska_ger_inget():
    a = {"x": 1, "y": [1, 2, {"z": "a"}]}
    assert jamfor(a, a) == []


def test_flyttal_inom_tolerans_ar_lika():
    assert jamfor({"p": 0.1 + 0.2}, {"p": 0.3}) == []


def test_flyttal_utanfor_tolerans_ar_olika():
    assert jamfor({"p": 0.30}, {"p": 0.31})


def test_heltal_mot_flyttal_med_samma_varde_ar_lika():
    # JSON skiljer inte på 2 och 2.0 på ett sätt som spelar roll här.
    assert jamfor({"n": 2}, {"n": 2.0}) == []


def test_bool_ar_inte_ett_tal():
    # True == 1 i Python. Ett fält som byter från bool till heltal är en
    # formändring och ska fångas.
    assert jamfor({"f": True}, {"f": 1})


def test_ordning_i_listor_ar_strikt():
    # Tabellens skiljetalsbugg: samma lag, annan ordning.
    a = {"standings": ["Färjestad", "Skellefteå", "Björklöven"]}
    b = {"standings": ["Färjestad", "Björklöven", "Skellefteå"]}
    assert jamfor(a, b)


def test_olika_langd_fangas():
    assert jamfor([1, 2, 3], [1, 2])


def test_saknad_nyckel_fangas_at_bada_hall():
    assert any("saknas i B" in r for r in jamfor({"a": 1, "b": 2}, {"a": 1}))
    assert any("saknas i A" in r for r in jamfor({"a": 1}, {"a": 1, "b": 2}))


def test_typbyte_fangas():
    assert jamfor({"x": "1"}, {"x": 1})


def test_null_mot_varde_fangas():
    assert jamfor({"x": None}, {"x": 0})


def test_sokvagen_pekar_ut_platsen():
    r = jamfor({"teams": {"ours": {"shots": 23}}}, {"teams": {"ours": {"shots": 24}}})
    assert r == [".teams.ours.shots: 23 → 24"]


def test_stada_tar_bort_tidsstamplar_pa_alla_nivaer():
    o = {"generated_at": "x", "a": {"last_updated": "y", "b": 1},
         "l": [{"generated_at": "z", "c": 2}]}
    assert stada(o) == {"a": {"b": 1}, "l": [{"c": 2}]}


def test_stada_rör_inte_data():
    o = {"score": 2.67, "parts": {"sog": 3}}
    assert stada(o) == o


if __name__ == "__main__":
    tester = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fel = 0
    for t in tester:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError:
            fel += 1
            print(f"  FEL  {t.__name__}")
    print(f"\n{len(tester) - fel} av {len(tester)} gick igenom")
    sys.exit(1 if fel else 0)
