import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.feedback_report import FeedbackReport
from app.models.user import User

log = structlog.get_logger()

ALLOWED_CATEGORIES = frozenset({"feature_request", "bug", "other"})

STATUS_PENDING = "pending"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"

MAX_MESSAGE_LENGTH = 5000
MAX_DIAGNOSTICS_BYTES = 16 * 1024
MAX_WEBHOOK_MESSAGE_PREVIEW = 240
MAX_NOTIFICATION_ERROR = 512

_STRING_LIMITS = {
    "reported_at_client": 64,
    "path": 512,
    "page_title": 256,
    "user_agent": 512,
    "timezone": 128,
}
_DIAGNOSTIC_KEYS = (
    "reported_at_client",
    "path",
    "page_title",
    "user_agent",
    "viewport",
    "timezone",
    "route_context",
)


class FeedbackValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FeedbackSubmissionResult:
    id: UUID
    notification_status: str


def _truncate(value: str, limit: int) -> str:
    return value[:limit]


def _is_integer(value: Any) -> bool:
    return type(value) is int


def _webhook_url(settings: Settings) -> str | None:
    if settings.feedback_webhook_url is None:
        return None
    value = settings.feedback_webhook_url.get_secret_value().strip()
    return value or None


def validate_category(category: str) -> str:
    if category not in ALLOWED_CATEGORIES:
        raise FeedbackValidationError("Invalid feedback category")
    return category


def validate_message(message: str) -> str:
    trimmed = message.strip()
    if not trimmed:
        raise FeedbackValidationError("Feedback message is required")
    if len(trimmed) > MAX_MESSAGE_LENGTH:
        raise FeedbackValidationError("Feedback message is too long")
    return trimmed


def sanitize_diagnostics(diagnostics: Any) -> dict[str, Any]:
    if diagnostics is None:
        return {}
    if not isinstance(diagnostics, dict):
        raise FeedbackValidationError("Diagnostics must be an object")

    sanitized: dict[str, Any] = {}
    for key in _DIAGNOSTIC_KEYS:
        if key not in diagnostics:
            continue
        value = diagnostics[key]
        if key in _STRING_LIMITS and isinstance(value, str):
            sanitized[key] = _truncate(value, _STRING_LIMITS[key])
        elif key == "viewport" and isinstance(value, dict):
            width = value.get("width")
            height = value.get("height")
            if _is_integer(width) and _is_integer(height):
                sanitized["viewport"] = {"width": width, "height": height}
        elif key == "route_context" and isinstance(value, dict):
            route_context: dict[str, str] = {}
            for route_key, route_value in value.items():
                if isinstance(route_key, str) and isinstance(route_value, str):
                    route_context[_truncate(route_key, 128)] = _truncate(route_value, 128)
                if len(route_context) >= 64:
                    break
            sanitized["route_context"] = route_context

    encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > MAX_DIAGNOSTICS_BYTES:
        raise FeedbackValidationError("Diagnostics payload is too large")
    return sanitized


def build_webhook_payload(report: FeedbackReport) -> dict[str, Any]:
    return {
        "event": "feedback.submitted",
        "feedback_id": str(report.id),
        "user_id": str(report.user_id),
        "user_email": report.user_email,
        "category": report.category,
        "message_preview": report.message[:MAX_WEBHOOK_MESSAGE_PREVIEW],
        "path": report.diagnostics.get("path"),
        "diagnostics": report.diagnostics,
        "created_at": report.created_at.isoformat(),
    }


def _bounded_error(error: str) -> str:
    return error[:MAX_NOTIFICATION_ERROR]


async def create_feedback_report(
    *,
    user: User,
    category: str,
    message: str,
    diagnostics: Any,
    session: AsyncSession,
    settings: Settings,
) -> FeedbackSubmissionResult:
    category = validate_category(category)
    message = validate_message(message)
    sanitized_diagnostics = sanitize_diagnostics(diagnostics)
    webhook_configured = _webhook_url(settings) is not None
    report = FeedbackReport(
        user_id=user.id,
        user_email=user.email,
        category=category,
        message=message,
        diagnostics=sanitized_diagnostics,
        notification_status=STATUS_PENDING if webhook_configured else STATUS_NOT_CONFIGURED,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    response_id = report.id
    response_status = report.notification_status
    await log.ainfo(
        "feedback.submitted",
        feedback_report_id=str(response_id),
        user_id=str(user.id),
        category=category,
        notification_status=response_status,
    )

    if webhook_configured:
        response_status = await dispatch_feedback_webhook(
            report, session=session, settings=settings
        )
    return FeedbackSubmissionResult(id=response_id, notification_status=response_status)


async def dispatch_feedback_webhook(
    report: FeedbackReport,
    *,
    session: AsyncSession,
    settings: Settings,
) -> str:
    webhook_url = _webhook_url(settings)
    if webhook_url is None:
        return report.notification_status

    feedback_id = str(report.id)
    payload = build_webhook_payload(report)
    status = STATUS_SENT
    error = None
    try:
        async with httpx.AsyncClient(
            timeout=settings.feedback_webhook_timeout_seconds
        ) as client:
            response = await client.post(webhook_url, json=payload)
        if not 200 <= response.status_code < 300:
            status = STATUS_FAILED
            error = _bounded_error(f"Webhook returned HTTP {response.status_code}")
    except Exception as exc:
        status = STATUS_FAILED
        error = _bounded_error(f"{type(exc).__name__}: {exc}")

    report.notification_status = status
    report.notification_error = error
    try:
        await session.commit()
    except Exception as exc:
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            await log.aerror(
                "feedback.notification_status_update_failed",
                feedback_report_id=feedback_id,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        return status

    log_data = {
        "feedback_report_id": feedback_id,
        "notification_status": status,
    }
    try:
        if status == STATUS_SENT:
            await log.ainfo("feedback.notification_sent", **log_data)
        else:
            await log.aerror(
                "feedback.notification_failed",
                **log_data,
                notification_error=error,
            )
    except Exception:
        pass
    return status
