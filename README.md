# Słownik stereotypów przyrodniczych i graf etymologiczny

Projekt łączy dwa elementy: słownik stereotypów językowych nazw przyrodniczych oraz graf etymologiczny w SQLite. Graf przechowuje formy językowe, relacje, źródła, stopień pewności i poświadczenia. Model językowy może wspierać opracowanie haseł słownika, ale nie powinien być traktowany jako dowód etymologiczny.

## Wymagania

- Python 3.11 lub nowszy
- klucz API OpenAI tylko wtedy, gdy uruchamiasz generator haseł
- połączenie internetowe potrzebne do instalacji zależności i wywołań API; sama baza SQLite działa lokalnie

## Uruchomienie lokalne

```bash
git clone https://github.com/TWOJ_LOGIN/TWOJE_REPO.git
cd TWOJE_REPO
python -m venv .venv
```

Aktywuj środowisko:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Zainstaluj pakiety:

```bash
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Sprawdź polecenia:

```bash
python slownik_stereotypow_przyrodniczych.py --help
python -m pytest -v
```

## Graf etymologiczny

Utwórz bazę:

```bash
python slownik_stereotypow_przyrodniczych.py graf-init
```

Dodaj relację ręcznie, zawsze wskazując źródło i pewność:

```bash
python slownik_stereotypow_przyrodniczych.py dodaj-krawedz --od pl:lis --do zlw-opl:lis --typ dziedziczenie --pewnosc naukowe --zrodlo sstp --uwagi "Przykładowy wpis do uzupełnienia zweryfikowanym źródłem"
python slownik_stereotypow_przyrodniczych.py etymologia lis
```

Przykład importu z Wiktextract (pobierz odpowiedni JSONL ze strony Kaikki/Wiktextract):

```bash
python slownik_stereotypow_przyrodniczych.py importuj-wiktextract polish.jsonl --slowa lis dąb sowa
```

Można też użyć `--tylko-przyroda`, aby ograniczyć import do rozpoznanych kategorii przyrodniczych. Najpierw sprawdź kategorie na wybranym zrzucie.

## Generator haseł słownika

Ustaw klucz API jako zmienną środowiskową. Nie zapisuj go w kodzie ani w repozytorium.

macOS/Linux:

```bash
export OPENAI_API_KEY="TWÓJ_KLUCZ"
export OPENAI_MODEL="gpt-4.1-mini"
python slownik_stereotypow_przyrodniczych.py stereotypy --baza slownik_stereotypow.json lis sowa
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="TWÓJ_KLUCZ"
$env:OPENAI_MODEL="gpt-4.1-mini"
python slownik_stereotypow_przyrodniczych.py stereotypy --baza slownik_stereotypow.json lis sowa
```

Eksport CSV:

```bash
python slownik_stereotypow_przyrodniczych.py stereotypy --baza slownik_stereotypow.json --csv slownik.csv lis sowa
```

## GitHub Actions

Po wysłaniu repozytorium GitHub automatycznie uruchamia testy z `.github/workflows/tests.yml` dla Python 3.11–3.13 przy każdym `push` i `pull_request`. Testy nie potrzebują klucza API i nie wykonują płatnych wywołań modelu.

## Ważne ograniczenia obecnej wersji

- To pakiet oparty na istniejącym schemacie grafu v1. **Migracja do schematu v2 i rozdzielanie homonimów nie są jeszcze wdrożone**; w v1 forma w obrębie języka jest scalana według klucza normalizacyjnego.
- Importer obecnie odtwarza łańcuch na podstawie kolejności szablonów etymologicznych i dodaje uwagę, że wymaga on weryfikacji. To uproszczenie, nie dowód, że każda kolejna forma jest przodkiem poprzedniej.
- Rozpoznawanie kategorii przyrodniczych zależy od kategorii obecnych w konkretnym zrzucie Wiktextract.
- Wygenerowane hasła słownika są materiałem redakcyjnym do weryfikacji, nie gotowymi ustaleniami naukowymi.

Przed publikacją danych sprawdź licencje zrzutów i źródeł oraz udokumentuj ich pochodzenie.
