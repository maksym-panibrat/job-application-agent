"""Custom transport that converts the OAuth login JSON response into a 303
redirect back to the SPA, so browser users land in the app instead of staring
at a raw {"access_token": ...} JSON page.

The token rides in the URL fragment rather than the query string: fragments
are never sent over HTTP, so the JWT doesn't end up in Cloud Run access logs,
the Referer header, or our own structured logs.
"""

from fastapi import Response
from fastapi.responses import RedirectResponse
from fastapi_users.authentication.transport.bearer import BearerTransport


class RedirectingBearerTransport(BearerTransport):
    def __init__(self, redirect_url: str, *, tokenUrl: str = "/auth/jwt/login"):
        super().__init__(tokenUrl=tokenUrl)
        self.redirect_url = redirect_url

    async def get_login_response(self, token: str) -> Response:
        return RedirectResponse(
            url=f"{self.redirect_url}#access_token={token}",
            status_code=303,
        )
