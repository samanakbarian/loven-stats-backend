# Löven Stats Hub - Backend & Data Pipeline

Detta repository innehåller backend- och data-infrastruktur för Löven Stats Hub.

## Arkitektur
- **`api/`**: FastAPI applikation (Cloud Run) som serverar data till frontend.
- **`functions/`**: Cloud Functions för att hämta rådata (t.ex. Sportradar) och spara i Cloud Storage.
- **`dbt/`**: dbt-projekt för att transformera data i BigQuery.

## Komma igång
För att utveckla lokalt:
1. `cd api`
2. `pip install -r requirements.txt`
3. `uvicorn main:app --reload`
