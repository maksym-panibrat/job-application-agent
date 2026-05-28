import uuid

from app.models.subscription import EngagementEvent


def test_engagement_event_accepts_metadata_alias_without_silent_drop():
    payload = {"source": "unit-test"}

    event = EngagementEvent(
        user_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        event_type="profile_updated",
        metadata=payload,
    )

    assert event.event_metadata == payload
    assert EngagementEvent.__table__.c["metadata"].name == "metadata"

    explicit = EngagementEvent(
        user_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        event_type="profile_updated",
        event_metadata={"source": "explicit-name"},
    )
    assert explicit.event_metadata == {"source": "explicit-name"}
