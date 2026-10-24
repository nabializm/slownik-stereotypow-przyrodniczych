# Przegląd techniczny: słownik stereotypów i graf etymologiczny

## Zakres i wynik uruchomienia

Przejrzano:
- `slownik_stereotypow_przyrodniczych-1.py` — CLI uwzględniający graf,
- `graf_etymologiczny.py` — schemat SQLite, zapytania i importer Wiktextract,
- `README-4.md` — instrukcję obsługi.

Oba pliki `.py` przechodzą `py_compile`.

Test w odizolowanej bazie `/tmp/lis_review/etymologia.sqlite`:
1. `graf-init` utworzyło bazę z 33 wpisami języków.
2. Pytanie o `lis` w pustym grafie zwróciło poprawny komunikat o braku danych.
3. Zaimportowano jeden **syntetyczny rekord testowy w formacie zbliżonym do Wiktextract** (nie rzeczywisty zrzut). Importer utworzył węzeł `pl:lis` i krawędź dziedziczenia do `sla-pro:*lisъ`.
4. Zapytanie zwróciło: `lis [pl] ←(dziedziczenie; wspolnotowe; wikislownik)← *lisъ [sla-pro]`.
5. Próba `stereotypy lis` nie uruchomiła generowania modelowego, ponieważ w środowisku nie dało się zaimportować `OpenAI` z pakietu `openai`; CLI zwróciło komunikat o braku pakietu.

## Najważniejsze problemy

### P1 — niespójna nazwa pliku CLI w README

`README-4.md` pokazuje polecenia uruchamiające `slownik_stereotypow_przyrodniczych.py`. W dostarczonych plikach jest to starszy skrypt bez poleceń grafu. Nowe polecenia (`graf-init`, `importuj-wiktextract`, `etymologia` itd.) znajdują się w `slownik_stereotypow_przyrodniczych-1.py`. Wykonanie instrukcji README ze starszym plikiem kończy się błędem `unrecognized arguments: --graf ...`.

**Naprawa:** ustalić jeden kanoniczny plik CLI; uaktualnić README i przestać utrzymywać dwie niezgodne wersje pod podobnymi nazwami.

### P1 — model danych scala homonimy i odrębne etymologie

Tabela `wezly` ma `UNIQUE (jezyk, forma_klucz)`, a importer zapisuje tylko pierwszą znalezioną glosę. W efekcie różne znaczenia i historie tej samej formy są scalane. To szczególnie ważne dla `lis`: poza nazwą zwierzęcia istnieją użycia przenośne, a Wiktionary opisuje też skrót `lis` od `listopad`. Jedna forma nie powinna automatycznie oznaczać jednego leksemu ani jednej etymologii.

**Naprawa:** wprowadzić osobny identyfikator leksemu/sensu lub etymologii, a `forma` traktować jako formę powierzchniową. Zachować identyfikator wpisu/etymologii ze źródła.

### P1 — „pewność” miesza różne osie oceny

`PEWNOSCI` jednocześnie zawiera poziom rodzaju źródła (`naukowe`, `wspolnotowe`), status epistemiczny (`hipoteza`, `sporne`) i pochodzenie twierdzenia (`model`). Kod porządkuje je liniowo i wylicza pewność łańcucha jako najsłabszy element. Nie zawsze da się sensownie ustawić te kategorie na jednej skali.

**Naprawa:** oddzielić co najmniej: (a) rodzaj źródła, (b) status/ocenę etymologii, (c) siłę poparcia dla konkretnej relacji. Nie wyliczać jednej „pewności łańcucha”, dopóki skale nie są jednoznacznie zdefiniowane.

### P1 — importer zgaduje liniowy łańcuch z kolejności szablonów

`_kroki_z_szablonow()` interpretuje sekwencję szablonów jako jeden łańcuch. Dla rozgałęzionych etymologii i różnych wariantów szablonów może utworzyć błędne połączenia lub pominąć relację. Pełny tekst etymologii jest przechowywany jedynie jako skrócona uwaga (maksymalnie 400 znaków), bez stabilnego identyfikatora rekordu i dokładnego odwołania do wersji hasła źródłowego.

**Naprawa:** mapować każdy obsługiwany typ szablonu oddzielnie, zachować oryginalne argumenty i identyfikator źródłowego rekordu, a niejednoznaczne przypadki oznaczać jako nierozstrzygnięte do ręcznej weryfikacji. Dodać testy dla wielu wariantów `inh`, `bor`, `cal`, `der` i `cog`.

### P2 — limit ścieżek jest stosowany po ich wyliczeniu

`lancuchy_pochodzenia()` ogranicza wynik do `max_sciezek` dopiero po wywołaniu `_przodkowie()`. W gęstym grafie funkcja może wcześniej wyliczyć bardzo wiele ścieżek, co grozi dużym zużyciem pamięci i długim czasem odpowiedzi.

**Naprawa:** egzekwować limit podczas przeszukiwania, a nie dopiero na końcu; rozważyć limit liczby odwiedzonych węzłów i osobny status informujący o ucięciu wyników.

### P2 — importer nie waliduje typu poprawnego JSON-a

Błędny składniowo JSON jest pomijany, ale poprawny JSON, który nie jest obiektem (np. lista lub liczba), spowoduje błąd przy wywołaniu `wpis.get(...)`. Brak pliku wejściowego również nie jest obsłużony komunikatem CLI na tym samym poziomie co `ValueError`.

**Naprawa:** sprawdzać `isinstance(wpis, dict)`, numerować linie i podawać czytelne błędy dla braku pliku oraz niepoprawnego schematu rekordu.

### P2 — istniejące krawędzie nie są aktualizowane

Unikalność krawędzi obejmuje węzły, typ i źródło, ale nie `pewnosc` ani `uwagi`. Ponowne dodanie tej samej relacji z tym samym źródłem i poprawioną oceną zostanie zignorowane, a CLI pokaże tylko komunikat o duplikacie.

**Naprawa:** rozstrzygnąć, czy krawędź ma być wersjonowana, czy aktualizowana. W przypadku aktualizacji zastosować jawne `ON CONFLICT DO UPDATE`; w przypadku wersjonowania dodać historię ocen i zachować poprzednią wartość.

### P2 — brak migracji i wersji schematu

`otworz()` uruchamia `CREATE TABLE IF NOT EXISTS`, ale nie aktualizuje istniejących tabel po zmianie schematu. Kolejna wersja programu może pozornie otworzyć starą bazę, a potem zawieść na braku kolumn lub ograniczeń.

**Naprawa:** dodać `PRAGMA user_version` lub tabelę wersji schematu oraz migracje uruchamiane transakcyjnie.

### P3 — normalizacja nazw w dwóch modułach jest różna

Graf normalizuje Unicode do NFC; główny skrypt `normalizuj()` tego nie robi. Dwa wizualnie identyczne ciągi mogą więc zostać różnie potraktowane w indeksie JSON i w grafie.

**Naprawa:** współdzielić jedną funkcję normalizacji Unicode, z zachowaniem polskich znaków diakrytycznych i gwiazdki rekonstrukcji tam, gdzie ma ona znaczenie.

### P3 — testy automatyczne nie są częścią projektu

Nie znaleziono testów automatycznych obejmujących cykle, równoległe źródła, wieloznaczność, formaty szablonów, ponowny import i migrację schematu. Test jednego rekordu potwierdza podstawową ścieżkę, ale nie potwierdza poprawności na pełnym zrzucie.

**Naprawa:** dodać testy jednostkowe i mały, ręcznie zweryfikowany korpus referencyjny. Przy każdym rekordzie testowym przechowywać oczekiwany graf oraz źródłowe uzasadnienie.

## Zalecana kolejność prac

1. Ujednolicić nazwę pliku CLI i naprawić instrukcje README.
2. Zmienić model węzłów tak, by rozróżniał leksemy/sensy/etymologie.
3. Rozdzielić rodzaj źródła, status etymologii i siłę poparcia relacji.
4. Zachować identyfikatory i surowe dane źródłowe; ograniczyć automatyczne odtwarzanie łańcuchów.
5. Dodać limity przeszukiwania, walidację importerów, migracje i testy regresyjne.

## Ważne ograniczenie testu

Wynik dla `lis` sprawdza przepływ w grafie i zapis rekonstrukcji. Nie jest dowodem, że obecna heurystyka poprawnie odczyta wszystkie rzeczywiste rekordy Wiktextract. Przed importem całego zrzutu należy sprawdzić rzeczywisty rekord `lis` i zestaw trudnych przypadków z wieloma etymologiami oraz rozgałęzieniami.
