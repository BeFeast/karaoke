"""Read the catalog pilot spend ledger with an explicit output allowlist."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys

from pydantic import SecretStr
from sqlalchemy import select, text

from karaoke.catalog_pilot import DAILY_MICROS, TOTAL_MICROS, _utc, ledger_spend
from karaoke.config import get_settings
from karaoke.db.models import CatalogAsset, CatalogPilotState
from karaoke.db.session import create_engine_and_sessionmaker

STATES = frozenset({"processing", "ready", "review", "failed", "retired"})


async def read_ledger(database_url: str | SecretStr) -> dict:
    engine, factory = create_engine_and_sessionmaker(database_url)
    try:
        async with factory.begin() as db:
            if engine.dialect.name == "postgresql":
                await db.execute(text("SET TRANSACTION READ ONLY"))
            paused = await db.scalar(select(CatalogPilotState.paused).where(CatalogPilotState.id == 1))
            attempts = (await db.execute(
                select(
                    CatalogAsset.id,
                    CatalogAsset.job_id,
                    CatalogAsset.state,
                    CatalogAsset.download_started_at,
                    CatalogAsset.finished_at,
                    CatalogAsset.reserved_micros,
                    CatalogAsset.charged_micros,
                ).order_by(CatalogAsset.id)
            )).all()
    finally:
        await engine.dispose()

    day = dt.datetime.now(dt.UTC).date()
    total, today = ledger_spend(attempts, day)
    return {
        "utc_day": day.isoformat(),
        "paused": bool(paused),
        "lifetime_used_micros": total,
        "lifetime_cap_micros": TOTAL_MICROS,
        "utc_day_used_micros": today,
        "utc_day_cap_micros": DAILY_MICROS,
        "attempts": [
            {
                "asset_id": row.id,
                "job_id": row.job_id,
                "state": row.state if row.state in STATES else "unknown",
                "started_at": _utc(row.download_started_at).isoformat(),
                "finished_at": _utc(row.finished_at).isoformat() if row.finished_at else None,
                "reserved_micros": row.reserved_micros,
                "charged_micros": row.charged_micros,
            }
            for row in attempts
        ],
    }


def main() -> int:
    try:
        ledger = asyncio.run(read_ledger(get_settings().database_url))
    except ValueError:
        print("catalog_ledger_invalid_config", file=sys.stderr)
        return 1
    except Exception:
        print("catalog_ledger_unavailable", file=sys.stderr)
        return 1
    print(json.dumps(ledger))
    return 0
