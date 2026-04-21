from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class RateLimit(SQLModel, table=True):
    __tablename__ = "rate_limits"
    id: int = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    window_start: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))
    count: int = Field(default=0)

    __table_args__ = (sa.UniqueConstraint("key", "window_start", name="uq_rate_limits_key_window"),)
