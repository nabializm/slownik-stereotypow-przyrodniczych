#!/usr/bin/env python3
"""Słownik stereotypów językowych nazw przyrodniczych + graf etymologiczny.

CO TO JEST
----------
Dwie współpracujące części:
1. `stereotypy`  – generator haseł o stereotypach językowych (jak dotąd, przez model),
2. `graf-*`, `importuj-wiktextract`, `etymologia` – baza etymologiczna (SQLite),
   w której każde połączenie między wyrazami ma źródło i stopień pewności
   (szczegóły i uzasadnienie: graf_etymologiczny.py oraz README.md).

DLACZEGO TAK POŁĄCZONE
----------------------
Model nie powinien sam „pamiętać” etymologii – łatwo ją zmyśla. Dlatego:
- fakty etymologiczne pochodzą z grafu (ze źródłem i pewnością),
- model dostaje je jako kontekst i ma zakaz dodawania własnych,
- dane z grafu trafiają do hasła BEZPOŚREDNIO z bazy (pole `etymologia_z_grafu`),
  a nie przez „przepisanie” modelu – więc nie mogą zostać po drodze zniekształcone.

STARE UŻYCIE DZIAŁA BEZ ZMIAN
-----------------------------
  python slownik_stereotypow_przyrodniczych.py "Odra (rzeka)" lis sowa
  python slownik_stereotypow_przyrodniczych.py --csv slownik.csv "dąb" "burza"

NOWE POLECENIA
--------------
  graf-init               utworzenie bazy grafu (domyślnie etymologia.sqlite)
  importuj-wiktextract    import z pliku JSONL (kaikki.org) dla wskazanych słów
  dodaj-krawedz           ręczne dodanie połączenia ze źródłem i pewnością
  dodaj-poswiadczenie     ręczne dodanie datowanego poświadczenia (np. z Sstp)
  etymologia              wypisanie łańcuchów pochodzenia wyrazu
  stereotypy --graf ...   generowanie haseł z kontekstem etymologicznym z grafu

Wymaga dla `stereotypy`: pip install openai oraz zmienna OPENAI_API_KEY.
Model można zmienić zmienną OPENAI_MODEL (domyślnie: gpt-4.1-mini).
Pozostałe polecenia używają wyłącznie biblioteki standardowej.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import graf_etymologiczny as graf

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DOMYSLNY_GRAF = "etymologia.sqlite"
POLECENIA = {
    "stereotypy", "graf-init", "importuj-wiktextract",
    "dodaj-krawedz", "dodaj-poswiadczenie", "etymologia",
}

# Strict JSON Schema: model ma zwrócić strukturę, którą program może zapisać
# do bazy i później przetwarzać, zamiast jedynie generować swobodny opis.
# Etymologii celowo tu NIE ma – pochodzi z grafu, nie od modelu.
ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "haslo": {"type": "string"},
        "kategoria": {"type": "string"},
        "identyfikacja": {"type": "string"},
        "stereotypowe_skojarzenia": {"type": "array", "items": {"type": "string"}},
        "konotacje": {
            "type": "object",
            "properties": {
                "pozytywne": {"type": "array", "items": {"type": "string"}},
                "negatywne": {"type": "array", "items": {"type": "string"}},
                "neutralne": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pozytywne", "negatywne", "neutralne"],
            "additionalProperties": False,
        },
        "frazeologia": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "wyrazenie": {"type": "string"},
                    "znaczenie": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["utrwalone", "wymaga_weryfikacji"],
                    },
                },
                "required": ["wyrazenie", "znaczenie", "status"],
                "additionalProperties": False,
            },
        },
        "przyklady_uzycia": {"type": "array", "items": {"type": "string"}},
        "interpretacja_kulturowa": {"type": "string"},
        "uwagi_metodologiczne": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "haslo",
        "kategoria",
        "identyfikacja",
        "stereotypowe_skojarzenia",
        "konotacje",
        "frazeologia",
        "przyklady_uzycia",
        "interpretacja_kulturowa",
        "uwagi_metodologiczne",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Jesteś redaktorem polskiego słownika stereotypów językowych nazw przyrodniczych.
Opracowujesz hasła dla nazw zwierząt, roślin, grzybów, rzek, gór, zjawisk pogodowych,
elementów krajobrazu i innych obiektów przyrodniczych.

Zasady:
1. Pisz po polsku i zachowuj rozróżnienie między stereotypem językowym, skojarzeniem
   kulturowym i rzeczywistą cechą biologiczną/geograficzną.
2. Nie przedstawiaj skojarzeń jako faktów naukowych o obiekcie.
3. Nie wymyślaj przysłów ani frazeologizmów. W polu frazeologia wpisuj tylko wyrażenia,
   które są rzeczywiście utrwalone według Twojej wiedzy. Gdy masz wątpliwość, oznacz
   status jako 'wymaga_weryfikacji'. Jeśli brak odpowiedniego przykładu, zwróć pustą listę.
4. Nie wymyślaj źródeł, cytatów, częstotliwości korpusowych ani adresów internetowych.
5. Jeśli wyraz jest wieloznaczny, wykorzystaj doprecyzowanie w nawiasie, a gdy go brak,
   opisz najbardziej prawdopodobne znaczenie i zaznacz niejednoznaczność w uwagach.
6. Dla nazw własnych, takich jak Odra (rzeka), unikaj przypisywania im jednego uniwersalnego
   stereotypu; uwzględnij kontekst geograficzny, historyczny i kulturowy.
7. W przykładowych zdaniach nie sugeruj, że są to cytaty z rzeczywistych źródeł.
8. Bądź ostrożny: jeśli brak powszechnego stereotypu, powiedz to wprost.
9. Etymologia: nie podawaj własnych informacji o pochodzeniu wyrazu. Jeśli w poleceniu
   znajdują się „Dane etymologiczne z bazy”, możesz się do nich odwołać (np. przy
   interpretacji kulturowej), ale niczego do nich nie dodawaj ani nie zmieniaj; zwracaj
   uwagę na podany stopień pewności. Jeśli danych brak, nie spekuluj o etymologii.
"""


def normalizuj(tekst: str) -> str:
    """Ujednolica zapis hasła do porównywania wpisów w bazie."""
    return " ".join(tekst.casefold().split())


def wczytaj_baze(sciezka: Path) -> list[dict[str, Any]]:
    if not sciezka.exists():
        return []
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Nie można odczytać bazy {sciezka}: {exc}") from exc
    if not isinstance(dane, list):
        raise RuntimeError(f"Baza {sciezka} powinna zawierać listę haseł JSON.")
    return dane


def zapisz_baze(sciezka: Path, baza: list[dict[str, Any]]) -> None:
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    tymczasowa = sciezka.with_suffix(sciezka.suffix + ".tmp")
    tymczasowa.write_text(
        json.dumps(baza, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tymczasowa.replace(sciezka)


# --------------------------------------------------------------------------
# Most między hasłem a grafem
# --------------------------------------------------------------------------

def etymologia_z_grafu(
    sciezka_grafu: Path | None, haslo: str, jezyk: str
) -> dict[str, Any] | None:
    """Szuka wyrazu w grafie. Z hasła 'Odra (rzeka)' bierze część 'Odra'."""
    if sciezka_grafu is None or not sciezka_grafu.exists():
        return None
    podstawa = haslo.split("(")[0].strip()
    con = graf.otworz(sciezka_grafu)
    try:
        return graf.lancuchy_pochodzenia(con, jezyk, podstawa)
    finally:
        con.close()


def wygeneruj_haslo(
    client: Any, haslo: str, model: str, kontekst_etymologiczny: str | None = None
) -> dict[str, Any]:
    instrukcja = (
        f"Opracuj hasło słownikowe dla nazwy: {haslo!r}. "
        "W polu 'identyfikacja' krótko wyjaśnij, do czego odnosi się nazwa. "
        "W polu 'stereotypowe_skojarzenia' uwzględnij tylko istotne skojarzenia językowe "
        "lub kulturowe, a nie dowolne cechy obiektu. W 'uwagi_metodologiczne' zaznacz, "
        "co wymaga sprawdzenia w słownikach, korpusach lub źródłach historycznych. "
        "Jeżeli nie ma podstaw do wskazania stereotypu, powiedz o tym jasno."
    )
    if kontekst_etymologiczny:
        instrukcja += (
            "\n\nDane etymologiczne z bazy (jedyne dozwolone informacje o pochodzeniu "
            "wyrazu; stopnie pewności: naukowe > wspolnotowe > hipoteza > sporne > model):\n"
            + kontekst_etymologiczny
        )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instrukcja},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "haslo_stereotypu_przyrodniczego",
                "strict": True,
                "schema": ENTRY_SCHEMA,
            }
        },
    )
    tekst = response.output_text
    if not tekst:
        raise RuntimeError(f"Model nie zwrócił treści dla hasła {haslo!r}.")
    wynik = json.loads(tekst)
    # Zachowujemy dokładny zapis podany przez użytkownika jako hasło wejściowe.
    wynik["haslo"] = haslo
    wynik["metadane"] = {
        "wygenerowano_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "wymaga_weryfikacji_redakcyjnej": True,
        "uwaga": "Treść wygenerowana przez model; nie jest automatycznie potwierdzona korpusowo.",
    }
    return wynik


def eksportuj_csv(sciezka: Path, baza: list[dict[str, Any]]) -> None:
    """Eksportuje najważniejsze pola do tabeli CSV; pełne dane pozostają w JSON."""
    kolumny = [
        "haslo",
        "kategoria",
        "identyfikacja",
        "stereotypowe_skojarzenia",
        "konotacje_pozytywne",
        "konotacje_negatywne",
        "konotacje_neutralne",
        "frazeologia",
        "etymologia_z_grafu",
        "interpretacja_kulturowa",
        "uwagi_metodologiczne",
    ]
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    with sciezka.open("w", encoding="utf-8-sig", newline="") as plik:
        writer = csv.DictWriter(plik, fieldnames=kolumny)
        writer.writeheader()
        for wpis in baza:
            kon = wpis.get("konotacje", {})
            frazeologia = wpis.get("frazeologia", [])
            etym = wpis.get("etymologia_z_grafu")
            writer.writerow(
                {
                    "haslo": wpis.get("haslo", ""),
                    "kategoria": wpis.get("kategoria", ""),
                    "identyfikacja": wpis.get("identyfikacja", ""),
                    "stereotypowe_skojarzenia": "; ".join(wpis.get("stereotypowe_skojarzenia", [])),
                    "konotacje_pozytywne": "; ".join(kon.get("pozytywne", [])),
                    "konotacje_negatywne": "; ".join(kon.get("negatywne", [])),
                    "konotacje_neutralne": "; ".join(kon.get("neutralne", [])),
                    "frazeologia": "; ".join(
                        f"{f.get('wyrazenie', '')} — {f.get('znaczenie', '')} [{f.get('status', '')}]"
                        for f in frazeologia
                    ),
                    "etymologia_z_grafu": graf.formatuj_tekst(etym).replace("\n", " | ") if etym else "",
                    "interpretacja_kulturowa": wpis.get("interpretacja_kulturowa", ""),
                    "uwagi_metodologiczne": "; ".join(wpis.get("uwagi_metodologiczne", [])),
                }
            )


# --------------------------------------------------------------------------
# Polecenia
# --------------------------------------------------------------------------

def parsuj_wezel(tekst: str) -> tuple[str, str]:
    """'pl:lis' -> ('pl', 'lis'). Forma może zawierać dwukropek."""
    if ":" not in tekst:
        raise ValueError(f"Oczekiwano zapisu kod_języka:forma (np. pl:lis), jest: {tekst!r}")
    kod, forma = tekst.split(":", 1)
    return kod.strip(), forma.strip()


def cmd_stereotypy(args: argparse.Namespace) -> int:
    hasla = args.hasla
    if not hasla:
        odpowiedz = input("Podaj hasła oddzielone średnikami (np. Odra (rzeka); lis; sowa): ")
        hasla = [h.strip() for h in odpowiedz.split(";") if h.strip()]
    if not hasla:
        print("Nie podano żadnych haseł.", file=sys.stderr)
        return 2

    if OpenAI is None:
        print("Brakuje pakietu openai. Zainstaluj go poleceniem: pip install openai", file=sys.stderr)
        return 2
    if not os.getenv("OPENAI_API_KEY"):
        print(
            "Brakuje zmiennej OPENAI_API_KEY. Ustaw klucz API w zmiennych środowiskowych i uruchom program ponownie.",
            file=sys.stderr,
        )
        return 2

    sciezka_bazy = Path(args.baza)
    try:
        baza = wczytaj_baze(sciezka_bazy)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sciezka_grafu = Path(args.graf) if args.graf else None
    if sciezka_grafu is not None and not sciezka_grafu.exists():
        print(f"Uwaga: brak bazy grafu {sciezka_grafu}; hasła powstaną bez etymologii.", file=sys.stderr)

    client = OpenAI()
    indeks = {normalizuj(str(w.get("haslo", ""))): i for i, w in enumerate(baza)}

    for haslo in hasla:
        klucz = normalizuj(haslo)
        if klucz in indeks and not args.ponownie:
            print(f"[BAZA] {haslo} — hasło już istnieje; pomijam generowanie.")
            continue
        try:
            print(f"[GENEROWANIE] {haslo} ...")
            etym = etymologia_z_grafu(sciezka_grafu, haslo, args.jezyk)
            kontekst = graf.formatuj_tekst(etym) if etym else None
            wpis = wygeneruj_haslo(client, haslo, args.model, kontekst)
            if etym:
                # Dane z grafu wstawiamy sami – nie przechodzą przez model.
                wpis["etymologia_z_grafu"] = etym
            elif sciezka_grafu is not None and sciezka_grafu.exists():
                wpis["uwagi_metodologiczne"].append(
                    "Brak wyrazu w grafie etymologicznym – nie podano etymologii."
                )
            if klucz in indeks:
                baza[indeks[klucz]] = wpis
            else:
                baza.append(wpis)
                indeks[klucz] = len(baza) - 1
            zapisz_baze(sciezka_bazy, baza)
            print(f"[ZAPISANO] {haslo} -> {sciezka_bazy}")
        except Exception as exc:  # zapisujemy wcześniejsze hasła, jeśli jedno wywołanie się nie powiedzie
            print(f"[BŁĄD] Nie udało się opracować hasła {haslo!r}: {exc}", file=sys.stderr)

    if args.csv_path:
        eksportuj_csv(Path(args.csv_path), baza)
        print(f"[CSV] Eksport zapisany w: {args.csv_path}")

    print(f"Gotowe. Liczba haseł w bazie: {len(baza)}")
    return 0


def cmd_graf_init(args: argparse.Namespace) -> int:
    con = graf.otworz(args.graf, utworz=True)
    liczba = con.execute("SELECT COUNT(*) FROM jezyki").fetchone()[0]
    con.close()
    print(f"Baza grafu gotowa: {args.graf} (języków: {liczba}).")
    return 0


def cmd_importuj(args: argparse.Namespace) -> int:
    slowa = list(args.slowa or [])
    if args.plik_slow:
        tekst = Path(args.plik_slow).read_text(encoding="utf-8")
        slowa += [w.strip() for w in tekst.splitlines() if w.strip()]
    con = graf.otworz(args.graf, utworz=True)
    try:
        stat = graf.importuj_wiktextract(
            con, args.plik, jezyk=args.jezyk, slowa=slowa or None,
            tylko_przyroda=args.tylko_przyroda, kategoria=args.kategoria,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        con.close()
    for k, v in sorted(stat.items()):
        print(f"  {k}: {v}")
    if not stat.get("hasla"):
        print("Nie zaimportowano żadnego hasła – sprawdź kod języka i filtr.", file=sys.stderr)
    return 0


def cmd_dodaj_krawedz(args: argparse.Namespace) -> int:
    con = graf.otworz(args.graf, utworz=True)
    try:
        with con:
            jo, fo = parsuj_wezel(args.od)
            jd, fd = parsuj_wezel(args.do)
            id_od = graf.zapewnij_wezel(con, jo, fo)
            id_do = graf.zapewnij_wezel(con, jd, fd)
            wynik = graf.dodaj_krawedz(con, id_od, id_do, args.typ, args.pewnosc, args.zrodlo, args.uwagi)
    except (ValueError, KeyError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 2
    finally:
        con.close()
    print("Dodano połączenie." if wynik else "Takie połączenie już istnieje (lub jest pętlą).")
    return 0


def cmd_dodaj_poswiadczenie(args: argparse.Namespace) -> int:
    con = graf.otworz(args.graf, utworz=True)
    try:
        with con:
            j, f = parsuj_wezel(args.wezel)
            id_ = graf.zapewnij_wezel(con, j, f)
            graf.dodaj_poswiadczenie(
                con, id_, args.zrodlo, rok_od=args.rok_od, rok_do=args.rok_do,
                cytat=args.cytat, lokalizacja=args.lokalizacja,
            )
    except (ValueError, KeyError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 2
    finally:
        con.close()
    print("Dodano poświadczenie.")
    return 0


def cmd_etymologia(args: argparse.Namespace) -> int:
    try:
        con = graf.otworz(args.graf)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        wynik = graf.lancuchy_pochodzenia(con, args.jezyk, args.haslo, max_glebokosc=args.glebokosc)
    finally:
        con.close()
    if wynik is None:
        print(f"Brak wyrazu {args.haslo!r} [{args.jezyk}] w grafie. To brak danych, a nie dowód braku etymologii.")
        return 1
    print(json.dumps(wynik, ensure_ascii=False, indent=2) if args.json else graf.formatuj_tekst(wynik))
    return 0


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Słownik stereotypów językowych nazw przyrodniczych + graf etymologiczny."
    )
    pod = parser.add_subparsers(dest="polecenie", required=True)

    p = pod.add_parser("stereotypy", help="Generuje hasła słownika stereotypów (model + opcjonalnie graf).")
    p.add_argument("hasla", nargs="*", help="Hasła, np. 'Odra (rzeka)' lis sowa")
    p.add_argument("--baza", default="slownik_stereotypow.json",
                   help="Plik JSON z bazą (domyślnie: slownik_stereotypow.json)")
    p.add_argument("--csv", dest="csv_path", help="Opcjonalny plik CSV do eksportu")
    p.add_argument("--ponownie", action="store_true", help="Wygeneruj ponownie hasła, które już istnieją w bazie")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Model API (domyślnie: {DEFAULT_MODEL})")
    p.add_argument("--graf", help=f"Baza grafu SQLite z etymologią (np. {DOMYSLNY_GRAF})")
    p.add_argument("--jezyk", default="pl", help="Kod języka haseł w grafie (domyślnie: pl)")
    p.set_defaults(func=cmd_stereotypy)

    p = pod.add_parser("graf-init", help="Tworzy bazę grafu z językami i źródłami startowymi.")
    p.add_argument("--graf", default=DOMYSLNY_GRAF)
    p.set_defaults(func=cmd_graf_init)

    p = pod.add_parser("importuj-wiktextract", help="Import z pliku JSONL (kaikki.org) dla wybranych słów.")
    p.add_argument("plik", help="Plik .jsonl z Wiktextract")
    p.add_argument("--graf", default=DOMYSLNY_GRAF)
    p.add_argument("--jezyk", default="pl", help="Kod języka haseł do importu (domyślnie: pl)")
    p.add_argument("--slowa", nargs="*", help="Słowa do zaimportowania")
    p.add_argument("--plik-slow", help="Plik tekstowy: jedno słowo w linii")
    p.add_argument("--tylko-przyroda", action="store_true",
                   help="Importuj tylko hasła rozpoznane jako przyrodnicze (kategorie/tematy Wikisłownika)")
    p.add_argument("--kategoria", help="Etykieta kategorii dla importowanych haseł bez rozpoznanej kategorii")
    p.set_defaults(func=cmd_importuj)

    p = pod.add_parser("dodaj-krawedz", help="Ręcznie dodaje połączenie między wyrazami.")
    p.add_argument("--graf", default=DOMYSLNY_GRAF)
    p.add_argument("--od", required=True, help="Forma późniejsza, np. pl:lis")
    p.add_argument("--do", required=True, help="Forma starsza/źródłowa, np. zlw-opl:lis")
    p.add_argument("--typ", required=True, choices=graf.TYPY_KRAWEDZI)
    p.add_argument("--pewnosc", required=True, choices=graf.PEWNOSCI)
    p.add_argument("--zrodlo", required=True, help="Klucz źródła, np. sstp, wikislownik, reczne")
    p.add_argument("--uwagi", help="Opis, strona w źródle, zastrzeżenia")
    p.set_defaults(func=cmd_dodaj_krawedz)

    p = pod.add_parser("dodaj-poswiadczenie", help="Ręcznie dodaje datowane poświadczenie wyrazu.")
    p.add_argument("--graf", default=DOMYSLNY_GRAF)
    p.add_argument("--wezel", required=True, help="Np. zlw-opl:lis")
    p.add_argument("--zrodlo", required=True)
    p.add_argument("--rok-od", type=int)
    p.add_argument("--rok-do", type=int)
    p.add_argument("--cytat", help="Krótki cytat lub kontekst (zgodnie z prawem cytatu)")
    p.add_argument("--lokalizacja", help="Zabytek, strona, region")
    p.set_defaults(func=cmd_dodaj_poswiadczenie)

    p = pod.add_parser("etymologia", help="Wypisuje łańcuchy pochodzenia wyrazu.")
    p.add_argument("haslo", help="Wyraz, np. lis")
    p.add_argument("--graf", default=DOMYSLNY_GRAF)
    p.add_argument("--jezyk", default="pl")
    p.add_argument("--glebokosc", type=int, default=12)
    p.add_argument("--json", action="store_true", help="Wynik w formacie JSON")
    p.set_defaults(func=cmd_etymologia)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Zgodność wsteczna: stare wywołanie bez nazwy polecenia = `stereotypy`.
    if not argv or (argv[0] not in POLECENIA and argv[0] not in ("-h", "--help")):
        argv.insert(0, "stereotypy")
    args = zbuduj_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
