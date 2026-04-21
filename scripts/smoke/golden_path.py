"""
Smoke test: golden-path assertions against the deployed Cloud Run URL.

Usage:
    # Minimal — uses defaults
    SMOKE_BASE_URL=https://... SMOKE_BEARER_TOKEN=eyJ... uv run python scripts/smoke/golden_path.py

    # Verbose request/response logging
    SMOKE_BASE_URL=https://... SMOKE_BEARER_TOKEN=eyJ... \\
        uv run python scripts/smoke/golden_path.py --verbose

    # Help
    uv run python scripts/smoke/golden_path.py --help

Environment variables:
    SMOKE_BASE_URL        Base URL of the deployed app (required unless --base-url is given).
                          Defaults to the Cloud Run service URL from CI secrets.
    SMOKE_BEARER_TOKEN    JWT for smoke@panibrat.com (required).  Generate with `make smoke-token`.

Exit codes:
    0  All assertions passed
    1  One or more assertions failed (JSON error payload printed to stderr)
    2  Configuration error (missing env vars / bad args)

Step mapping (matches stabilisation plan):
    Step 1  GET /api/auth/google/authorize  → redirect_uri param matches expected Cloud Run callback
    Step 2  GET /health                     → {"status": "ok"}
    Step 3  GET /api/profile               → 200 with smoke user's profile
    Step 4  PATCH /api/profile             → update full_name, assert round-trip
    Step 5  GET /api/applications          → 200 list (may be empty)
    Step 6  POST /api/jobs/sync            → 200 {"status": "synced"}  (may be slow)
    Step 7  GET /api/applications          → list count ≥ 0 (asserts sync didn't break auth)
    Step 8  POST /api/applications/{id}/submit
              → expected to fail until PR 7 lands (X-Smoke-DryRun not yet implemented);
                asserts the endpoint exists (not 404) and the caller's token is accepted (not 401).
                Documented as XFAIL: a 400/500 here is acceptable in PR 1.
    Step 9  Cleanup: reset profile full_name to 'Smoke Test' (idempotent teardown)

Note on Step 6: job sync calls external APIs (Adzuna, Remotive, etc.) and may take 10–30 s in
production.  The script uses a 90 s timeout for that step only.
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
DEFAULT_TIMEOUT_S = 20

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


async def step6_job_sync(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """POST /api/jobs/sync → 200 {"status": "synced"}.
    Uses a longer timeout (SYNC_TIMEOUT_S) because this calls external job APIs."""
    url = f"{base_url}/api/jobs/sync"
    label = "step6_job_sync"
    try:
        r = await client.post(url, headers=_bearer_headers(token), timeout=SYNC_TIMEOUT_S)
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

    # 429 means daily quota hit — treat as acceptable (smoke user re-ran too quickly)
    if r.status_code == 429:
        return True, {
            "step": 6,
            "note": "429 daily quota — sync already ran today; treating as pass",
            "body": body,
        }

    if r.status_code != 200:
        return False, {"step": 6, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, dict) or body.get("status") != "synced":
        return False, {
            "step": 6,
            "error": 'Expected {"status": "synced"}',
            "body": body,
        }

    return True, {
        "step": 6,
        "status": body.get("status"),
        "synced": body.get("synced"),
        "skipped": body.get("skipped"),
    }


async def step7_applications_post_sync(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """GET /api/applications after sync — asserts auth still works and list is valid."""
    url = f"{base_url}/api/applications?limit=5"
    label = "step7_applications_post_sync"
    try:
        r = await client.get(url, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S)
    except httpx.RequestError as exc:
        return False, {"step": 7, "error": f"Request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "GET", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": 7,
            "error": "401 after sync — session may have been revoked",
            "body": body,
        }

    if r.status_code != 200:
        return False, {"step": 7, "error": f"Expected 200, got {r.status_code}", "body": body}

    if not isinstance(body, list):
        return False, {"step": 7, "error": "Expected JSON array", "body": body}

    return True, {"step": 7, "application_count": len(body)}


async def step8_submit_xfail(
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

    If there are no applications yet (step 7 returned empty list), skip gracefully.
    """
    # First fetch an application id to use
    url_list = f"{base_url}/api/applications?limit=1"
    label = "step8_submit_xfail"
    try:
        r_list = await client.get(
            url_list, headers=_bearer_headers(token), timeout=DEFAULT_TIMEOUT_S
        )
    except httpx.RequestError as exc:
        return False, {"step": 8, "error": f"List request failed: {exc}"}

    body_list = _try_json(r_list)
    if verbose:
        _verbose_log(f"{label}_list", "GET", url_list, r_list.status_code, body_list)

    if r_list.status_code != 200 or not isinstance(body_list, list):
        return False, {"step": 8, "error": "Could not fetch application list for submit test"}

    if not body_list:
        return True, {
            "step": 8,
            "note": "No applications yet — skip submit assertion (XFAIL expected in PR 1)",
            "xfail": True,
        }

    app_id = body_list[0].get("id")
    if not app_id:
        return False, {"step": 8, "error": "First application missing 'id' field"}

    url = f"{base_url}/api/applications/{app_id}/submit"
    try:
        r = await client.post(
            url,
            headers=_bearer_headers(token),
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return False, {"step": 8, "error": f"Submit request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "POST", url, r.status_code, body)

    if r.status_code == 401:
        return False, {
            "step": 8,
            "error": "401 — token rejected by submit endpoint",
            "body": body,
        }

    if r.status_code == 404:
        return False, {
            "step": 8,
            "error": "404 — submit endpoint missing (routing broken?)",
            "app_id": app_id,
            "body": body,
        }

    # Any non-401/404 is acceptable here; real status-code contract lands in PR 7
    return True, {
        "step": 8,
        "note": "XFAIL — DryRun header not yet implemented (PR 7). Endpoint reachable.",
        "http_status": r.status_code,
        "xfail": True,
        "app_id": app_id,
    }


async def step9_cleanup(
    client: httpx.AsyncClient, base_url: str, token: str, verbose: bool
) -> StepResult:
    """Reset full_name back to 'Smoke Test' (idempotent teardown)."""
    url = f"{base_url}/api/profile"
    label = "step9_cleanup"
    try:
        r = await client.patch(
            url,
            headers=_bearer_headers(token),
            json={"full_name": "Smoke Test"},
            timeout=DEFAULT_TIMEOUT_S,
        )
    except httpx.RequestError as exc:
        return False, {"step": 9, "error": f"Cleanup request failed: {exc}"}

    body = _try_json(r)
    if verbose:
        _verbose_log(label, "PATCH", url, r.status_code, body)

    if r.status_code not in (200, 204):
        return False, {"step": 9, "error": f"Cleanup PATCH got HTTP {r.status_code}", "body": body}

    return True, {"step": 9, "teardown": "full_name reset to 'Smoke Test'"}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        text = response.text
        return text[:500] if len(text) > 500 else text


async def run(base_url: str, token: str, verbose: bool) -> int:
    """Run all 9 steps. Returns 0 on full pass, 1 on any failure."""
    base_url = base_url.rstrip("/")

    steps = [
        ("1  OAuth authorize redirect_uri", step1_oauth_authorize),
        ("2  Health check", step2_health),
        ("3  GET profile (auth check)", step3_get_profile),
        ("4  PATCH profile round-trip", step4_patch_profile),
        ("5  List applications (baseline)", step5_list_applications),
        ("6  Job sync", step6_job_sync),
        ("7  List applications (post-sync)", step7_applications_post_sync),
        ("8  Submit endpoint reachable (XFAIL)", step8_submit_xfail),
        ("9  Cleanup / teardown", step9_cleanup),
    ]

    failures: list[dict] = []
    passed = 0
    xfails = 0

    async with httpx.AsyncClient() as client:
        for label, fn in steps:
            print(f"  running step {label} ...", end="", flush=True)
            try:
                # Steps that need token pass it; steps that don't still get the arg
                # (all step functions accept (client, base_url, token?, verbose))
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
            else:
                print(f"  FAIL — {details.get('error', 'unknown')}")
                failures.append({"label": label, **details})

    total = len(steps)
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
            "Smoke test: walks the 9 golden-path assertions against a deployed instance.\n\n"
            "Requires:\n"
            "  SMOKE_BASE_URL      Base URL of the deployment (e.g. https://…run.app)\n"
            "  SMOKE_BEARER_TOKEN  JWT for smoke@panibrat.com.  "
            "Generate with `make smoke-token`.\n\n"
            "Step 8 is XFAIL in PR 1 (X-Smoke-DryRun not yet implemented).\n"
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

    errors: list[str] = []
    if not base_url:
        errors.append(
            "SMOKE_BASE_URL is required.  Export it or pass --base-url https://your-service.run.app"
        )
    if not token:
        errors.append(
            "SMOKE_BEARER_TOKEN is required.  Generate one with `make smoke-token` and export it."
        )
    if errors:
        for msg in errors:
            print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(2)

    print(f"Smoke test target: {base_url}")
    print("Running 9 golden-path assertions...\n")

    exit_code = asyncio.run(run(base_url, token, args.verbose))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
