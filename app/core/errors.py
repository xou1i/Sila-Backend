"""Error standard (07_api_design.md §9): every error is `{error_code, message, status}`.

This is the only place that builds error responses. Services raise `AppError(ErrorCode.X)`.
"""

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("sila.errors")


class ErrorCode(StrEnum):
    # Documented in 07_api_design.md §9
    INSUFFICIENT_AVAILABLE_WEIGHT = "INSUFFICIENT_AVAILABLE_WEIGHT"
    KYC_NOT_VERIFIED = "KYC_NOT_VERIFIED"
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    LISTING_NOT_ACTIVE = "LISTING_NOT_ACTIVE"
    INTEGRITY_CHECK_FAILED = "INTEGRITY_CHECK_FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    # Added (same format)
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    PRICE_CHANGED = "PRICE_CHANGED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_DEFAULTS: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.INSUFFICIENT_AVAILABLE_WEIGHT: (409, "الكمية تغيرت، حدّث الصفحة"),
    ErrorCode.KYC_NOT_VERIFIED: (403, "يجب إكمال التوثيق قبل إتمام العملية"),
    ErrorCode.SUBSCRIPTION_REQUIRED: (403, "هذه الميزة تتطلب اشتراك Premium فعّال"),
    ErrorCode.LISTING_NOT_ACTIVE: (409, "العرض غير متاح حالياً"),
    ErrorCode.INTEGRITY_CHECK_FAILED: (403, "تعذّر التحقق من سلامة سجل الملكية"),
    ErrorCode.VALIDATION_ERROR: (422, "البيانات المدخلة غير صالحة"),
    ErrorCode.UNAUTHORIZED: (401, "يجب تسجيل الدخول"),
    ErrorCode.INVALID_CREDENTIALS: (401, "البريد الإلكتروني أو كلمة المرور غير صحيحة"),
    ErrorCode.FORBIDDEN: (403, "ليس لديك صلاحية لهذه العملية"),
    ErrorCode.NOT_FOUND: (404, "العنصر المطلوب غير موجود"),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "الطريقة غير مسموحة لهذا المسار"),
    ErrorCode.EMAIL_ALREADY_EXISTS: (409, "هذا الإيميل مسجل مسبقاً"),
    ErrorCode.INVALID_STATUS_TRANSITION: (409, "لا يمكن تغيير حالة العرض بهذا الشكل"),
    ErrorCode.PRICE_CHANGED: (409, "انتهت صلاحية عرض السعر، حدّث المعاينة"),
    ErrorCode.PAYMENT_FAILED: (402, "فشلت عملية الدفع، حاول مرة أخرى"),
    ErrorCode.RATE_LIMITED: (429, "طلبات كثيرة، حاول بعد قليل"),
    ErrorCode.AI_UNAVAILABLE: (503, "الخدمة غير متاحة مؤقتاً، جرّب التصفح اليدوي"),
    ErrorCode.PRICE_UNAVAILABLE: (503, "أسعار السوق غير متاحة حالياً"),
    ErrorCode.INTERNAL_ERROR: (500, "حدث خطأ غير متوقع"),
}


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    """Body of every non-2xx response."""

    error_code: str
    message: str
    status: int
    details: list[FieldError] | None = None


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        default_status, default_message = _DEFAULTS[code]
        self.code = code
        self.status = status or default_status
        self.message = message or default_message
        self.headers = headers
        super().__init__(f"{code}: {self.message}")


def _body(code: ErrorCode, status: int, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error_code": code.value, "message": message, "status": status}
    if details is not None:
        body["details"] = details
    return body


_HTTP_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    429: ErrorCode.RATE_LIMITED,
}


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or "body"


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        _body(exc.code, exc.status, exc.message), status_code=exc.status, headers=exc.headers
    )


async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_TO_CODE.get(exc.status_code)
    if code is None:
        code = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_ERROR
    message = _DEFAULTS[code][1]
    return JSONResponse(
        _body(code, exc.status_code, message),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": _field_name(tuple(err.get("loc", ()))), "message": err.get("msg", "")}
        for err in exc.errors()
    ]
    status, message = _DEFAULTS[ErrorCode.VALIDATION_ERROR]
    return JSONResponse(_body(ErrorCode.VALIDATION_ERROR, status, message, details), status)


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    status, message = _DEFAULTS[ErrorCode.INTERNAL_ERROR]
    return JSONResponse(_body(ErrorCode.INTERNAL_ERROR, status, message), status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` helper documenting the standard error body."""
    return {s: {"model": ErrorResponse, "description": "Error"} for s in statuses}
