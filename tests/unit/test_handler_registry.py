def test_registry_is_empty_initially():
    from app.worker.handlers import HANDLERS

    assert isinstance(HANDLERS, dict)


def test_transient_error_carries_retry_after():
    from app.worker.handlers import TransientError

    err = TransientError("test", retry_after_seconds=42)
    assert err.retry_after_seconds == 42
    default_err = TransientError("test")
    assert default_err.retry_after_seconds is None
