import uuid

from sqlmodel import Field, SQLModel


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
