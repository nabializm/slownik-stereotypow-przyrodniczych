#!/usr/bin/env python3
"""Graf etymologiczny – rdzeń „detektywistycznej maszyny wyrazów”.

PO CO TO JEST
-------------
Chcemy śledzić, którymi „kanałami” wyrazy przechodziły między językami:
od praindoeuropejskiego i praslawskiego, przez staropolski, aż po polszczyznę
dzisiejszą, a dalej przez kaszubski, śląski, czeski, sąsiadów Polski i kolejne
kręgi (Skandynawia, Anglia, Francja…). Do tego potrzebna jest struktura, w której
każdy wyraz w każdym języku jest osobnym punktem, a związki między nimi – osobnymi,
opisanymi połączeniami.

DLACZEGO GRAF W SQLITE, A NIE TEKST OD MODELU
--------------------------------------------
1. Etymologię bardzo łatwo „wymyślić” na podstawie podobnego brzmienia. Dlatego
   każde połączenie MUSI mieć źródło i stopień pewności. Model językowy nie jest tu
   źródłem wiedzy – co najwyżej pomaga czytać i porządkować źródła.
2. Graf pozwala zadawać pytania, których nie zadamy tekstowi: „jakie są wszystkie
   przodki wyrazu X?”, „które wyrazy w innych językach łączy ten sam przodek?”,
   „co w ogóle pochodzi od pnia *Y?” – i śledzić, skąd wiemy to, co wiemy.
3. SQLite to jeden plik, bez serwera, z gwarancjami spójności (klucze obce,
   ograniczenia CHECK). Łatwo go kopiować, wersjonować i otwierać zwykłymi
   narzędziami. Gdy projekt urośnie, schemat można przenieść do większej bazy.
4. Jest to baza ROZSZERZALNA ETAPAMI: kolumna `krag` w tabeli `jezyki` mówi, na
   którym „kręgu” leży język (0 = polski i jego przodkowie, 1 = kaszubski, śląski,
   czeski i bezpośredni sąsiedzi, 2 = dalszy bufor), więc zakres można poszerzać
   bez zmiany schematu.

CO JEST W BAZIE
---------------
- jezyki         – kody języków (zgodne z kodami Wikisłownika) i ich krąg,
- zrodla         – skąd pochodzi dana informacja (słownik, praca, Wikisłownik, wpis ręczny),
- wezly          – wyraz w danym języku (np. pl:lis, zlw-opl:lis, sla-pro:*lisъ),
- krawedzie      – połączenia: dziedziczenie, zapożyczenie, kalka, pochodzenie, kognat,
- poswiadczenia  – datowane użycia wyrazu (np. z Sstp), czyli „ślady” w tekstach.

STOPNIE PEWNOŚCI (od najmocniejszego)
-------------------------------------
naukowe      – słownik etymologiczny / praca naukowa,
wspolnotowe  – Wikisłownik, Wikidata (przydatne, ale redagowane przez wolontariuszy),
hipoteza     – proponowane, bez przekonującego dowodu,
sporne       – źródła się nie zgadzają,
model        – wyciągnięte przez model językowy, niezweryfikowane.
Pewność całego łańcucha = pewność jego najsłabszego ogniwa.

ZNANE OGRANICZENIA (świadomie, na start)
----------------------------------------
- Homonimy w obrębie jednego języka (np. dwa różne „zamek”) są na razie scalane
  w jeden węzeł. Połączenia zachowują jednak swoje uwagi i źródło, więc da się je
  później rozdzielić. Docelowo: numeracja homonimów.
- Nie porównujemy brzmienia automatycznie. Podobieństwo to nie dowód pokrewieństwa;
  połączenie powstaje tylko wtedy, gdy ma źródło.
- Pisownia NIE jest upraszczana (ł, ą, ъ pozostają), bo zlanie liter zlałoby
  różne wyrazy. Normalizujemy tylko wielkość liter, spacje i zapis Unicode.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Słowniki pojęć
# --------------------------------------------------------------------------

# Od najmocniejszego do najsłabszego; pewność łańcucha = min po tej skali.
PEWNOSCI = ("naukowe", "wspolnotowe", "hipoteza", "sporne", "model")
RANGA_PEWNOSCI = {nazwa: len(PEWNOSCI) - i for i, nazwa in enumerate(PEWNOSCI)}

# Kierunek krawędzi (poza kognatem): od = forma późniejsza, do = forma starsza/źródłowa.
TYPY_KRAWEDZI = ("dziedziczenie", "zapozyczenie", "kalka", "pochodzenie", "kognat")

# Nazwy szablonów Wikisłownika (wyjście Wiktextract) -> typ krawędzi.
SZABLONY_LANCUCHA = {
    "inh": "dziedziczenie", "inh+": "dziedziczenie",
    "bor": "zapozyczenie", "bor+": "zapozyczenie", "lbor": "zapozyczenie",
    "slbor": "zapozyczenie", "obor": "zapozyczenie", "ubor": "zapozyczenie",
    "cal": "kalka", "calque": "kalka", "pcal": "kalka", "psm": "kalka",
    "der": "pochodzenie", "der+": "pochodzenie",
}
SZABLONY_KOGNATOW = {"cog", "cognate"}

# Rozpoznawanie „przyrodniczości” na podstawie kategorii/tematów Wikisłownika.
# UWAGA: nazwy kategorii to założenia oparte na konwencji angielskiego Wikisłownika
# (np. „pl:Mammals”). Sprawdź je na własnym zrzucie i dopisz brakujące.
KATEGORIE_PRZYRODNICZE = {
    "animals": "zwierzę", "mammals": "zwierzę (ssak)", "birds": "zwierzę (ptak)",
    "fish": "zwierzę (ryba)", "insects": "zwierzę (owad)", "reptiles": "zwierzę (gad)",
    "amphibians": "zwierzę (płaz)", "plants": "roślina", "trees": "roślina (drzewo)",
    "flowers": "roślina (kwiat)", "herbs": "roślina (zioło)", "fungi": "grzyb",
    "mushrooms": "grzyb", "weather": "zjawisko atmosferyczne",
    "rivers": "rzeka", "mountains": "góra", "bodies of water": "zbiornik wodny",
    "landforms": "forma terenu", "minerals": "minerał",
}
TEMATY_PRZYRODNICZE = {
    "zoology": "zwierzę", "ornithology": "zwierzę (ptak)", "entomology": "zwierzę (owad)",
    "ichthyology": "zwierzę (ryba)", "botany": "roślina", "mycology": "grzyb",
    "meteorology": "zjawisko atmosferyczne", "hydrology": "woda", "geology": "geologia",
}

# Języki startowe. `krag`: 0 = polski i jego przodkowie, 1 = sąsiedzi i warstwy
# najbliższe, 2 = dalszy bufor. Nieznany kod zostanie dopisany automatycznie
# z kręgiem 2 i nazwą równą kodowi – do późniejszego uzupełnienia.
JEZYKI_STARTOWE = [
    ("pl", "polski", 0, None),
    ("zlw-opl", "staropolski", 0, "do ok. 1500 r.; źródło: Sstp"),
    ("zlw-mpl", "średniopolski", 0, None),
    ("sla-pro", "praslawski", 0, "rekonstrukcja"),
    ("ine-bsl-pro", "pra-bałtosłowiański", 0, "rekonstrukcja"),
    ("ine-pro", "praindoeuropejski", 0, "rekonstrukcja"),
    ("csb", "kaszubski", 1, None),
    ("szl", "śląski", 1, None),
    ("cs", "czeski", 1, None),
    ("zlw-ocs", "staroczeski", 1, None),
    ("sk", "słowacki", 1, None),
    ("de", "niemiecki", 1, None),
    ("gmh", "średnio-wysoko-niemiecki", 1, None),
    ("goh", "staro-wysoko-niemiecki", 1, None),
    ("uk", "ukraiński", 1, None),
    ("be", "białoruski", 1, None),
    ("ru", "rosyjski", 1, None),
    ("orv", "staroruski", 1, None),
    ("cu", "staro-cerkiewno-słowiański", 1, None),
    ("lt", "litewski", 1, None),
    ("lv", "łotewski", 1, None),
    ("sv", "szwedzki", 2, None),
    ("da", "duński", 2, None),
    ("no", "norweski", 2, None),
    ("non", "staronordyjski", 2, None),
    ("fi", "fiński", 2, None),
    ("en", "angielski", 2, None),
    ("ang", "staroangielski", 2, None),
    ("fr", "francuski", 2, None),
    ("fro", "starofrancuski", 2, None),
    ("la", "łacina", 2, None),
    ("grc", "starogrecki", 2, None),
    ("gem-pro", "pragermański", 2, "rekonstrukcja"),
]

ZRODLA_STARTOWE = [
    ("wikislownik", "Wikisłownik (przez Wiktextract / kaikki.org)", "wspolnotowe",
     "do sprawdzenia przed publikacją (Wikisłownik: CC BY-SA)", "https://kaikki.org/",
     "Treść redagowana przez wolontariuszy; traktuj jako trop do weryfikacji."),
    ("sstp", "Słownik staropolski (Sstp), IJP PAN", "naukowe",
     "do sprawdzenia", "https://pjs.ijppan.pl/sstp.html",
     "Poświadczenia staropolskie do ok. 1500 r."),
    ("wsjp", "Wielki słownik języka polskiego PAN", "naukowe",
     "do sprawdzenia", "https://wsjp.pl/", "Słownik współczesnej polszczyzny."),
    ("reczne", "Wpis ręczny redaktora", "hipoteza",
     None, None, "Używaj razem z polem `uwagi`, by podać prawdziwe źródło."),
    ("model", "Wyciągnięte przez model językowy", "model",
     None, None, "Niezweryfikowane; wymaga sprawdzenia w źródle."),
]


def _lista_sql(wartosci: tuple[str, ...]) -> str:
    return ", ".join(f"'{w}'" for w in wartosci)


SCHEMAT = f"""
CREATE TABLE IF NOT EXISTS jezyki (
    kod   TEXT PRIMARY KEY,
    nazwa TEXT NOT NULL,
    krag  INTEGER NOT NULL DEFAULT 2,
    uwagi TEXT
);
CREATE TABLE IF NOT EXISTS zrodla (
    id       INTEGER PRIMARY KEY,
    klucz    TEXT NOT NULL UNIQUE,
    tytul    TEXT NOT NULL,
    typ      TEXT NOT NULL CHECK (typ IN ({_lista_sql(PEWNOSCI)})),
    licencja TEXT,
    url      TEXT,
    uwagi    TEXT
);
CREATE TABLE IF NOT EXISTS wezly (
    id            INTEGER PRIMARY KEY,
    jezyk         TEXT NOT NULL REFERENCES jezyki(kod),
    forma         TEXT NOT NULL,
    forma_klucz   TEXT NOT NULL,
    czesc_mowy    TEXT NOT NULL DEFAULT '',
    znaczenie     TEXT,
    kategoria     TEXT,
    rekonstrukcja INTEGER NOT NULL DEFAULT 0,
    UNIQUE (jezyk, forma_klucz)
);
CREATE TABLE IF NOT EXISTS krawedzie (
    id        INTEGER PRIMARY KEY,
    od_wezla  INTEGER NOT NULL REFERENCES wezly(id),
    do_wezla  INTEGER NOT NULL REFERENCES wezly(id),
    typ       TEXT NOT NULL CHECK (typ IN ({_lista_sql(TYPY_KRAWEDZI)})),
    pewnosc   TEXT NOT NULL CHECK (pewnosc IN ({_lista_sql(PEWNOSCI)})),
    zrodlo_id INTEGER NOT NULL REFERENCES zrodla(id),
    uwagi     TEXT,
    UNIQUE (od_wezla, do_wezla, typ, zrodlo_id)
);
CREATE TABLE IF NOT EXISTS poswiadczenia (
    id          INTEGER PRIMARY KEY,
    wezel_id    INTEGER NOT NULL REFERENCES wezly(id),
    zrodlo_id   INTEGER NOT NULL REFERENCES zrodla(id),
    rok_od      INTEGER,
    rok_do      INTEGER,
    cytat       TEXT,
    lokalizacja TEXT
);
CREATE INDEX IF NOT EXISTS idx_krawedzie_od ON krawedzie(od_wezla);
CREATE INDEX IF NOT EXISTS idx_krawedzie_do ON krawedzie(do_wezla);
CREATE INDEX IF NOT EXISTS idx_wezly_klucz ON wezly(forma_klucz);
"""


# --------------------------------------------------------------------------
# Połączenie z bazą i podstawowe operacje
# --------------------------------------------------------------------------

def klucz_formy(forma: str) -> str:
    """Klucz porównawczy: Unicode NFC, małe litery, pojedyncze spacje.

    Celowo NIE usuwamy znaków diakrytycznych ani gwiazdki rekonstrukcji.
    """
    return " ".join(unicodedata.normalize("NFC", forma).casefold().split())


def otworz(sciezka: Path | str, *, utworz: bool = False) -> sqlite3.Connection:
    sciezka = Path(sciezka)
    if not sciezka.exists() and not utworz:
        raise FileNotFoundError(
            f"Brak bazy grafu: {sciezka}. Utwórz ją poleceniem graf-init."
        )
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(sciezka)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMAT)
    for kod, nazwa, krag, uwagi in JEZYKI_STARTOWE:
        con.execute(
            "INSERT OR IGNORE INTO jezyki (kod, nazwa, krag, uwagi) VALUES (?, ?, ?, ?)",
            (kod, nazwa, krag, uwagi),
        )
    for klucz, tytul, typ, licencja, url, uwagi in ZRODLA_STARTOWE:
        con.execute(
            "INSERT OR IGNORE INTO zrodla (klucz, tytul, typ, licencja, url, uwagi) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (klucz, tytul, typ, licencja, url, uwagi),
        )
    con.commit()
    return con


def zapewnij_jezyk(con: sqlite3.Connection, kod: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO jezyki (kod, nazwa, krag, uwagi) VALUES (?, ?, 2, ?)",
        (kod, kod, "dopisany automatycznie – uzupełnij nazwę i krąg"),
    )


def _id_zrodla(con: sqlite3.Connection, klucz: str) -> int:
    wiersz = con.execute("SELECT id FROM zrodla WHERE klucz = ?", (klucz,)).fetchone()
    if wiersz is None:
        znane = ", ".join(r["klucz"] for r in con.execute("SELECT klucz FROM zrodla"))
        raise KeyError(f"Nieznane źródło {klucz!r}. Znane źródła: {znane}")
    return int(wiersz["id"])


def zapewnij_wezel(
    con: sqlite3.Connection,
    jezyk: str,
    forma: str,
    *,
    czesc_mowy: str = "",
    znaczenie: str | None = None,
    kategoria: str | None = None,
) -> int:
    forma = forma.strip()
    if not forma:
        raise ValueError("Pusta forma wyrazu.")
    zapewnij_jezyk(con, jezyk)
    # Języki rekonstruowane (-pro) zapisujemy z gwiazdką, by odróżnić
    # formy poświadczone od odtworzonych.
    if jezyk.endswith("-pro") and not forma.startswith("*"):
        forma = "*" + forma
    rekonstrukcja = int(forma.startswith("*"))
    klucz = klucz_formy(forma)
    con.execute(
        "INSERT OR IGNORE INTO wezly (jezyk, forma, forma_klucz, rekonstrukcja) "
        "VALUES (?, ?, ?, ?)",
        (jezyk, forma, klucz, rekonstrukcja),
    )
    id_ = con.execute(
        "SELECT id FROM wezly WHERE jezyk = ? AND forma_klucz = ?", (jezyk, klucz)
    ).fetchone()["id"]
    # Uzupełniamy tylko puste pola – nigdy nie nadpisujemy wcześniejszej wiedzy.
    con.execute(
        "UPDATE wezly SET "
        "czesc_mowy = CASE WHEN czesc_mowy = '' THEN ? ELSE czesc_mowy END, "
        "znaczenie = COALESCE(znaczenie, ?), kategoria = COALESCE(kategoria, ?) "
        "WHERE id = ?",
        (czesc_mowy or "", znaczenie or None, kategoria or None, id_),
    )
    return int(id_)


def dodaj_krawedz(
    con: sqlite3.Connection,
    od_id: int,
    do_id: int,
    typ: str,
    pewnosc: str,
    zrodlo: str,
    uwagi: str | None = None,
) -> int | None:
    """Dodaje połączenie. Zwraca id lub None (duplikat / pętla)."""
    if typ not in TYPY_KRAWEDZI:
        raise ValueError(f"Nieznany typ krawędzi {typ!r}; dozwolone: {TYPY_KRAWEDZI}")
    if pewnosc not in PEWNOSCI:
        raise ValueError(f"Nieznana pewność {pewnosc!r}; dozwolone: {PEWNOSCI}")
    if od_id == do_id:
        return None
    if typ == "kognat":  # relacja symetryczna – zapisujemy w jednej, stałej kolejności
        od_id, do_id = sorted((od_id, do_id))
    kursor = con.execute(
        "INSERT OR IGNORE INTO krawedzie (od_wezla, do_wezla, typ, pewnosc, zrodlo_id, uwagi) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (od_id, do_id, typ, pewnosc, _id_zrodla(con, zrodlo), uwagi),
    )
    return kursor.lastrowid if kursor.rowcount else None


def dodaj_poswiadczenie(
    con: sqlite3.Connection,
    wezel_id: int,
    zrodlo: str,
    *,
    rok_od: int | None = None,
    rok_do: int | None = None,
    cytat: str | None = None,
    lokalizacja: str | None = None,
) -> int:
    kursor = con.execute(
        "INSERT INTO poswiadczenia (wezel_id, zrodlo_id, rok_od, rok_do, cytat, lokalizacja) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (wezel_id, _id_zrodla(con, zrodlo), rok_od, rok_do, cytat, lokalizacja),
    )
    return int(kursor.lastrowid)


def znajdz_wezel(con: sqlite3.Connection, jezyk: str, forma: str) -> sqlite3.Row | None:
    for kandydat in (forma, "*" + forma if jezyk.endswith("-pro") else forma):
        wiersz = con.execute(
            "SELECT * FROM wezly WHERE jezyk = ? AND forma_klucz = ?",
            (jezyk, klucz_formy(kandydat)),
        ).fetchone()
        if wiersz is not None:
            return wiersz
    return None


# --------------------------------------------------------------------------
# Zapytania: łańcuchy pochodzenia, kognaty, poświadczenia
# --------------------------------------------------------------------------

def _opis_wezla(con: sqlite3.Connection, id_: int) -> dict[str, Any]:
    w = con.execute("SELECT * FROM wezly WHERE id = ?", (id_,)).fetchone()
    return {
        "id": w["id"], "jezyk": w["jezyk"], "forma": w["forma"],
        "czesc_mowy": w["czesc_mowy"], "znaczenie": w["znaczenie"],
        "kategoria": w["kategoria"], "rekonstrukcja": bool(w["rekonstrukcja"]),
    }


def _przodkowie(
    con: sqlite3.Connection, id_: int, limit: int, odwiedzone: tuple[int, ...] = ()
) -> list[list[dict[str, Any]]]:
    """Wszystkie ścieżki od węzła w głąb, do przodków (bez kognatów, bez cykli)."""
    if limit <= 0:
        return [[]]
    wiersze = con.execute(
        "SELECT k.*, z.klucz AS zrodlo FROM krawedzie k "
        "JOIN zrodla z ON z.id = k.zrodlo_id "
        "WHERE k.od_wezla = ? AND k.typ != 'kognat' ORDER BY k.id",
        (id_,),
    ).fetchall()
    # Równoległe źródła tego samego połączenia (np. Sstp i Wikisłownik) scalamy
    # w jeden krok: pewność = najlepsze źródło, a lista źródeł zostaje widoczna.
    grupy: dict[tuple[int, str], dict[str, Any]] = {}
    for w in wiersze:
        if w["do_wezla"] in odwiedzone or w["do_wezla"] == id_:
            continue
        g = grupy.setdefault((w["do_wezla"], w["typ"]), {
            "do": w["do_wezla"], "typ": w["typ"], "pewnosc": w["pewnosc"],
            "zrodla": [], "uwagi": [],
        })
        if RANGA_PEWNOSCI[w["pewnosc"]] > RANGA_PEWNOSCI[g["pewnosc"]]:
            g["pewnosc"] = w["pewnosc"]
        g["zrodla"].append(w["zrodlo"])
        if w["uwagi"]:
            g["uwagi"].append(f"[{w['zrodlo']}] {w['uwagi']}")
    sciezki: list[list[dict[str, Any]]] = []
    for g in grupy.values():
        krok = {
            **_opis_wezla(con, g["do"]),
            "typ": g["typ"], "pewnosc": g["pewnosc"],
            "zrodlo": "+".join(g["zrodla"]), "uwagi": " / ".join(g["uwagi"]) or None,
        }
        for reszta in _przodkowie(con, g["do"], limit - 1, odwiedzone + (id_,)):
            sciezki.append([krok, *reszta])
    return sciezki or [[]]


def lancuchy_pochodzenia(
    con: sqlite3.Connection,
    jezyk: str,
    forma: str,
    *,
    max_glebokosc: int = 12,
    max_sciezek: int = 20,
) -> dict[str, Any] | None:
    """Zwraca pełny obraz wyrazu: przodkowie, kognaty i poświadczenia.

    None – gdy wyrazu nie ma w grafie (to informacja, nie błąd: brak danych
    nie znaczy, że wyraz nie ma etymologii).
    """
    w = znajdz_wezel(con, jezyk, forma)
    if w is None:
        return None
    sciezki = [s for s in _przodkowie(con, w["id"], max_glebokosc) if s][:max_sciezek]
    lancuchy = [
        {
            "pewnosc_lancucha": min((k["pewnosc"] for k in s), key=RANGA_PEWNOSCI.get),
            "kroki": s,
        }
        for s in sciezki
    ]
    id_w_lancuchu = {w["id"]} | {k["id"] for s in sciezki for k in s}

    kognaty: list[dict[str, Any]] = []
    poswiadczenia: list[dict[str, Any]] = []
    for id_ in sorted(id_w_lancuchu):
        for r in con.execute(
            "SELECT k.*, z.klucz AS zrodlo FROM krawedzie k "
            "JOIN zrodla z ON z.id = k.zrodlo_id "
            "WHERE k.typ = 'kognat' AND (k.od_wezla = ? OR k.do_wezla = ?)",
            (id_, id_),
        ):
            drugi = r["do_wezla"] if r["od_wezla"] == id_ else r["od_wezla"]
            kognaty.append(
                {**_opis_wezla(con, drugi), "przy_wezle": id_, "pewnosc": r["pewnosc"],
                 "zrodlo": r["zrodlo"], "uwagi": r["uwagi"]}
            )
        for r in con.execute(
            "SELECT p.*, z.klucz AS zrodlo FROM poswiadczenia p "
            "JOIN zrodla z ON z.id = p.zrodlo_id WHERE p.wezel_id = ? "
            "ORDER BY COALESCE(p.rok_od, 9999)",
            (id_,),
        ):
            opis = _opis_wezla(con, id_)
            poswiadczenia.append(
                {"jezyk": opis["jezyk"], "forma": opis["forma"], "rok_od": r["rok_od"],
                 "rok_do": r["rok_do"], "cytat": r["cytat"], "lokalizacja": r["lokalizacja"],
                 "zrodlo": r["zrodlo"]}
            )
    return {
        "wezel": _opis_wezla(con, w["id"]),
        "lancuchy": lancuchy,
        "kognaty": kognaty,
        "poswiadczenia": poswiadczenia,
    }


def formatuj_tekst(wynik: dict[str, Any]) -> str:
    """Czytelny zapis wyniku – także do wklejenia modelowi jako kontekst."""
    w = wynik["wezel"]
    linie = [f"{w['forma']} [{w['jezyk']}]" + (f" — {w['kategoria']}" if w["kategoria"] else "")]
    if not wynik["lancuchy"]:
        linie.append("  (w grafie brak zapisanych przodków tego wyrazu)")
    for i, l in enumerate(wynik["lancuchy"], 1):
        linie.append(f"  Łańcuch {i} (pewność łańcucha: {l['pewnosc_lancucha']}):")
        czesci = [f"{w['forma']} [{w['jezyk']}]"]
        for k in l["kroki"]:
            czesci.append(f"←({k['typ']}; {k['pewnosc']}; {k['zrodlo']})← {k['forma']} [{k['jezyk']}]")
        linie.append("    " + " ".join(czesci))
    if wynik["kognaty"]:
        linie.append("  Kognaty (punkt pokrewieństwa podany tak, jak w źródle):")
        for k in wynik["kognaty"]:
            linie.append(f"    {k['forma']} [{k['jezyk']}] ({k['pewnosc']}; {k['zrodlo']})")
    if wynik["poswiadczenia"]:
        linie.append("  Poświadczenia:")
        for p in wynik["poswiadczenia"]:
            lata = "?" if p["rok_od"] is None else (
                str(p["rok_od"]) if p["rok_do"] in (None, p["rok_od"]) else f"{p['rok_od']}–{p['rok_do']}"
            )
            cytat = f" „{p['cytat']}”" if p["cytat"] else ""
            linie.append(f"    {p['forma']} [{p['jezyk']}], {lata} [{p['zrodlo']}]{cytat}")
    return "\n".join(linie)


# --------------------------------------------------------------------------
# Import z Wiktextract (JSONL z kaikki.org)
# --------------------------------------------------------------------------
# DLACZEGO PLIK, A NIE ŻYWE ZAPYTANIA DO SIECI:
# - powtarzalność: ten sam zrzut daje ten sam wynik, można go cytować w pracy,
# - uprzejmość wobec serwisów: nie obciążamy ich tysiącami zapytań,
# - niezależność od tego, czy dany serwis pozwala na automatyczny dostęp.

def rozpoznaj_przyrode(wpis: dict[str, Any]) -> str | None:
    """Zwraca etykietę kategorii przyrodniczej lub None."""
    for sens in wpis.get("senses", []) or []:
        for kat in sens.get("categories", []) or []:
            nazwa = kat.get("name", "") if isinstance(kat, dict) else str(kat)
            nazwa = nazwa.split(":", 1)[-1].strip().casefold()
            if nazwa in KATEGORIE_PRZYRODNICZE:
                return KATEGORIE_PRZYRODNICZE[nazwa]
        for temat in sens.get("topics", []) or []:
            if str(temat).casefold() in TEMATY_PRZYRODNICZE:
                return TEMATY_PRZYRODNICZE[str(temat).casefold()]
    return None


def _kroki_z_szablonow(wpis: dict[str, Any]) -> list[tuple[str, str, str, str, str, str | None]]:
    """(jezyk_od, forma_od, jezyk_do, forma_do, typ, glosa) z szablonów etymologicznych.

    Łańcuch odtwarzamy z KOLEJNOŚCI szablonów: pierwszy opisuje pochodzenie hasła,
    następny – pochodzenie jego źródła itd. Dla etymologii rozgałęzionych
    („z X lub Y”) to uproszczenie, dlatego krawędzie dostają odpowiednią uwagę.
    Kognaty łączymy z hasłem głównym.
    """
    jezyk, slowo = wpis["lang_code"], wpis["word"]
    biezacy = (jezyk, slowo)
    kroki: list[tuple[str, str, str, str, str, str | None]] = []
    for szablon in wpis.get("etymology_templates", []) or []:
        nazwa, arg = szablon.get("name"), szablon.get("args", {}) or {}
        if nazwa in SZABLONY_LANCUCHA:
            cel, zrodlo_jez = arg.get("1"), arg.get("2")
            term = (arg.get("3") or "").strip()
            if not cel or not zrodlo_jez or term in ("", "-"):
                continue
            if cel == biezacy[0]:
                od = biezacy
            elif cel == jezyk:
                od = (jezyk, slowo)
            else:
                continue
            glosa = arg.get("t") or arg.get("gloss") or arg.get("5")
            kroki.append((od[0], od[1], zrodlo_jez, term, SZABLONY_LANCUCHA[nazwa], glosa))
            biezacy = (zrodlo_jez, term)
        elif nazwa in SZABLONY_KOGNATOW:
            jez_k, term = arg.get("1"), (arg.get("2") or "").strip()
            if jez_k and term and term != "-":
                kroki.append((jezyk, slowo, jez_k, term, "kognat", arg.get("t") or arg.get("gloss")))
    return kroki


def importuj_wiktextract(
    con: sqlite3.Connection,
    plik: Path | str,
    *,
    jezyk: str,
    slowa: list[str] | None = None,
    tylko_przyroda: bool = False,
    kategoria: str | None = None,
    zrodlo: str = "wikislownik",
) -> Counter:
    """Importuje hasła jednego języka z pliku JSONL (Wiktextract).

    Wymagamy filtra (lista słów lub rozpoznanie przyrody), żeby przypadkiem nie
    wczytać całego słownika – graf ma być celowo zbudowany, nie „zalany”.
    """
    if not slowa and not tylko_przyroda:
        raise ValueError("Podaj listę słów (--slowa/--plik-slow) albo --tylko-przyroda.")
    klucze = {klucz_formy(s) for s in slowa} if slowa else None
    stat: Counter = Counter()
    with con, open(plik, encoding="utf-8") as f:
        for linia in f:
            linia = linia.strip()
            if not linia:
                continue
            try:
                wpis = json.loads(linia)
            except json.JSONDecodeError:
                stat["bledne_linie"] += 1
                continue
            if wpis.get("lang_code") != jezyk or not wpis.get("word"):
                continue
            if klucze is not None and klucz_formy(wpis["word"]) not in klucze:
                continue
            kat = rozpoznaj_przyrode(wpis)
            if tylko_przyroda and kat is None:
                stat["pominiete_nieprzyrodnicze"] += 1
                continue
            glosa0 = None
            for sens in wpis.get("senses", []) or []:
                if sens.get("glosses"):
                    glosa0 = sens["glosses"][0]
                    break
            korzen = zapewnij_wezel(
                con, jezyk, wpis["word"], czesc_mowy=wpis.get("pos", "") or "",
                znaczenie=glosa0, kategoria=kat or kategoria,
            )
            stat["hasla"] += 1
            tekst = (wpis.get("etymology_text") or "").strip().replace("\n", " ")[:400]
            numer = wpis.get("etymology_number")
            for jo, fo, jd, fd, typ, glosa in _kroki_z_szablonow(wpis):
                id_od = zapewnij_wezel(con, jo, fo) if (jo, fo) != (jezyk, wpis["word"]) else korzen
                id_do = zapewnij_wezel(con, jd, fd, znaczenie=glosa)
                uwagi = "Łańcuch odtworzony z kolejności szablonów; zweryfikuj w haśle źródłowym."
                if numer is not None:
                    uwagi += f" (etymologia nr {numer})"
                if tekst:
                    uwagi += f" Tekst źródłowy: {tekst}"
                if dodaj_krawedz(con, id_od, id_do, typ, "wspolnotowe", zrodlo, uwagi):
                    stat["krawedzie_" + typ] += 1
    return stat
