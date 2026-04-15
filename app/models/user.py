import uuid

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlmodel import Field, SQLModel


class User(SQLAlchemyBaseUserTableUUID, SQLModel, table=True):
    __tablename__ = "users"

    # fastapi-users fields are inherited from SQLAlchemyBaseUserTableUUID.
    # We re-declare id here using SQLModel's Field so that SQLModel picks up
    # the primary key correctly when generating metadata.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
