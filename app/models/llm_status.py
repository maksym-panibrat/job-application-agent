from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class LLMStatus(SQLModel, table=True):
    __tablename__ = "llm_status"
    id: int = Field(default=1, primary_key=True)
    exhausted_until: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
