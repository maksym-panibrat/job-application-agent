import uuid
from datetime import date

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class UsageCounter(SQLModel, table=True):
    __tablename__ = "usage_counters"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(index=True)
    action: str
    utc_day: date = Field(sa_column=sa.Column(sa.Date, nullable=False))
    count: int = Field(default=0)

    __table_args__ = (
        sa.UniqueConstraint(
            "user_id", "action", "utc_day", name="uq_usage_counters_user_action_day"
        ),
    )
