"""Security-critical domain services for the future multi-user Web edition."""

from .registration import (
    AuthConfig,
    InMemoryCaptchaVerifier,
    InMemoryGuardianConsentVerifier,
    InMemoryRegistrationStore,
    RecordingSmsSender,
    RegistrationResult,
    RegistrationService,
    SendCodeResult,
    normalize_cn_mobile,
)
from .asgi import AuthAsgiApp

__all__ = [
    "AuthConfig",
    "AuthAsgiApp",
    "InMemoryCaptchaVerifier",
    "InMemoryGuardianConsentVerifier",
    "InMemoryRegistrationStore",
    "RecordingSmsSender",
    "RegistrationResult",
    "RegistrationService",
    "SendCodeResult",
    "normalize_cn_mobile",
]
