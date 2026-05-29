import uuid

import pytest


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_enqueue_batch_match_canary_uses_batch_payload_and_dedupe(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    calls = []

    async def fake_enqueue(session, **kwargs):
        calls.append((session, kwargs))
        return 123

    monkeypatch.setattr(script, "enqueue", fake_enqueue)
    session = _FakeSession()
    profile_id = uuid.uuid4()

    row_id = await script.enqueue_batch_match_canary(session, profile_id=profile_id)

    assert row_id == 123
    assert session.committed is True
    assert calls == [
        (
            session,
            {
                "job_type": "batch-match",
                "payload": {"profile_id": str(profile_id)},
                "dedupe_key": f"batch-match:{profile_id}",
                "on_conflict": "upsert_reset_not_before",
            },
        )
    ]
