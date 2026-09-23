"""Spelschemat som iCalendar-flöde.

Kalenderappar hämtar adressen själva med jämna mellanrum, så en flyttad match
följer med utan att någon gör något. Varje match har ett UID som bara bygger
på lagen och säsongen, inte på datumet: flyttas matchen ändras tiden i samma
händelse i stället för att en ny läggs till bredvid den gamla.

Tiderna i schemat är svensk lokal tid och skrivs med TZID. En tid i UTC hade
räknats om fel av appar som cachar flödet över sommartidsbytet.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

MATCHLANGD = timedelta(hours=2, minutes=30)

# Europe/Stockholm, som Outlook kräver för att förstå TZID.
_VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Stockholm",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0100",
    "TZOFFSETTO:+0200",
    "TZNAME:CEST",
    "DTSTART:19700329T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0100",
    "TZNAME:CET",
    "DTSTART:19701025T030000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def _text(v: str) -> str:
    """Escapning enligt RFC 5545."""
    return (str(v).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _vik(rad: str) -> list[str]:
    """Rader över 75 byte viks, med ett mellanslag först på fortsättningen."""
    ut, b = [], rad.encode("utf-8")
    while len(b) > 75:
        cut = 75
        # Inte mitt i ett flerbytestecken.
        while cut > 0 and (b[cut] & 0xC0) == 0x80:
            cut -= 1
        ut.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    ut.append(b.decode("utf-8"))
    return ut


def _uid(sasong: str, hemma: str, borta: str, nr: int) -> str:
    nyckel = f"{sasong}|{hemma}|{borta}|{nr}"
    return hashlib.sha1(nyckel.encode("utf-8")).hexdigest()[:20] + "@sida377.se"


def _kort(lag: str) -> str:
    return re.sub(r"^IF\s+", "", str(lag or "")).strip()


def bygg(matcher: list[dict], sasong: str, namn: str, nu: datetime) -> str:
    """matcher: schemats rader för våra matcher, sorterade på datum."""
    rader = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sida 377//Björklöven//SV",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_text('Björklöven ' + namn)}",
        "X-WR-TIMEZONE:Europe/Stockholm",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        *_VTIMEZONE,
    ]
    stamp = nu.strftime("%Y%m%dT%H%M%SZ")
    # Två möten på samma arena under säsongen: ordningsnumret skiljer dem.
    moten: dict[tuple[str, str], int] = {}
    for m in matcher:
        hemma, borta = str(m.get("home_team") or ""), str(m.get("away_team") or "")
        dag = str(m.get("match_date") or "")[:10]
        if not hemma or not borta or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dag):
            continue
        nr = moten.get((hemma, borta), 0) + 1
        moten[(hemma, borta)] = nr

        titel = f"{_kort(hemma)} – {_kort(borta)}"
        resultat = str(m.get("result") or "").replace(" ", "")
        if re.fullmatch(r"\d+-\d+", resultat):
            titel += f" {resultat.replace('-', '–')}"

        rader += ["BEGIN:VEVENT", f"UID:{_uid(sasong, hemma, borta, nr)}", f"DTSTAMP:{stamp}"]
        tid = str(m.get("match_time") or "")
        if re.fullmatch(r"\d{1,2}:\d{2}", tid):
            start = datetime.fromisoformat(f"{dag}T{tid.zfill(5)}")
            slut = start + MATCHLANGD
            rader += [
                f"DTSTART;TZID=Europe/Stockholm:{start:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=Europe/Stockholm:{slut:%Y%m%dT%H%M%S}",
            ]
        else:
            d = date.fromisoformat(dag)
            rader += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        rader.append(f"SUMMARY:{_text(titel)}")
        if m.get("venue"):
            rader.append(f"LOCATION:{_text(m['venue'])}")
        rader += ["URL:https://sida377.se/matcher", "TRANSP:TRANSPARENT", "END:VEVENT"]
    rader.append("END:VCALENDAR")
    return "\r\n".join(r for rad in rader for r in _vik(rad)) + "\r\n"
