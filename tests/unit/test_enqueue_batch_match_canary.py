import uuid

import pytest


class _FakeSession:
    def __init__(self, *query_results) -> None:
        self._query_results = iter(query_results)
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement):
        del statement
        return _FakeResult(next(self._query_results))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.parametrize("max_items", [1, 10])
def test_parser_accepts_canary_bounds(max_items):
    from scripts.enqueue_batch_match_canary import _parser

    args = _parser().parse_args(
        [
            "--profile-id",
            str(uuid.uuid4()),
            "--max-items",
            str(max_items),
            "--ack-isolated-canary-profile",
        ]
    )

    assert args.max_items == max_items
    assert args.ack_isolated_canary_profile is True


@pytest.mark.parametrize("max_items", ["0", "11", "not-a-number"])
def test_parser_rejects_out_of_bounds_max_items(max_items):
    from scripts.enqueue_batch_match_canary import _parser

    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(
            [
                "--profile-id",
                str(uuid.uuid4()),
                "--max-items",
                max_items,
                "--ack-isolated-canary-profile",
            ]
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "extra_args",
    [[], ["--max-items", "1"]],
)
def test_parser_requires_limit_and_isolation_acknowledgment(extra_args):
    from scripts.enqueue_batch_match_canary import _parser

    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["--profile-id", str(uuid.uuid4()), *extra_args])

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_enqueue_batch_match_canary_uses_exact_bounded_typed_payload(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    calls = []

    async def fake_assert_idle(session, *, profile_id):
        calls.append(("idle", session, profile_id))

    async def fake_enqueue(session, profile_id, **kwargs):
        calls.append(("enqueue", session, profile_id, kwargs))
        return 123

    async def fake_assert_no_active_batch(session, *, profile_id):
        calls.append(("active", session, profile_id))

    monkeypatch.setattr(script, "_assert_profile_idle", fake_assert_idle)
    monkeypatch.setattr(script, "_assert_no_active_provider_batch", fake_assert_no_active_batch)
    monkeypatch.setattr(script, "enqueue_batch_match", fake_enqueue)
    session = _FakeSession()
    profile_id = uuid.uuid4()

    row_id = await script.enqueue_batch_match_canary(
        session,
        profile_id=profile_id,
        max_items=7,
    )

    assert row_id == 123
    assert session.committed is True
    assert calls == [
        ("idle", session, profile_id),
        (
            "enqueue",
            session,
            profile_id,
            {
                "max_items": 7,
                "max_candidates": 7,
                "return_existing_on_conflict": False,
            },
        ),
        ("active", session, profile_id),
    ]


@pytest.mark.asyncio
async def test_canary_refuses_pending_or_in_progress_queue_work(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    enqueue_called = False

    async def fake_enqueue(*args, **kwargs):
        nonlocal enqueue_called
        enqueue_called = True

    monkeypatch.setattr(script, "enqueue_batch_match", fake_enqueue)
    session = _FakeSession(42)

    with pytest.raises(script.CanaryRefused, match="pending/in-progress"):
        await script.enqueue_batch_match_canary(
            session,
            profile_id=uuid.uuid4(),
            max_items=1,
        )

    assert enqueue_called is False
    assert session.committed is False


@pytest.mark.asyncio
async def test_canary_refuses_active_provider_batch(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    enqueue_called = False

    async def fake_enqueue(*args, **kwargs):
        nonlocal enqueue_called
        enqueue_called = True

    monkeypatch.setattr(script, "enqueue_batch_match", fake_enqueue)
    session = _FakeSession(None, uuid.uuid4())

    with pytest.raises(script.CanaryRefused, match="active provider batch"):
        await script.enqueue_batch_match_canary(
            session,
            profile_id=uuid.uuid4(),
            max_items=1,
        )

    assert enqueue_called is False
    assert session.committed is False


@pytest.mark.asyncio
async def test_canary_refuses_enqueue_conflict_without_resetting_existing_row(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    async def fake_assert_idle(session, *, profile_id):
        pass

    async def fake_enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(script, "_assert_profile_idle", fake_assert_idle)
    monkeypatch.setattr(script, "enqueue_batch_match", fake_enqueue)
    session = _FakeSession()

    with pytest.raises(script.CanaryRefused, match="concurrent"):
        await script.enqueue_batch_match_canary(
            session,
            profile_id=uuid.uuid4(),
            max_items=1,
        )

    assert session.committed is False


@pytest.mark.asyncio
async def test_canary_rolls_back_if_provider_batch_appears_after_insert(monkeypatch):
    from scripts import enqueue_batch_match_canary as script

    async def fake_assert_idle(session, *, profile_id):
        pass

    async def fake_enqueue(*args, **kwargs):
        return 123

    async def fake_assert_no_active_batch(session, *, profile_id):
        raise script.CanaryRefused("profile already has an active provider batch")

    monkeypatch.setattr(script, "_assert_profile_idle", fake_assert_idle)
    monkeypatch.setattr(script, "enqueue_batch_match", fake_enqueue)
    monkeypatch.setattr(script, "_assert_no_active_provider_batch", fake_assert_no_active_batch)
    session = _FakeSession()

    with pytest.raises(script.CanaryRefused, match="active provider batch"):
        await script.enqueue_batch_match_canary(
            session,
            profile_id=uuid.uuid4(),
            max_items=1,
        )

    assert session.rolled_back is True
    assert session.committed is False
