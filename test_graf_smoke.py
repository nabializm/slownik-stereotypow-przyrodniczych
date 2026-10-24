import json
import sqlite3
from pathlib import Path

import pytest

import graf_etymologiczny as graf


def test_unicode_normalization_keeps_diacritics():
    assert graf.klucz_formy('  ŁIS  ') == 'łis'
    assert graf.klucz_formy('lis') != graf.klucz_formy('liś')


def test_graph_create_and_add_sourced_edge(tmp_path):
    db = tmp_path / 'test.sqlite'
    con = graf.otworz(db, utworz=True)
    try:
        newer = graf.zapewnij_wezel(con, 'pl', 'lis', znaczenie='zwierzę')
        older = graf.zapewnij_wezel(con, 'zlw-opl', 'lis', znaczenie='lis')
        edge_id = graf.dodaj_krawedz(
            con, newer, older, 'dziedziczenie', 'naukowe', 'sstp', 'test'
        )
        con.commit()
        assert edge_id is not None
        assert con.execute('SELECT COUNT(*) FROM krawedzie').fetchone()[0] == 1
        assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    finally:
        con.close()


def test_rejects_unknown_edge_type(tmp_path):
    con = graf.otworz(tmp_path / 'test.sqlite', utworz=True)
    try:
        a = graf.zapewnij_wezel(con, 'pl', 'lis')
        b = graf.zapewnij_wezel(con, 'cs', 'liš')
        with pytest.raises(ValueError):
            graf.dodaj_krawedz(con, a, b, 'podobne_brzmienie', 'naukowe', 'sstp')
    finally:
        con.close()


def test_importer_requires_a_filter(tmp_path):
    con = graf.otworz(tmp_path / 'test.sqlite', utworz=True)
    source = tmp_path / 'empty.jsonl'
    source.write_text('', encoding='utf-8')
    try:
        with pytest.raises(ValueError):
            graf.importuj_wiktextract(con, source, jezyk='pl')
    finally:
        con.close()


def test_cli_help_runs():
    from slownik_stereotypow_przyrodniczych import zbuduj_parser
    parser = zbuduj_parser()
    assert parser is not None
