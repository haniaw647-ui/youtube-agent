"""Real, unmocked — IMPLEMENTATION_PLAN.md Phase 10: 'confirm RLS isolation
holds under concurrent load, not just single-request tests.' The existing
isolation test (test_tenant_isolation.py) proves RLS is correct sequentially;
this proves it stays correct when many tenants' DB work genuinely overlaps in
time. Goes straight at tenant_session() with real asyncio.gather() concurrency
rather than through FastAPI's TestClient — swapping a shared
app.dependency_overrides dict across concurrently-dispatched requests isn't
itself safe to do in a test, so this isolates the actual claim under test
(each tenant_session() call gets its own NullPool connection, so concurrent
calls should never cross-contaminate) from that unrelated hazard.
"""

import asyncio
import uuid

from sqlalchemy import text

from src.orchestrator.db import service_session, tenant_session
from src.orchestrator.tenants import ensure_tenant

TENANT_COUNT = 8
TENANTS = [uuid.uuid4() for _ in range(TENANT_COUNT)]


def setup_module(_module: object) -> None:
    for tenant_id in TENANTS:
        asyncio.run(ensure_tenant(tenant_id, f"RLS Concurrency Tenant {tenant_id}"))


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            for tenant_id in TENANTS:
                await session.execute(
                    text("DELETE FROM channels WHERE tenant_id = :t"), {"t": tenant_id}
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id}
                )
            await session.commit()

    asyncio.run(_cleanup())


async def _create_channel(tenant_id: uuid.UUID) -> str:
    async with tenant_session(tenant_id) as session:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO channels (tenant_id, name) VALUES (:t, :name) "
                        "RETURNING id"
                    ),
                    {"t": tenant_id, "name": f"Channel for {tenant_id}"},
                )
            )
            .mappings()
            .one()
        )
    return str(row["id"])


async def _list_channel_ids(tenant_id: uuid.UUID) -> list[str]:
    async with tenant_session(tenant_id) as session:
        rows = (await session.execute(text("SELECT id FROM channels"))).mappings().all()
    return [str(r["id"]) for r in rows]


def test_concurrent_requests_from_many_tenants_never_cross_leak() -> None:
    async def _run() -> None:
        # Every tenant creates its one channel at genuinely overlapping
        # times — asyncio.gather runs these on the same event loop, and each
        # tenant_session() call opens its own real connection (NullPool), so
        # they're truly in flight together, not serialized behind each other.
        channel_ids = await asyncio.gather(*[_create_channel(t) for t in TENANTS])
        assert len(set(channel_ids)) == TENANT_COUNT  # every channel is distinct

        # Now every tenant lists channels at the same time — none may ever
        # see another tenant's channel, even with requests genuinely
        # in flight together against the real RLS-enforced session layer.
        listings = await asyncio.gather(*[_list_channel_ids(t) for t in TENANTS])

        for tenant_id, seen_ids, own_channel_id in zip(
            TENANTS, listings, channel_ids, strict=True
        ):
            assert seen_ids == [own_channel_id], (
                f"ISOLATION BREACH under concurrency: tenant {tenant_id} saw {seen_ids}, "
                f"expected only its own channel {own_channel_id}"
            )

    asyncio.run(_run())
