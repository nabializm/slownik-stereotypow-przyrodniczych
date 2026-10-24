from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st
from openai import OpenAI

import graf_etymologiczny as graf
from slownik_stereotypow_przyrodniczych import DEFAULT_MODEL, etymologia_z_grafu, wygeneruj_haslo

st.set_page_config(
    page_title="Słownik stereotypów przyrodniczych",
    page_icon="🌿",
    layout="wide",
)

WSJP_LIS_URL = "https://wsjp.pl/haslo/podglad/31781/lis"
DEMO_DB_PATH = Path(tempfile.gettempdir()) / "slownik_stereotypow_demo.sqlite"
MAX_QUERIES_PER_SESSION = 8


def read_secret(name: str, default: str = "") -> str:
    """Odczytuje sekret z ustawień hostingu lub zmiennej środowiskowej."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        value = str(st.secrets.get(name, default)).strip()
    except Exception:
        value = default
    return value


def ensure_demo_graph() -> Path:
    """Używa bazy dostarczonej z repozytorium lub tworzy minimalny przykład demo."""
    committed_db = Path(__file__).resolve().parent / "etymologia.sqlite"
    if committed_db.exists():
        return committed_db

    con = graf.otworz(DEMO_DB_PATH, utworz=True)
    try:
        with con:
            if graf.znajdz_wezel(con, "pl", "lis") is None:
                nowoczesny = graf.zapewnij_wezel(
                    con, "pl", "lis", czesc_mowy="rzeczownik", znaczenie="zwierzę", kategoria="zwierzę"
                )
                praslowianski = graf.zapewnij_wezel(
                    con, "sla-pro", "*lisъ", czesc_mowy="rzeczownik", znaczenie="forma rekonstruowana"
                )
                graf.dodaj_krawedz(
                    con,
                    nowoczesny,
                    praslowianski,
                    "dziedziczenie",
                    "naukowe",
                    "wsjp",
                    "Wielki słownik języka polskiego PAN, hasło „lis”, sekcja „Pochodzenie”: psł. *lisъ. Źródło: " + WSJP_LIS_URL,
                )
    finally:
        con.close()
    return DEMO_DB_PATH


def language_name(db_path: Path, code: str) -> str:
    try:
        con = graf.otworz(db_path)
        try:
            row = con.execute("SELECT nazwa FROM jezyki WHERE kod = ?", (code,)).fetchone()
            return str(row[0]) if row else code
        finally:
            con.close()
    except Exception:
        return code


def source_details(db_path: Path, source_key: str) -> tuple[str, str | None]:
    """Zwraca czytelną nazwę i URL źródła zapisanego w grafie."""
    first_key = source_key.split("+")[0].strip()
    try:
        con = graf.otworz(db_path)
        try:
            row = con.execute("SELECT tytul, url FROM zrodla WHERE klucz = ?", (first_key,)).fetchone()
            if row:
                url = row[1]
                if first_key == "wsjp":
                    url = WSJP_LIS_URL
                return str(row[0]), str(url) if url else None
        finally:
            con.close()
    except Exception:
        pass
    if first_key == "wsjp":
        return "Wielki słownik języka polskiego PAN", WSJP_LIS_URL
    return first_key or "źródło nieopisane", None


def display_etymology(db_path: Path, term: str, result: dict[str, Any] | None) -> None:
    st.subheader("Etymologia i źródła")
    st.caption("Dane etymologiczne pochodzą z grafu źródłowego, a nie z domysłów modelu językowego.")
    if result is None:
        st.info(
            f"W dostępnej bazie nie ma obecnie wpisu etymologicznego dla „{term}”. "
            "To brak danych w tej aplikacji, a nie dowód, że wyraz nie ma etymologii."
        )
        return

    node = result.get("wezel", {})
    st.markdown(
        f"**Hasło w grafie:** {node.get('forma', term)}  "
        f"\n\n**Język:** {language_name(db_path, node.get('jezyk', 'pl'))}"
    )
    chains = result.get("lancuchy", [])
    if not chains:
        st.info("Hasło jest zapisane w grafie, ale nie ma w nim zapisanych przodków tego wyrazu.")
    for number, chain in enumerate(chains, 1):
        confidence = chain.get("pewnosc_lancucha", "nieokreślona")
        st.markdown(f"**Łańcuch {number}** · pewność całego łańcucha: `{confidence}`")
        parts = [f"**{node.get('forma', term)}** ({language_name(db_path, node.get('jezyk', 'pl'))})"]
        for step in chain.get("kroki", []):
            source_title, source_url = source_details(db_path, str(step.get("zrodlo", "")))
            if source_url:
                source_label = f"[{source_title}]({source_url})"
            else:
                source_label = source_title
            parts.append(
                f"← *{step.get('typ', 'relacja')}* · pewność: `{step.get('pewnosc', 'nieokreślona')}` "
                f"· źródło: {source_label} ← `{step.get('forma', '')}` "
                f"({language_name(db_path, step.get('jezyk', ''))})"
            )
        st.markdown("  ".join(parts))
        for step in chain.get("kroki", []):
            if step.get("uwagi"):
                st.caption(str(step["uwagi"]))

    if result.get("kognaty"):
        with st.expander("Pokrewne formy (kognaty)"):
            for item in result["kognaty"]:
                source_title, source_url = source_details(db_path, str(item.get("zrodlo", "")))
                suffix = f" · źródło: [{source_title}]({source_url})" if source_url else f" · źródło: {source_title}"
                st.markdown(
                    f"- **{item.get('forma', '')}** ({language_name(db_path, item.get('jezyk', ''))})"
                    f" · pewność: `{item.get('pewnosc', '')}`{suffix}"
                )
    if result.get("poswiadczenia"):
        with st.expander("Poświadczenia w źródłach"):
            for item in result["poswiadczenia"]:
                date = item.get("rok_od") or "data nieustalona"
                if item.get("rok_do") and item.get("rok_do") != item.get("rok_od"):
                    date = f"{date}–{item['rok_do']}"
                st.markdown(f"- **{item.get('forma', '')}** · {date} · źródło: `{item.get('zrodlo', '')}`")
                if item.get("cytat"):
                    st.caption(str(item["cytat"]))


def display_list(items: list[str], empty_message: str | None = None) -> None:
    if items:
        for item in items:
            st.markdown(f"- {item}")
    elif empty_message:
        st.caption(empty_message)


def display_entry(entry: dict[str, Any]) -> None:
    st.subheader("Opis stereotypów i skojarzeń")
    st.markdown(f"### {entry.get('haslo', 'Hasło')}")
    st.write(entry.get("identyfikacja", "Brak opisu identyfikacyjnego."))

    st.markdown("#### Skojarzenia stereotypowe")
    display_list(entry.get("stereotypowe_skojarzenia", []), "Model nie wskazał utrwalonych skojarzeń; wymaga to weryfikacji redakcyjnej.")

    connotations = entry.get("konotacje", {}) or {}
    positive, neutral, negative = st.columns(3)
    with positive:
        st.markdown("#### Pozytywne")
        display_list(connotations.get("pozytywne", []), "Brak wskazań.")
    with neutral:
        st.markdown("#### Neutralne")
        display_list(connotations.get("neutralne", []), "Brak wskazań.")
    with negative:
        st.markdown("#### Negatywne")
        display_list(connotations.get("negatywne", []), "Brak wskazań.")

    st.markdown("#### Frazeologia")
    phrases = entry.get("frazeologia", []) or []
    if phrases:
        for phrase in phrases:
            st.markdown(f"- **{phrase.get('wyrazenie', '')}** — {phrase.get('znaczenie', '')}  ")
            st.caption(f"Status: {phrase.get('status', 'wymaga weryfikacji')}")
    else:
        st.caption("Nie podano frazeologizmów. Aplikacja nie dopisuje ich na siłę.")

    st.markdown("#### Przykłady użycia")
    display_list(entry.get("przyklady_uzycia", []), "Brak przykładów.")

    st.markdown("#### Interpretacja kulturowa")
    st.write(entry.get("interpretacja_kulturowa", "Brak interpretacji."))

    st.markdown("#### Co wymaga sprawdzenia")
    display_list(entry.get("uwagi_metodologiczne", []), "Brak dodatkowych uwag.")

    metadata = entry.get("metadane", {}) or {}
    st.warning("To materiał redakcyjny wygenerowany przez model, a nie automatycznie potwierdzone ustalenie naukowe.")
    if metadata.get("model"):
        st.caption(f"Model: {metadata['model']} · Wygenerowano (UTC): {metadata.get('wygenerowano_utc', 'brak danych')}")


st.title("🌿 Słownik stereotypów przyrodniczych")
st.write(
    "Wpisz nazwę zwierzęcia, rośliny, zjawiska lub miejsca. Aplikacja przygotuje opis skojarzeń "
    "językowych i kulturowych, a dane etymologiczne pokaże osobno, na podstawie źródeł zapisanych w grafie."
)

with st.sidebar:
    st.header("O słowniku")
    st.write("Skojarzenia generowane przez AI są materiałem do weryfikacji, nie dowodem naukowym.")
    st.write("Etymologia nie jest dopowiadana przez model. Jeśli baza nie zawiera wpisu, aplikacja jasno to pokaże.")
    st.markdown(f"Przykładowe źródło etymologii dla hasła „lis”: [WSJP PAN]({WSJP_LIS_URL}).")
    st.divider()
    st.caption("Każde wyszukanie może zużyć środki z konta API właściciela aplikacji. Nie udostępniaj publicznie klucza API.")

with st.form("search_form"):
    term = st.text_input("Wpisz hasło", placeholder="np. lis, sowa, dąb, burza", max_chars=100)
    submitted = st.form_submit_button("Szukaj", type="primary", use_container_width=True)

if "query_count" not in st.session_state:
    st.session_state.query_count = 0

if submitted:
    term = term.strip()
    if not term:
        st.warning("Wpisz nazwę, której szukamy.")
    elif st.session_state.query_count >= MAX_QUERIES_PER_SESSION:
        st.error(
            "W tej sesji wykorzystano limit wyszukiwań. To podstawowe ograniczenie dla demonstracji; "
            "przy publicznym udostępnieniu warto dodać mocniejszą ochronę przed nadużyciami."
        )
    else:
        st.session_state.query_count += 1
        db_path = ensure_demo_graph()
        etymology = etymologia_z_grafu(db_path, term, "pl")
        display_etymology(db_path, term, etymology)

        api_key = read_secret("OPENAI_API_KEY")
        model = read_secret("OPENAI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        if not api_key:
            st.warning(
                "Opis AI nie jest jeszcze włączony. Właściciel aplikacji musi dodać klucz API "
                "w ustawieniach aplikacji Streamlit (Secrets). Nie wpisuj klucza w to pole i nie zapisuj go na GitHubie."
            )
        else:
            try:
                client = OpenAI(api_key=api_key)
                graph_context = graf.formatuj_tekst(etymology) if etymology else None
                with st.spinner("Przygotowuję opis..."):
                    entry = wygeneruj_haslo(client, term, model, graph_context)
                    if etymology:
                        entry["etymologia_z_grafu"] = etymology
                display_entry(entry)
            except Exception as exc:
                st.error(
                    "Nie udało się wygenerować opisu. Sprawdź, czy klucz API jest poprawny, "
                    "czy konto API ma dostępne środki oraz czy model jest dostępny."
                )
                st.caption(f"Szczegóły błędu: {type(exc).__name__}: {exc}")
