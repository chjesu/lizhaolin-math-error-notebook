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

__all__ = [
    "AuthConfig",
    "InMemoryCaptchaVerifier",
    "InMemoryGuardianConsentVerifier",
    "InMemoryRegistrationStore",
    "RecordingSmsSender",
    "RegistrationResult",
    "RegistrationService",
    "SendCodeResult",
    "normalize_cn_mobile",
]
