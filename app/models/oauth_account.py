import uuid

from sqlmodel import Field, SQLModel


class OAuthAccount(SQLModel, table=True):
    __tablename__ = "oauth_accounts"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    oauth_name: str = Field(index=True)
    access_token: str
    expires_at: int | None = None
    refresh_token: str | None = None
    account_id: str = Field(index=True)
    account_email: str
