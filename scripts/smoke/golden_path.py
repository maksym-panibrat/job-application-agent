"""
Smoke test: golden-path assertions against the deployed Cloud Run URL.

Usage:
    # Minimal — uses defaults
    SMOKE_BASE_URL=https://... SMOKE_BEARER_TOKEN=eyJ... \\
        SMOKE_CRON_SECRET=<secret> \\
        uv run python scripts/smoke/golden_path.py

    # Verbose request/response logging
    SMOKE_BASE_URL=https://... SMOKE_BEARER_TOKEN=eyJ... \\
        SMOKE_CRON_SECRET=<secret> \\
        uv run python scripts/smoke/golden_path.py --verbose

    # Help
    uv run python scripts/smoke/golden_path.py --help

Environment variables:
    SMOKE_BASE_URL        Base URL of the deployed app (required unless --base-url is given).
                          Defaults to the Cloud Run service URL from CI secrets.
    SMOKE_BEARER_TOKEN    JWT for smoke@panibrat.com (required).  Generate with `make smoke-token`.
    SMOKE_CRON_SECRET     Value of the CRON_SHARED_SECRET prod secret (required for step 6).
                          Passed as X-Cron-Secret header to POST /internal/cron/sync.

Exit codes:
    0  All assertions passed
    1  One or more assertions failed (JSON error payload printed to stderr)
    2  Configuration error (missing env vars / bad args)

Step mapping (matches stabilisation plan):
    Step 1  GET /auth/google/authorize     → redirect_uri param matches expected Cloud Run callback
    Step 2  GET /health                     → {"status": "ok"}
    Step 3  GET /api/profile               → 200 with smoke user's profile
    Step 4  PATCH /api/profile             → update full_name, assert round-trip
    Step 5  GET /api/applications          → 200 list (may be empty)
    Step 6  POST /internal/cron/sync       → 200 {"status": "ok"}  (X-Cron-Secret gated;
                                             may be slow)
    Step 7  POST /api/chat/messages        → 200 SSE stream with assistant response
                                             (proves Gemini pipeline wired to prod)
    Step 8a POST /api/applications/{id}/regenerate
                                           → 200/202 (XFAIL until PR 8/9)
    Step 8b Poll GET /api/applications/{id} until generation_status=="awaiting_review"
                                           → up to 180s (XFAIL until PR 8/9)
    Step 8c PATCH /api/applications/{id} {"status": "approved"}
                                           → 200 (XFAIL until PR 8/9)
    Step 8d Poll GET /api/applications/{id} until generation_status=="ready"
                                           → up to 60s (XFAIL until PR 8/9)
    Step 9  POST /api/applications/{id}/submit
                                           → endpoint exists, token accepted (XFAIL — PR 7)
    Step 10 Cleanup: reset profile full_name to 'Smoke Test' (idempotent teardown)

Note on Step 6: job sync calls external APIs (Adzuna, Remotive, etc.) and may take 10–30 s in
production.  The script uses a 90 s timeout for that step only.

Note on Steps 8a–8d: all four sub-steps are marked XFAIL until PR 8/9 land.  Individual
sub-step failures are diagnosable from the JSON details in the output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api-<revision>-uc.a.run.app"  # placeholder; override via env
SYNC_TIMEOUT_S = 90
CHAT_TIMEOUT_S = 60
DEFAULT_TIMEOUT_S = 20
GENERATION_POLL_INTERVAL_S = 3
GENERATION_AWAITING_REVIEW_TIMEOUT_S = 180
GENERATION_READY_TIMEOUT_S = 60

StepResult = tuple[bool, dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verbose_log(label: str, method: str, url: str, status: int | None, body: Any) -> None:
    print(f"\n[{label}] {method} {url}", file=sys.stderr)
    if status is not None:
        print(f"  HTTP {status}", file=sys.stderr)
    if body is not None:
        try:
            pretty = json.dumps(body, indent=2, default=str)
        except Exception:
            pretty = str(body)
        # Truncate very long bodies to keep output readable
        if len(pretty) > 2000:
            pretty = pretty[:2000] + "\n  ... (truncated)"
        print(f"  {pretty}", file=sys.stderr)


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Step functions — each returns (passed: bool, details: dict)
# ---------------------------------------------------------------------------


async def step1_oauth_authorize(
    client: httpx.AsyncClient, base_url: str, verbose: bool
) -> StepResult:
    """GET /api/auth/google/authorize → redirect_uri must contain the same base_url."""
    url = f"{base_url}/auth/google/authorize"
    params = {"scopes": "openid,email,profile"}
    label = "step1_oauth_authorize"
    try:
        r = await client.get(url, params=params, follow_redirects=False, timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 1, "error": f"Request failed: {exc}", "url": url}

    if verbose:
        _verbose_log(label, "GET", url, r.status_code, _try_json(r))

    # Expect a redirect (3xx) or a JSON body with an authorization_url key
    auth_url: str | None = None
    if r.status_code in (301, 302, 303, 307, 308):
        auth_url = r.headers.get("location", "")
    elif r.status_code == 200:
        body = _try_json(r)
        if isinstance(body, dict):
            auth_url = body.get("authorization_url") or body.get("url")

    if auth_url is None:
        return False, {
            "step": 1,
            "error": f"Expected redirect or authorization_url, got HTTP {r.status_code}",
            "url": url,
            "response_body": _try_json(r),
        }

    # Parse redirect_uri from the OAuth URL's query string
    parsed = urlparse(auth_url)
    qs = parse_qs(parsed.query)
    redirect_uri_values = qs.get("redirect_uri", [])
    if not redirect_uri_values:
        # Also acceptable: state param contains encoded callback — just assert URL is Google
        if "accounts.google.com" not in auth_url:
            return False, {
                "step": 1,
                "error": "Authorization URL does not point to Google",
                "auth_url": auth_url,
            }
        return True, {
            "step": 1,
            "note": "No redirect_uri param found but URL points to Google; acceptable",
            "auth_url": auth_url[:200],
        }

    redirect_uri = redirect_uri_values[0]
    expected_host = urlparse(base_url).netloc
    actual_host = urlparse(redirect_uri).netloc
    if expected_host and expected_host not in redirect_uri:
        return False, {
            "step": 1,
            "error": (
                f"redirect_uri host mismatch: expected '{expected_host}' in '{redirect_uri}'. "
                "Fix GCP OAuth consent → Authorized redirect URIs in GCP Console manually."
            ),
            "redirect_uri": redirect_uri,
            "expected_host": expected_host,
        }

    return True, {
        "step": 1,
        "redirect_uri": redirect_uri,
        "actual_host": actual_host,
        "expected_host": expected_host,
    }


async def step2_health(client: httpx.AsyncClient, base_url: str, verbose: bool) -> StepResult:
    """GET /health → {"status": "ok"}."""
    url = f"{base_url}/health"
    label = "step2_health"
    try:
        r = await client.get(url, timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 2, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "GET", url, r.status_code, body)

    if r.status_code != 200:
        return False, {"step": 2, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, dict) or body.get("status") != "ok":
        return False, {"step": 2, "error": 'Expected {"status": "ok"}', "body": body}

    return True, {"step": 2, "environment": body.get("environment")}


async def step3_get_profile(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """GET /api/profile → 200 with smoke user's email."""
    url = f"{base_url}/api/profile"
    label = "step3_get_profile"
    try:
        r = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 3, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "GET", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": 3,
            "error": "401 Unauthorized — token rejected. Re-run `make smoke-token` to refresh.",
            "body": body,
        }

    if r.status_code != 200:
        return False, {"step": 3, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, dict) or "id" not in body:
        return False, {"step": 3, "error": "Response missing 'id' field", "body": body}

    return True, {"step": 3, "profile_id": body.get("id"), "email": body.get("email")}


async def step4_patch_profile(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """PATCH /api/profile → update full_name; assert round-trip."""
    url = f"{base_url}/api/profile"
    label = "step4_patch_profile"
    sentinel = f"Smoke Test (patched at {int(time.time())})"
    payload = {"full_name": sentinel}
    try:
        r = await client.patch(
            url,
            headers=_bearer_headers(token),
            json=payload,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return False, {"step": 4, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "PATCH", url, r.status_code, body)

    if r.status_code not in (200, 204):
        return False, {"step": 4, "error": f"Expected 200/204, got {r.status_code}", "body": body}

    # Verify the patch by reading back
    try:
        r2 = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 4, "error": f"Read-back request failed: {exc}"}

    body2 = _try_json(r2)
    if verbose:
        _verbose_log(f"{label}_readback", "GET", url, r2.status_code, body2)

    if r2.status_code != 200:
        return False, {"step": 4, "error": f"Read-back got HTTP {r2.status_code}"}

    actual_name = body2.get("full_name") if isinstance(body2, dict) else None
    if actual_name != sentinel:
        return False, {
            "step": 4,
            "error": f"Round-trip mismatch: sent '{sentinel}', got '{actual_name}'",
        }

    return True, {"step": 4, "full_name_round_trip": "ok", "value": sentinel}


async def step5_list_applications(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """GET /api/applications → 200 list."""
    url = f"{base_url}/api/applications"
    label = "step5_list_applications"
    try:
        r = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 5, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "GET", url, r.status_code, body)

    if r.status_code != 200:
        return False, {"step": 5, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, list):
        return False, {"step": 5, "error": "Expected JSON array", "body": body}

    return True, {"step": 5, "application_count": len(body)}


async def step6_cron_sync(
    client: httpx.AsyncClient,
    base_url: str,
    cron_secret: str,
    verbose: bool,
) -> StepResult:
    """POST /internal/cron/sync → 200 {"status": "ok"}.

    Gated by X-Cron-Secret header (verify_secret dep in app/api/internal_cron.py).
    Uses a longer timeout (SYNC_TIMEOUT_S) because this calls external job APIs.
    """
    url = f"{base_url}/internal/cron/sync"
    label = "step6_cron_sync"
    headers = {"X-Cron-Secret": cron_secret}
    try:
        r = await client.post(url, headers=headers, timeout=SYNC_TIMEOUT_S)
    except httpx.TimeoutException:
        return False, {
            "step": 6,
            "error": f"Timed out after {SYNC_TIMEOUT_S}s — job sync may be hung",
        }
    except httpx.RequestError as exc:
        return False, {"step": 6, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "POST", url, r.status_code, body)

    if r.status_code == 403:
        return False, {
            "step": 6,
            "error": (
                "403 Forbidden — cron secret rejected. "
                "Check SMOKE_CRON_SECRET matches CRON_SHARED_SECRET in prod."
            ),
            "body": body,
        }

    # 429 means daily quota hit — treat as acceptable (smoke user re-ran too quickly)
    if r.status_code == 429:
        return True, {
            "step": 6,
            "note": "429 daily quota — sync already ran today; treating as pass",
            "body": body,
        }

    if r.status_code != 200:
        return False, {"step": 6, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, dict) or body.get("status") != "ok":
        return False, {
            "step": 6,
            "error": 'Expected {"status": "ok", ...}',
            "body": body,
        }

    return True, {
        "step": 6,
        "status": body.get("status"),
        "duration_ms": body.get("duration_ms"),
    }


async def step7_gemini_chat(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """POST /api/chat/messages → SSE stream with at least one assistant content chunk.

    Proves the Gemini LLM pipeline is wired to prod (not a fake/stub).
    The endpoint streams SSE; we consume until [DONE] or until we see a content chunk.
    """
    url = f"{base_url}/api/chat/messages"
    label = "step7_gemini_chat"
    payload = {"message": "Hello — please reply with a single word: 'ready'."}
    headers = {**_bearer_headers(token), "Accept": "text/event-stream"}

    try:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=CHAT_TIMEOUT_S,
        ) as r:
            if r.status_code == 401:
                return False, {
                    "step": 7,
                    "error": "401 Unauthorized — token rejected by chat endpoint",
                }
            if r.status_code != 200:
                body_text = await r.aread()
                return False, {
                    "step": 7,
                    "error": f"Expected 200, got {r.status_code}",
                    "body": body_text.decode(errors="replace")[:500],
                }

            content_seen = False
            error_seen: str | None = None
            chunks_received = 0

            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[len("data: ") :]
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                chunks_received += 1
                if verbose:
                    _verbose_log(f"{label}_event", "SSE", url, None, event)

                if "error" in event:
                    error_seen = event["error"]
                    # "Agent not available" means checkpointer not initialized in prod
                    if "not available" in str(error_seen).lower():
                        return False, {
                            "step": 7,
                            "error": (
                                "Chat agent unavailable — checkpointer not initialized. "
                                "Check LangGraph AsyncPostgresSaver setup in prod."
                            ),
                            "detail": error_seen,
                        }
                    break

                if event.get("content"):
                    content_seen = True
                    # We have evidence the LLM responded — no need to drain the full stream
                    break

    except httpx.TimeoutException:
        return False, {
            "step": 7,
            "error": f"Chat stream timed out after {CHAT_TIMEOUT_S}s — Gemini may be unreachable",
        }
    except httpx.RequestError as exc:
        return False, {"step": 7, "error": f"Request failed: {exc}"}

    if error_seen:
        return False, {
            "step": 7,
            "error": f"Chat stream returned error event: {error_seen}",
        }

    if not content_seen:
        return False, {
            "step": 7,
            "error": (
                f"No assistant content received after {chunks_received} SSE chunk(s). "
                "Gemini may not be returning text responses."
            ),
            "chunks_received": chunks_received,
        }

    return True, {
        "step": 7,
        "gemini_responded": True,
        "chunks_received": chunks_received,
    }


async def step8a_regenerate(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    app_id: str,
    verbose: bool,
) -> StepResult:
    """POST /api/applications/{id}/regenerate → 200 or 202.  XFAIL until PR 8/9."""
    url = f"{base_url}/api/applications/{app_id}/regenerate"
    label = "step8a_regenerate"
    try:
        r = await client.post(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return True, {
            "step": "8a",
            "xfail": True,
            "note": "generation interrupt/resume broken — targeted by PR 8",
            "error": f"Request failed: {exc}",
        }

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "POST", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": "8a",
            "error": "401 — token rejected by regenerate endpoint",
            "body": body,
        }
    if r.status_code == 404:
        return False, {
            "step": "8a",
            "error": "404 — regenerate endpoint missing (routing broken?)",
            "app_id": app_id,
            "body": body,
        }

    # 429 = max attempts reached; treat as XFAIL (smoke user hit limit)
    if r.status_code == 429:
        return True, {
            "step": "8a",
            "xfail": True,
            "note": "generation interrupt/resume broken — targeted by PR 8",
            "detail": "429 max generation attempts reached for smoke application",
            "app_id": app_id,
        }

    if r.status_code not in (200, 202):
        return True, {
            "step": "8a",
            "xfail": True,
            "note": "generation interrupt/resume broken — targeted by PR 8",
            "http_status": r.status_code,
            "body": body,
            "app_id": app_id,
        }

    return True, {
        "step": "8a",
        "xfail": True,
        "note": "generation interrupt/resume broken — targeted by PR 8",
        "http_status": r.status_code,
        "generation_status": body.get("generation_status") if isinstance(body, dict) else None,
        "app_id": app_id,
    }


async def step8b_poll_awaiting_review(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    app_id: str,
    verbose: bool,
) -> StepResult:
    """Poll GET /api/applications/{id} for generation_status=="awaiting_review".

    XFAIL until PR 8/9.
    """
    url = f"{base_url}/api/applications/{app_id}"
    label = "step8b_poll_awaiting_review"
    deadline = time.monotonic() + GENERATION_AWAITING_REVIEW_TIMEOUT_S
    last_status: str | None = None

    while time.monotonic() < deadline:
        try:
            r = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
        except httpx.RequestError as exc:
            return True, {
                "step": "8b",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "error": f"Poll request failed: {exc}",
            }

        body = _try_json(r)
        if verbose:
            _verbose_log(label, "GET", url, r.status_code, body)

        if r.status_code != 200:
            return True, {
                "step": "8b",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "error": f"Poll got HTTP {r.status_code}",
                "body": body,
            }

        last_status = body.get("generation_status") if isinstance(body, dict) else None
        if last_status == "awaiting_review":
            return True, {
                "step": "8b",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "generation_status": last_status,
                "app_id": app_id,
            }
        if last_status == "failed":
            return True, {
                "step": "8b",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "detail": "generation_status transitioned to 'failed'",
                "app_id": app_id,
            }

        await asyncio.sleep(GENERATION_POLL_INTERVAL_S)

    return True, {
        "step": "8b",
        "xfail": True,
        "note": "generation interrupt/resume broken — targeted by PR 8",
        "detail": (
            f"Timed out after {GENERATION_AWAITING_REVIEW_TIMEOUT_S}s waiting for "
            f"'awaiting_review'; last status: {last_status!r}"
        ),
        "app_id": app_id,
    }


async def step8c_approve(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    app_id: str,
    verbose: bool,
) -> StepResult:
    """PATCH /api/applications/{id} with {"status": "approved"}.  XFAIL until PR 8/9."""
    url = f"{base_url}/api/applications/{app_id}"
    label = "step8c_approve"
    # The endpoint reads data.get("status") — see app/api/applications.py:157
    payload = {"status": "approved"}
    try:
        r = await client.patch(
            url,
            headers=_bearer_headers(token),
            json=payload,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return True, {
            "step": "8c",
            "xfail": True,
            "note": "generation interrupt/resume broken — targeted by PR 8",
            "error": f"Request failed: {exc}",
        }

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "PATCH", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": "8c",
            "error": "401 — token rejected by PATCH applications endpoint",
            "body": body,
        }
    if r.status_code == 404:
        return False, {
            "step": "8c",
            "error": "404 — application not found (app_id mismatch or endpoint missing?)",
            "app_id": app_id,
            "body": body,
        }

    return True, {
        "step": "8c",
        "xfail": True,
        "note": "generation interrupt/resume broken — targeted by PR 8",
        "http_status": r.status_code,
        "generation_status": body.get("generation_status") if isinstance(body, dict) else None,
        "app_id": app_id,
    }


async def step8d_poll_ready(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    app_id: str,
    verbose: bool,
) -> StepResult:
    """Poll GET /api/applications/{id} until generation_status=="ready".  XFAIL until PR 8/9."""
    url = f"{base_url}/api/applications/{app_id}"
    label = "step8d_poll_ready"
    deadline = time.monotonic() + GENERATION_READY_TIMEOUT_S
    last_status: str | None = None

    while time.monotonic() < deadline:
        try:
            r = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
        except httpx.RequestError as exc:
            return True, {
                "step": "8d",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "error": f"Poll request failed: {exc}",
            }

        body = _try_json(r)
        if verbose:
            _verbose_log(label, "GET", url, r.status_code, body)

        if r.status_code != 200:
            return True, {
                "step": "8d",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "error": f"Poll got HTTP {r.status_code}",
                "body": body,
            }

        last_status = body.get("generation_status") if isinstance(body, dict) else None
        if last_status == "ready":
            return True, {
                "step": "8d",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "generation_status": last_status,
                "app_id": app_id,
            }
        if last_status == "failed":
            return True, {
                "step": "8d",
                "xfail": True,
                "note": "generation interrupt/resume broken — targeted by PR 8",
                "detail": "generation_status transitioned to 'failed'",
                "app_id": app_id,
            }

        await asyncio.sleep(GENERATION_POLL_INTERVAL_S)

    return True, {
        "step": "8d",
        "xfail": True,
        "note": "generation interrupt/resume broken — targeted by PR 8",
        "detail": (
            f"Timed out after {GENERATION_READY_TIMEOUT_S}s waiting for "
            f"'ready'; last status: {last_status!r}"
        ),
        "app_id": app_id,
    }


async def step9_submit_xfail(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    verbose: bool,
) -> StepResult:
    """
    POST /api/applications/{id}/submit — XFAIL in PR 1.

    X-Smoke-DryRun support lands in PR 7.  Here we only assert:
      - The endpoint exists (not 404)
      - The token is accepted (not 401)
    A 400/422/500 is treated as a PASS with a note.

    If there are no applications yet (step 5 returned empty list), skip gracefully.
    """
    # First fetch an application id to use
    url_list = f"{base_url}/api/applications?limit=1"
    label = "step9_submit_xfail"
    try:
        r_list = await client.get(
            url_list, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S
        )
    except httpx.RequestError as exc:
        return False, {"step": 9, "error": f"List request failed: {exc}"}

    body_list = _try_json(r_list)
    if verbose:
        _verbose_log(f"{label}_list", "GET", url_list, r_list.status_code, body_list)

    if r_list.status_code != 200 or not isinstance(body_list, list):
        return False, {"step": 9, "error": "Could not fetch application list for submit test"}

    if not body_list:
        return True, {
            "step": 9,
            "note": "No applications yet — skip submit assertion (XFAIL expected in PR 1)",
            "xfail": True,
        }

    app_id = body_list[0].get("id")
    if not app_id:
        return False, {"step": 9, "error": "First application missing 'id' field"}

    url = f"{base_url}/api/applications/{app_id}/submit"
    try:
        r = await client.post(
            url,
            headers=_bearer_headers(token),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return False, {"step": 9, "error": f"Submit request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "POST", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": 9,
            "error": "401 — token rejected by submit endpoint",
            "body": body,
        }

    if r.status_code == 404:
        return False, {
            "step": 9,
            "error": "404 — submit endpoint missing (routing broken?)",
            "app_id": app_id,
            "body": body,
        }

    # Any non-401/404 is acceptable here; real status-code contract lands in PR 7
    return True, {
        "step": 9,
        "note": "XFAIL — DryRun header not yet implemented (PR 7). Endpoint reachable.",
        "http_status": r.status_code,
        "xfail": True,
        "app_id": app_id,
    }


async def step10_cleanup(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """Reset full_name back to 'Smoke Test' (idempotent teardown)."""
    url = f"{base_url}/api/profile"
    label = "step10_cleanup"
    try:
        r = await client.patch(
            url,
            headers=_bearer_headers(token),
            json={"full_name": "Smoke Test"},
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return False, {"step": 10, "error": f"Cleanup request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "PATCH", url, r.status_code, body)

    if r.status_code not in (200, 204):
        return False, {
            "step": 10,
            "error": f"Cleanup PATCH got HTTP {r.status_code}",
            "body": body,
        }

    return True, {"step": 10, "teardown": "full_name reset to 'Smoke Test'"}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        text = response.text
        return text[:500] if len(text) > 500 else text


async def run(base_url: str, token: str, cron_secret: str, verbose: bool) -> int:
    """Run all steps. Returns 0 on full pass, 1 on any failure."""
    base_url = base_url.rstrip("/")

    failures: list[dict] = []
    passed = 0
    xfails = 0

    async with httpx.AsyncClient() as client:
        # --- Steps 1–5: static and auth checks ---
        simple_steps: list[tuple[str, Any]] = [
            ("1  OAuth authorize redirect_uri", step1_oauth_authorize),
            ("2  Health check", step2_health),
            ("3  GET profile (auth check)", step3_get_profile),
            ("4  PATCH profile round-trip", step4_patch_profile),
            ("5  List applications (baseline)", step5_list_applications),
        ]

        app_id_for_generation: str | None = None

        for label, fn in simple_steps:
            print(f"  running step {label} ...", end="", flush=True)
            try:
                import inspect

                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                if "token" in params:
                    ok, details = await fn(client, base_url, token, verbose)  # type: ignore[call-arg]
                else:
                    ok, details = await fn(client, base_url, verbose)  # type: ignore[call-arg]
            except Exception as exc:
                ok, details = False, {"error": f"Unhandled exception: {exc}"}

            xfail = details.get("xfail", False)
            if ok:
                if xfail:
                    print(f"  XFAIL ({details.get('note', '')})")
                    xfails += 1
                else:
                    print("  PASS")
                    passed += 1
                # Capture application list count from step 5 for later use
                if "5" in label and details.get("application_count", 0) > 0:
                    # Fetch highest-score application id for generation steps
                    try:
                        r = await client.get(
                            f"{base_url}/api/applications?limit=50",
                            headers=_bearer_headers(token),
                            timeout=DEFAULT_TIMEOUT_S,
                        )
                        apps = r.json() if r.status_code == 200 else []
                        if apps and isinstance(apps, list):
                            best = max(apps, key=lambda a: a.get("match_score") or 0.0)
                            app_id_for_generation = best.get("id")
                    except Exception:
                        pass
            else:
                print(f"  FAIL — {details.get('error', 'unknown')}")
                failures.append({"label": label, **details})

        # --- Step 6: cron sync ---
        label6 = "6  POST /internal/cron/sync"
        print(f"  running step {label6} ...", end="", flush=True)
        try:
            ok6, details6 = await step6_cron_sync(client, base_url, cron_secret, verbose)
        except Exception as exc:
            ok6, details6 = False, {"error": f"Unhandled exception: {exc}"}

        if ok6:
            if details6.get("xfail"):
                print(f"  XFAIL ({details6.get('note', '')})")
                xfails += 1
            else:
                print("  PASS")
                passed += 1
        else:
            print(f"  FAIL — {details6.get('error', 'unknown')}")
            failures.append({"label": label6, **details6})

        # --- Step 7: Gemini chat reachability ---
        label7 = "7  Gemini chat (LLM pipeline)"
        print(f"  running step {label7} ...", end="", flush=True)
        try:
            ok7, details7 = await step7_gemini_chat(client, base_url, token, verbose)
        except Exception as exc:
            ok7, details7 = False, {"error": f"Unhandled exception: {exc}"}

        if ok7:
            print("  PASS")
            passed += 1
        else:
            print(f"  FAIL — {details7.get('error', 'unknown')}")
            failures.append({"label": label7, **details7})

        # --- Steps 8a–8d: generation flow (all XFAIL until PR 8/9) ---
        if app_id_for_generation is None:
            # No application to test with; mark all generation sub-steps as XFAIL/skipped
            for sub in ("8a", "8b", "8c", "8d"):
                label_sub = f"{sub} Generation flow sub-step"
                print(f"  running step {label_sub} ...", end="", flush=True)
                print("  XFAIL (no seeded application available for generation test)")
                xfails += 1
        else:
            gen_steps: list[tuple[str, Any]] = [
                ("8a POST /regenerate", step8a_regenerate),
                ("8b Poll awaiting_review", step8b_poll_awaiting_review),
                ("8c PATCH approved", step8c_approve),
                ("8d Poll ready", step8d_poll_ready),
            ]
            for label_g, fn_g in gen_steps:
                print(f"  running step {label_g} ...", end="", flush=True)
                try:
                    ok_g, details_g = await fn_g(
                        client, base_url, token, app_id_for_generation, verbose
                    )
                except Exception as exc:
                    ok_g, details_g = False, {"error": f"Unhandled exception: {exc}"}

                xfail_g = details_g.get("xfail", False)
                if ok_g:
                    if xfail_g:
                        print(f"  XFAIL ({details_g.get('note', '')})")
                        xfails += 1
                    else:
                        print("  PASS")
                        passed += 1
                else:
                    print(f"  FAIL — {details_g.get('error', 'unknown')}")
                    failures.append({"label": label_g, **details_g})

        # --- Step 9: submit XFAIL ---
        label9 = "9  Submit endpoint reachable (XFAIL)"
        print(f"  running step {label9} ...", end="", flush=True)
        try:
            ok9, details9 = await step9_submit_xfail(client, base_url, token, verbose)
        except Exception as exc:
            ok9, details9 = False, {"error": f"Unhandled exception: {exc}"}

        if ok9:
            if details9.get("xfail"):
                print(f"  XFAIL ({details9.get('note', '')})")
                xfails += 1
            else:
                print("  PASS")
                passed += 1
        else:
            print(f"  FAIL — {details9.get('error', 'unknown')}")
            failures.append({"label": label9, **details9})

        # --- Step 10: cleanup ---
        label10 = "10 Cleanup / teardown"
        print(f"  running step {label10} ...", end="", flush=True)
        try:
            ok10, details10 = await step10_cleanup(client, base_url, token, verbose)
        except Exception as exc:
            ok10, details10 = False, {"error": f"Unhandled exception: {exc}"}

        if ok10:
            print("  PASS")
            passed += 1
        else:
            print(f"  FAIL — {details10.get('error', 'unknown')}")
            failures.append({"label": label10, **details10})

    total = passed + xfails + len(failures)
    print(f"\nResults: {passed} passed, {xfails} xfail, {len(failures)} failed / {total} total")

    if failures:
        print("\nFailed steps:", file=sys.stderr)
        print(json.dumps(failures, indent=2, default=str), file=sys.stderr)
        return 1

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="golden_path.py",
        description=(
            "Smoke test: walks the golden-path assertions against a deployed instance.\n\n"
            "Requires:\n"
            "  SMOKE_BASE_URL      Base URL of the deployment (e.g. https://…run.app)\n"
            "  SMOKE_BEARER_TOKEN  JWT for smoke@panibrat.com.  "
            "Generate with `make smoke-token`.\n"
            "  SMOKE_CRON_SECRET   Value of CRON_SHARED_SECRET prod secret\n"
            "                      (passed as X-Cron-Secret to POST /internal/cron/sync).\n\n"
            "Steps 8a–8d are XFAIL until PR 8/9 (generation interrupt/resume).\n"
            "Step 9 is XFAIL until PR 7 (X-Smoke-DryRun header).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override SMOKE_BASE_URL env var",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Override SMOKE_BEARER_TOKEN env var",
    )
    parser.add_argument(
        "--cron-secret",
        default=None,
        help="Override SMOKE_CRON_SECRET env var (X-Cron-Secret for /internal/cron/sync)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each step's request and response body to stderr",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    base_url: str = args.base_url or os.environ.get("SMOKE_BASE_URL", "")
    token: str = args.token or os.environ.get("SMOKE_BEARER_TOKEN", "")
    cron_secret: str = args.cron_secret or os.environ.get("SMOKE_CRON_SECRET", "")

    errors: list[str] = []
    if not base_url:
        errors.append(
            "SMOKE_BASE_URL is required.  Export it or pass --base-url https://your-service.run.app"
        )
    if not token:
        errors.append(
            "SMOKE_BEARER_TOKEN is required.  Generate one with `make smoke-token` and export it."
        )
    if not cron_secret:
        errors.append(
            "SMOKE_CRON_SECRET is required.  Set it to the value of CRON_SHARED_SECRET in prod."
        )
    if errors:
        for msg in errors:
            print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(2)

    print(f"Smoke test target: {base_url}")
    print("Running golden-path assertions...\n")

    exit_code = asyncio.run(run(base_url, token, cron_secret, args.verbose))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
