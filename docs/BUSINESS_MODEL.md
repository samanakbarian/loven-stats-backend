# 💰 Affärsmodell & Monetisering — Löven Stats Hub

*Senast uppdaterad: 2026-05-03*

> **Grundprincip:** Allt på plattformen är och förblir 100 % gratis för fansen. Alltid. Ingen betalvägg, inga premium-nivåer.

---

## Vad vi faktiskt bygger

Löven Stats Hub ser ut som en fan-sajt, men under huven bygger vi:

1. **En modern datapipeline** (Sportradar → GCS → BigQuery → dbt → API)
2. **Avancerad hockeyanalytik** (Corsi, Fenwick, xG, WAR — saker som inte finns för SHL idag)
3. **AI-integration** (Gemini för sentimentanalys, spelarvärderi, scouting reports)
4. **Realtidsinfrastruktur** (live-matcher, automatisk nyhetsklassificering)
5. **En lojal, engagerad publik** (Björklöven-supportrar som besöker dagligen under säsong)

Det är fem separata tillgångar som var och en kan monetiseras — utan att fansen betalar ett öre.

---

## Intäktsströmmar

### 1. 🏢 Lokal Sponsring (Enklast, snabbast)

**Vad:** "Native" sponsorytor integrerade i UI:t, inte banners.  
**Vem betalar:** Lokala Umeå-företag.  
**Varför de betalar:** Extremt riktad publik — 100 % Björklöven-supportrar i Umeå med omnejd.

| Sponsoryta | Placering | Exempel |
|-----------|-----------|---------|
| **"Presenteras av"** | Sidebar-botten (redan designad) | O'Learys Umeå |
| **"Matchens sponsor"** | Matchcenter header | Norrmejerier |
| **"Nyförvärvet"** | Silly Season — vid bekräftade signeringar | Umeå Energi |
| **Spelarprofil-sponsor** | "Spelarens säsong — presenteras av" | Rex Bar |
| **Dashboard-widget** | "Nästa match — boka bord på..." | Restauranger nära arenan |

**Prisnivå:** 2 000–10 000 kr/mån per sponsoryta. Liten kostnad för lokala företag, exakt rätt publik.  
**Potential:** 5 sponsorer × 5 000 kr = **25 000 kr/mån**.

> Den stora fördelen: Vårt mörka premium-UI gör att sponsorlogotyper ser mer exklusiva ut än på en vanlig sajt. Sponsorer får en "native"-känsla, inte en billig banner.

---

### 2. 📺 B2B-licensiering till Lokalmedia (Störst potential)

**Vad:** Embeddable widgets som lokalmedia kan integrera i sina artiklar.  
**Vem betalar:** VK (Västerbottens-Kuriren), Folkbladet, P4 Västerbotten, lokala podcasts.  
**Varför de betalar:** De har inte kapacitet att bygga hockeyanalytik själva. Våra widgets ger deras artiklar "wow-faktor".

| Widget | Beskrivning | Mediekontxt |
|--------|-------------|-------------|
| **Live Score Widget** | Embeddbar mini-scoreboard med period + klocka | "Följ matchen live" i VK-artiklar |
| **xG Match Chart** | Expected Goals-graf per match | Matchreferat: "Löven dominerade — xG 3.2 vs 1.1" |
| **Spelarjämförelse** | Jämför två spelare (radar chart med Corsi, xG, etc.) | Transferartiklar |
| **Silly Season Feed** | Embeddbart ryktesflöde med källhänvisning | Transfernyheter |
| **Tabellwidget** | Live SHL-tabell med Löven-markering | Alla matchartiklar |

**Prisnivå:** 3 000–15 000 kr/mån beroende på mediahusets storlek.  
**Potential:** VK + Folkbladet + 2 podcasts = **30 000–50 000 kr/mån**.

> Vi äger datan och analysen. Media köper *tillgång*, inte data. Fansen ser fortfarande allt gratis på vår sajt.

---

### 3. 🏒 White-Label / SaaS för Andra Lag (Skalbarhet)

**Vad:** Samma plattform, men för andra SHL/HA-lag.  
**Vem betalar:** Supporterföreningar, fansajter eller lagen själva.  
**Varför de betalar:** Ingen annan levererar xG, Corsi och AI-driven analys för svensk hockey.

**Hur det funkar:**
- Vår datapipeline hämtar redan data för ALLA SHL-lag (Sportradar levererar hela ligan)
- Frontend 2.0 kan enkelt "re-skinnas" med andra lagfärger och logotyper
- dbt-modellerna är redan multi-team (filter på `team_id`)

| Steg | Insats | Pris |
|------|--------|------|
| **Starter** | Dashboard + Matchcenter + Standings | 1 500 kr/mån |
| **Pro** | + Silly Season + xG + Spelaranalys | 3 500 kr/mån |
| **Enterprise** | + API-access + Custom widgets | 8 000 kr/mån |

**Potential:** 5 lag × 3 500 kr = **17 500 kr/mån** (redan med bara Pro).  
**Om alla 14 SHL-lag:** 14 × 5 000 kr = **70 000 kr/mån**.

> Detta är den riktiga guldgruvan. Vi bygger för Björklöven, men arkitekturen är lag-agnostisk.

---

### 4. 📊 Data/Analytik-API (Nischat, högt värde)

**Vad:** REST API med avancerade hockey-metriker.  
**Vem betalar:** Fantasy hockey-plattformar, bettingbolag, scouting-tjänster, sportsredaktioner.  
**Varför de betalar:** xG, WAR och AI scouting reports finns inte för svensk hockey idag.

| Endpoint | Data | Köpare |
|----------|------|--------|
| `/api/v1/xg/{match_id}` | xG per skott, per match | Media, betting |
| `/api/v1/player/{id}/advanced` | Corsi, Fenwick, xG, WAR | Fantasy, scouting |
| `/api/v1/ai/scouting/{player_id}` | AI-genererad scoutingrapport | Agenter, klubbar |
| `/api/v1/sentiment/feed` | AI-analyserat nyhetsflöde med sentiment | Betting, media |

**Prisnivå:** 5 000–25 000 kr/mån beroende på volym och exklusivitet.

> Fansen ser resultaten gratis. Betalande kunder får rå API-access för sina egna produkter.

---

### 5. 🔗 Affiliate & Partnergenererade Intäkter (Passiva)

| Typ | Partner | Placering |
|-----|---------|-----------|
| **Biljettförsäljning** | SHL.se, Ticketmaster | "Nästa match"-widget: "Köp biljetter →" |
| **Restaurangbokning** | Lokala krogar | "Förfest? Boka bord på O'Learys →" |
| **Merchandise** | Björklövens shop | "Nyförvärv? Skaffa #91 Wallmark-tröjan →" |
| **Streaming** | Viaplay / C More | "Se matchen live →" |

**Provision:** 5–15 % per konvertering.  
**Potential:** 5 000–15 000 kr/mån under säsong.

---

## Kostnadsbild (Vad det kostar oss)

| Post | Kostnad/mån | Kommentar |
|------|-------------|-----------|
| GCP (BigQuery + Cloud Run + GCS + Functions) | ~200–500 kr | Serverlöst = betala per användning |
| Sportradar API (betald) | ~3 000–8 000 kr? | Beroende på plan |
| EliteProspects API | ~1 000–3 000 kr? | Årsavgift / 12 |
| Firebase Hosting | ~0–100 kr | Generöst gratislager |
| Domännamn | ~100 kr | .se-domän |
| **Total** | **~4 500–12 000 kr/mån** | |

---

## Prioritering

| Intäktsström | Tid till intäkt | Uppskattat/mån | Svårighetsgrad |
|-------------|-----------------|----------------|----------------|
| **1. Lokal sponsring** | 1–2 månader | 15 000–25 000 kr | ⭐ Lätt |
| **2. Media-widgets** | 3–4 månader | 30 000–50 000 kr | ⭐⭐ Medel |
| **3. Affiliate** | 1 månad | 5 000–15 000 kr | ⭐ Lätt |
| **4. White-label** | 6+ månader | 20 000–70 000 kr | ⭐⭐⭐ Svårt |
| **5. Data-API** | 6+ månader | 10 000–25 000 kr | ⭐⭐ Medel |

**Break-even:** Med 2 lokala sponsorer + VK-widget-licens täcker vi alla driftskostnader.

---

## Sammanfattning

```
Fansen betalar: 0 kr. Alltid.

Intäktskällor:
  Lokala sponsorer  → de vill nå Björklöven-fans
  Lokalmedia        → de vill ha våra widgets och analytics
  Andra lag         → de vill ha samma plattform
  Data-API          → de vill ha xG/WAR-data
  Affiliate         → provision på biljetter/merch/mat
  
Moat (konkurrensfördelar):
  ✅ Ingen annan levererar xG/Corsi för svensk hockey
  ✅ AI-driven analys (Gemini) som ingen fan-sajt har
  ✅ Data warehouse-arkitektur som skalar till alla lag
  ✅ Lojal nischpublik (Björklöven-fansen)
```
