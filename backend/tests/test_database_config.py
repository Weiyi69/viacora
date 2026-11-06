from types import SimpleNamespace

from app.services import database


def test_database_engine_hides_bound_parameters_from_sqlalchemy_errors(
    monkeypatch,
) -> None:
    database.get_database_engine.cache_clear()
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: SimpleNamespace(database_url="sqlite://"),
    )
    try:
        engine = database.get_database_engine()
        assert engine.hide_parameters is True
        engine.dispose()
    finally:
        database.get_database_engine.cache_clear()
