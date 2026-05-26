import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.oauth_account import OAuthAccount


class User(SQLModel, table=True):
    """
    User table compatible with fastapi-users interface.
    Fields mirror SQLAlchemyBaseUserTableUUID but defined via SQLModel
    to avoid pydantic v2 incompatibility with Mapped[] types.
    """

    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str = ""
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    subscription_plan: str = Field(
        default="free",
        sa_column=Column(sa.String(), nullable=False, server_default=sa.text("'free'")),
    )
    subscription_status: str = Field(
        default="inactive",
        sa_column=Column(sa.String(), nullable=False, server_default=sa.text("'inactive'")),
    )
    subscription_current_period_end: datetime | None = Field(
        default=None,
        sa_column=Column(sa.DateTime(timezone=True), nullable=True),
    )

    # fastapi-users-db-sqlalchemy's add_oauth_account does
    # `user.oauth_accounts.append(...)`, and its _get_user calls .unique() on
    # results — both require this relationship configured with lazy="joined"
    # so the collection is eager-loaded via LEFT OUTER JOIN. Without it,
    # OAuth login fails with AttributeError on first sign-in.
    oauth_accounts: list["OAuthAccount"] = Relationship(
        sa_relationship_kwargs={"lazy": "joined"},
    )
