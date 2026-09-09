# Städlista: rotkatalogen

Sammanställd 2026-09-09. **Inget är borttaget än** — det här är underlaget.

Av 226 spårade filer ligger 127 löst i rotkatalogen som sediment från
felsökningssessioner: 113 `.py` och 14 data- och utdatafiler. Det är 56 procent
av repot. Ingen av dem ingår i någon körväg.

Städningen är avsiktligt lågt prioriterad — den ska göras EFTER premiären
19 september. Sju filer är dock ett säkerhetsproblem och kan tas bort direkt;
se sista avsnittet.

## Hur "oanvänd" fastställdes

Kravet var att bara ta bort sånt som faktiskt inte används. Ett skript kan vara
användbart och köras för hand utan att någonsin refereras, så enbart
referenssökning räcker inte. Tre kontroller användes:

1. **Refereras filnamnet någonstans?** `deploy.sh`, `.github/workflows/`,
   `docs/`, `api/`, `functions/`, eller importeras av ett annat rotskript.
2. **Har filen ett gränssnitt?** `argparse` eller `sys.argv` betyder att någon
   tänkt köra den mer än en gång.
3. **Är den låst vid ett tillfälle?** Hårdkodat `season_group_id` eller
   `game_id` betyder att den inte kan återanvändas.

Utfallet: **en enda fil klarade kontrollerna.** Medianfilen är elva rader lång,
och 56 av dem bär ett hårdkodat id. Samtliga är senast rörda 13–14 juni 2026.

Kontrollen går att köra om — kommandot står längst ned.

## Behåll

| Fil | Varför |
|---|---|
| `backfill_season.py` | Enda med `argparse`. Refereras i `docs/ETL_DAGBOK.md`, `docs/ARCHITECTURE_INTEGRATION_2026_06.md` och i backloggen som pågående arbete ("Lägg samma `run_id`-kontrakt i `backfill_season.py`"). |

`.gitignore`, `README.md`, `SYSTEM_DOCUMENTATION.md` och `deploy.sh` behålls
förstås också.

**Falska träffar:** `fix.py` och `patch.py` såg refererade ut, men dokumenten
innehåller orden "fix" och "patch" i löptext — inte filnamnen. De ska bort.

## Ta bort: 113 skript

Grupperade efter vad de är, inte efter namn.

**Numrerade syskon** — samma felsökning, försök ett till sex.
`fix_bq.py`/`fix_bq2.py`, `patch.py`/`patch2.py`, `test_bq.py`–`test_bq6.py`,
`test_goalies.py`/`test_goalies2.py`, `test_why.py`/`test_why2.py`,
`test_table0.py`/`test_table3.py`, `fix_cache.py`/`fix_cache2.py`,
`test_logic_local.py`/`test_logic_local2.py`, `test_team_links.py`/`2.py`.

**Låsta vid ett id** (56 st) — kan inte återanvändas. `test_18266.py`,
`find_20962.py`, `test_scrape_20822.py`, `test_insert_16147.py`,
`test_season_20822.py`, `test_2425.py`, `test_2526.py`, `check_ha2526.py`,
`test_goalies_18266.py`, `test_goalies_api_2425.py` med flera.

**Ersatta av `deploy.sh`** — kör man dem i dag får man ett sämre resultat än
målet i deploy-skriptet, eftersom de saknar kvalitetsgrindar och
körningsloggning. `run_local.py`, `run_scrapers.py`, `run_scraper_local.py`
och `load_historical_seasons.py` (vars egen docstring säger "Manually scrape
and load SHL 24/25 … These are inactive seasons") täcks nu av
`bash deploy.sh backfill`.

**Sonderingar och spår** — `probe_shl.py`, `trace_api.py`, `trace_json.py`,
`scratch_goalie_inspect.py`, `dump_seasons.py`, `extract_state.py`,
`query_bq.py` (importerar dessutom `pandas`, som inte finns i någon
requirements-fil).

**Resten** — `check_*.py` (10 st), `find_*.py` (8 st), `test_*.py` (~60 st).

### Läs den här innan du raderar den

`final_test.py` slår mot produktions-API:t och kontrollerar att endpointsen
svarar. Det är i grunden rätt idé — det är ungefär det röktest som saknades
när frontendbygget gick sönder 8 september och deployen stod still ett dygn.
**Skriptet ska bort, men idén ska tas vidare** som ett riktigt test innan det
kastas.

## Ta bort: 14 data- och utdatafiler

Utdrag och terminalutskrifter, alla från maj–juni 2026:
`analytics.json`, `analytics_2.json`, `analytics_3.json`, `prod_out.json`
(78 kB), `logs.json` (8 kB), `out.txt`, `teams.json`, `playersbyteam.html`,
`test_goalies_out.txt`, `test_goalies_out2.txt`, `test_scrape_out.txt`,
`tmp_latest_silly.json`, `tmp_newest.json`, `tmp_official_rendered.json`.

Inga hemligheter i dem — det kontrollerades i säkerhetsgenomgången 8 september.

## Gör först, av säkerhetsskäl

Sju skript skriver eller raderar i produktionens BigQuery utan bekräftelse,
med projekt-id hårdkodat. `fix_bq2.py` är fyra rader och kör
`UPDATE … SET playoff_id = NULL`. Ett felskrivet `python fix_bq2.py` ändrar
prod.

```
clean_bq.py  del_bq.py  fix_bq.py  fix_bq2.py
fix_seasons.py  update_ha2526.py  update_seasons.py
```

Samtliga är engångsmigreringar som redan är körda. De behöver inte vänta på
premiären — de gör bara skada kvar.

```
cd ~/loven-stats-backend && git rm clean_bq.py del_bq.py fix_bq.py fix_bq2.py fix_seasons.py update_ha2526.py update_seasons.py && git commit -m "Ta bort engångsskript som skriver i prod" && git push origin master
```

## Hela städningen, efter premiären

```
cd ~/loven-stats-backend && git rm $(git ls-files '*.py' | grep -v / | grep -v '^backfill_season\.py$') $(git ls-files | grep -v / | grep -E '\.(json|txt|html)$' | grep -v package) && git commit -m "Rensa rotkatalogen: 127 filer utan körväg" && git push origin master
```

Historiken finns kvar i git, så ingenting går förlorat.

## Så att det inte kommer tillbaka

Roten saknar skydd mot nästa sediment. Lägg i `.gitignore`:

```
/test_*.py
/check_*.py
/find_*.py
/fix_*.py
/patch*.py
/trace_*.py
/probe_*.py
/scratch_*.py
/tmp_*.json
/out.txt
```

Ett `scratch/`-katalog som ignoreras i sin helhet är ett alternativ — då finns
det ett självklart ställe att slänga en engångsfil, vilket är varför de hamnade
i roten från början.

## Kör om kontrollen

```
cd ~/loven-stats-backend && python3 -c "import subprocess,re; rot=[f for f in subprocess.run(['git','ls-files','*.py'],capture_output=True,text=True).stdout.split() if '/' not in f]; andra=[f for f in subprocess.run(['git','ls-files'],capture_output=True,text=True).stdout.split() if f not in rot]; txt=''.join(open(f,encoding='utf-8',errors='replace').read() for f in andra if not f.endswith('.json')); print('\n'.join(f for f in rot if f in txt))"
```

Skriver ut de rotskript som faktiskt nämns vid namn någon annanstans. Är
utskriften tom eller bara `backfill_season.py` gäller listan ovan fortfarande.

## Ingår inte här

Tre större poster, som är refaktorering och inte städning:

- `api/main.py` är 6 144 rader med 28 endpoints i en fil.
- `dbt/` har 28 modeller men ingen körväg; produktionen läser `sql/`
  (2 filer) via `bq query`. Ett av spåren bör väljas.
- 63 mojibake-träffar i `api/main.py` (`Ã¶` för `ö`) från en
  dubbelkodning, i strängar som når svaren.
