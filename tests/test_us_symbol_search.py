from src.services.us_symbol_search import UsSymbolSearchService


def test_google_alias_returns_alphabet_symbols(monkeypatch):
    service = UsSymbolSearchService()
    monkeypatch.setattr(service, "_yahoo_matches", lambda query, limit: [])

    results = service.search("GOOGLE")

    assert [item.symbol for item in results[:2]] == ["GOOG", "GOOGL"]
    assert results[0].name == "Alphabet Inc. Class C"


def test_symbol_search_dedupes_builtin_and_external(monkeypatch):
    service = UsSymbolSearchService()
    monkeypatch.setattr(
        service,
        "_yahoo_matches",
        lambda query, limit: [service._builtin_matches("APPLE")[0]],
    )

    results = service.search("APPLE")

    assert [item.symbol for item in results] == ["AAPL"]
