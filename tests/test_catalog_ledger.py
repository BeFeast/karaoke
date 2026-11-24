import asyncio
import datetime as dt
import json
import traceback
from types import SimpleNamespace

import pytest

from karaoke import main
from karaoke.config import Settings
from karaoke.db.models import Base, CatalogAsset, CatalogPilotState
from karaoke.db.session import create_engine_and_sessionmaker

SECRET_FIELDS = (
    "database_url", "service_token", "clerk_secret_key", "runpod_api_key",
    "vast_api_key", "r2_access_key_id", "r2_secret_access_key", "llm_api_key",
)


@pytest.mark.parametrize("argument", ["settings", "malformed_dsn", "invalid_port"])
def test_invalid_db_argument_has_safe_cli_and_traceback(monkeypatch, capsys, argument):
    secret = "private-regression-marker"
    cfg = Settings(**{name: secret for name in SECRET_FIELDS})
    assert secret not in repr(cfg)
    assert secret not in str(cfg)

    match argument:
        case "settings":
            invalid = cfg
        case "invalid_port":
            invalid = Settings(database_url=f"postgresql+asyncpg://localhost:{secret}/db").database_url
        case _:
            invalid = cfg.database_url
    monkeypatch.setattr("karaoke.catalog_ledger.get_settings", lambda: SimpleNamespace(database_url=invalid))
    assert main(["catalog", "ledger"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "catalog_ledger_invalid_config\n"

    with pytest.raises(ValueError, match="invalid database URL") as exc:
        create_engine_and_sessionmaker(invalid)
    rendered = "".join(traceback.format_exception(exc.value))
    assert secret not in rendered
    assert "Settings(" not in rendered


def test_ledger_emits_only_allowlisted_fields(tmp_path, monkeypatch, capsys):
    url = f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}"
    now = dt.datetime.now(dt.UTC)

    async def seed():
        engine, factory = create_engine_and_sessionmaker(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with factory.begin() as db:
                db.add(CatalogPilotState(id=1, paused=True))
                db.add(CatalogAsset(
                    id=1, job_id=2, source_id=3, pipeline_version="1",
                    state="untrusted-ledger-content", download_started_at=now,
                    reserved_micros=50_000,
                ))
        finally:
            await engine.dispose()

    asyncio.run(seed())
    cfg = Settings(database_url=url)
    monkeypatch.setattr("karaoke.catalog_ledger.get_settings", lambda: cfg)
    assert main(["catalog", "ledger"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "untrusted-ledger-content" not in out
    ledger = json.loads(out)
    assert ledger["paused"] is True
    assert ledger["lifetime_used_micros"] == ledger["utc_day_used_micros"] == 50_000
    assert ledger["attempts"] == [{
        "asset_id": 1, "job_id": 2, "state": "unknown", "started_at": now.isoformat(),
        "finished_at": None, "reserved_micros": 50_000, "charged_micros": None,
    }]
