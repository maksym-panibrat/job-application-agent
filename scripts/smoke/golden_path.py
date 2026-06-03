#!/usr/bin/env python3
"""Current-contract production smoke test.

This script intentionally checks only behavior that exists today. It fails on
real regressions instead of carrying historical XFAIL/PR placeholders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 30
SYNC_TIMEOUT_S = 240
GENERATION_TIMEOUT_S = 120
POLL_INTERVAL_S = 3


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _try_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:500]


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, Any]:
    response = await client.request(method, url, headers=headers, json=json_body, timeout=timeout)
    return response.status_code, _try_json(response)


async def _step_health(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    status, body = await _request_json(client, "GET", f"{base_url}/health")
    if status != 200:
        raise AssertionError(f"/health returned HTTP {status}: {body}")
    if not isinstance(body, dict) or body.get("status") not in {"ok", "healthy"}:
        raise AssertionError(f"unexpected /health body: {body}")
    return {"status": body.get("status")}


async def _step_profile(client: httpx.AsyncClient, base_url: str, token: str) -> dict[str, Any]:
    headers = _bearer_headers(token)
    status, profile = await _request_json(client, "GET", f"{base_url}/api/profile", headers=headers)
    if status != 200 or not isinstance(profile, dict):
        raise AssertionError(f"GET /api/profile returned HTTP {status}: {profile}")

    original = profile.get("full_name") or "Smoke Test"
    probe = "Smoke Test"
    status, updated = await _request_json(
        client,
        "PATCH",
        f"{base_url}/api/profile",
        headers=headers,
        json_body={"full_name": probe},
    )
    if status not in {200, 204}:
        raise AssertionError(f"PATCH /api/profile returned HTTP {status}: {updated}")

    # Best-effort reset. The prior PATCH proves the endpoint works; reset errors
    # should be surfaced but do not hide the round-trip result details.
    reset_status, reset_body = await _request_json(
        client,
        "PATCH",
        f"{base_url}/api/profile",
        headers=headers,
        json_body={"full_name": original},
    )
    if reset_status not in {200, 204, 429}:
        raise AssertionError(f"profile reset returned HTTP {reset_status}: {reset_body}")
    return {"profile_id": profile.get("id"), "reset_status": reset_status}


async def _step_applications(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
) -> dict[str, Any]:
    headers = _bearer_headers(token)
    status, apps = await _request_json(
        client,
        "GET",
        f"{base_url}/api/applications?limit=20",
        headers=headers,
    )
    if status != 200 or not isinstance(apps, list):
        raise AssertionError(f"GET /api/applications returned HTTP {status}: {apps}")

    status, summary = await _request_json(
        client,
        "GET",
        f"{base_url}/api/applications/summary",
        headers=headers,
    )
    required = {"pending_review", "auto_rejected", "dismissed", "applied"}
    if status != 200 or not isinstance(summary, dict) or not required.issubset(summary):
        raise AssertionError(f"GET /api/applications/summary returned HTTP {status}: {summary}")
    return {"application_count": len(apps), "summary": summary}


async def _step_sync_status(client: httpx.AsyncClient, base_url: str, token: str) -> dict[str, Any]:
    status, body = await _request_json(
        client,
        "GET",
        f"{base_url}/api/sync/status",
        headers=_bearer_headers(token),
    )
    required = {"state", "slugs_total", "slugs_pending", "matches_pending", "batch_matches_pending"}
    if status != 200 or not isinstance(body, dict) or not required.issubset(body):
        raise AssertionError(f"GET /api/sync/status returned HTTP {status}: {body}")
    return {key: body.get(key) for key in sorted(required)}


async def _step_cron_sync(
    client: httpx.AsyncClient,
    base_url: str,
    cron_secret: str,
) -> dict[str, Any]:
    status, body = await _request_json(
        client,
        "POST",
        f"{base_url}/internal/cron/sync",
        headers={"X-Cron-Secret": cron_secret},
        timeout=SYNC_TIMEOUT_S,
    )
    if status != 202 or not isinstance(body, dict):
        raise AssertionError(f"POST /internal/cron/sync returned HTTP {status}: {body}")
    for key in ("enqueued", "pruned", "active_profiles"):
        if key not in body:
            raise AssertionError(f"cron sync response missing {key}: {body}")
    return body


async def _step_generation_if_available(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
) -> dict[str, Any]:
    headers = _bearer_headers(token)
    status, apps = await _request_json(
        client,
        "GET",
        f"{base_url}/api/applications?status=pending_review&limit=1",
        headers=headers,
    )
    if status != 200 or not isinstance(apps, list):
        raise AssertionError(
            f"could not fetch application for generation test: HTTP {status}: {apps}"
        )
    if not apps:
        return {"skipped": "no pending_review application available"}

    app_id = apps[0].get("id")
    if not app_id:
        raise AssertionError(f"application missing id: {apps[0]}")

    status, requested = await _request_json(
        client,
        "POST",
        f"{base_url}/api/applications/{app_id}/cover-letter",
        headers=headers,
    )
    if status not in {202, 409}:
        raise AssertionError(f"cover-letter request returned HTTP {status}: {requested}")

    deadline = asyncio.get_running_loop().time() + GENERATION_TIMEOUT_S
    last_body: Any = None
    while asyncio.get_running_loop().time() < deadline:
        poll_status, last_body = await _request_json(
            client,
            "GET",
            f"{base_url}/api/applications/{app_id}/cover-letter/status",
            headers=headers,
        )
        if poll_status != 200 or not isinstance(last_body, dict):
            raise AssertionError(f"cover-letter status returned HTTP {poll_status}: {last_body}")
        if last_body.get("status") in {"ready", "failed"}:
            return {"application_id": app_id, "status": last_body.get("status")}
        await asyncio.sleep(POLL_INTERVAL_S)

    raise AssertionError(f"cover-letter generation did not finish in time: {last_body}")


async def run(base_url: str, token: str, cron_secret: str, verbose: bool) -> int:
    base_url = base_url.rstrip("/")
    steps = [
        ("health", lambda c: _step_health(c, base_url)),
        ("profile", lambda c: _step_profile(c, base_url, token)),
        ("applications", lambda c: _step_applications(c, base_url, token)),
        ("sync_status", lambda c: _step_sync_status(c, base_url, token)),
        ("cron_sync", lambda c: _step_cron_sync(c, base_url, cron_secret)),
        ("generation", lambda c: _step_generation_if_available(c, base_url, token)),
    ]

    failures: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for name, fn in steps:
            print(f"  running {name} ...", end="", flush=True)
            try:
                details = await fn(client)
                print(" PASS")
                if verbose:
                    print(json.dumps({name: details}, indent=2, default=str), file=sys.stderr)
            except Exception as exc:
                print(f" FAIL — {exc}")
                failures.append({"step": name, "error": str(exc)})

    if failures:
        print("\nFailed steps:", file=sys.stderr)
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    print(f"\nResults: {len(steps)} passed, 0 failed")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="golden_path.py",
        description="Smoke test current production contracts against a deployed instance.",
    )
    parser.add_argument("--base-url", default=None, help="Override SMOKE_BASE_URL")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    base_url = args.base_url or os.getenv("SMOKE_BASE_URL")
    token = os.getenv("SMOKE_BEARER_TOKEN")
    cron_secret = os.getenv("SMOKE_CRON_SECRET")
    missing = [
        name
        for name, value in {
            "SMOKE_BASE_URL": base_url,
            "SMOKE_BEARER_TOKEN": token,
            "SMOKE_CRON_SECRET": cron_secret,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing required setting(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    assert base_url is not None
    assert token is not None
    assert cron_secret is not None
    return asyncio.run(run(base_url, token, cron_secret, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
