from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest

from services.web_auth import (
    AuthConfig,
    InMemoryCaptchaVerifier,
    InMemoryGuardianConsentVerifier,
    InMemoryRegistrationStore,
    RecordingSmsSender,
    RegistrationService,
    normalize_cn_mobile,
)
from services.web_auth.registration import RegistrationStatus, SendCodeStatus


NOW = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)


class RegistrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryRegistrationStore()
        self.sender = RecordingSmsSender()
        self.captcha = InMemoryCaptchaVerifier({"captcha-once"})
        self.guardian = InMemoryGuardianConsentVerifier({"guardian-consent-verified-001"})
        self.service = RegistrationService(
            store=self.store,
            sms_sender=self.sender,
            captcha_verifier=self.captcha,
            guardian_consent_verifier=self.guardian,
            secret_pepper=b"p" * 32,
            config=AuthConfig(captcha_after_phone_day=2, captcha_after_ip_hour=4),
        )

    def request(self, *, now: datetime = NOW, captcha: str | None = None):
        return self.service.request_code(
            phone="+86 138-0013-8000",
            ip_address="203.0.113.7",
            device_id="browser-device-001",
            captcha_token=captcha,
            now=now,
        )

    def test_normalizes_supported_cn_mobile_forms(self) -> None:
        self.assertEqual(normalize_cn_mobile("+86 138-0013-8000"), "13800138000")
        self.assertEqual(normalize_cn_mobile("8613800138000"), "13800138000")
        with self.assertRaises(ValueError):
            normalize_cn_mobile("12345")

    def test_plaintext_code_is_not_persisted_or_audited(self) -> None:
        result = self.request()
        code = self.sender.deliveries[0][1]
        persisted = json.dumps(
            {
                "challenge": vars(self.store.challenges[result.challenge_id]),
                "audit": [vars(item) for item in self.store.audit_events],
            },
            default=str,
        )
        self.assertNotIn(code, persisted)
        self.assertNotIn("13800138000", persisted)
        self.assertIn("138****8000", persisted)

    def test_resend_invalidates_previous_code(self) -> None:
        first = self.request()
        old_code = self.sender.deliveries[-1][1]
        second = self.request(now=NOW + timedelta(seconds=61))
        self.assertEqual(self.store.challenges[first.challenge_id].status, "cancelled")
        attempt = self.service.register(
            challenge_id=first.challenge_id,
            phone="13800138000",
            code=old_code,
            display_name="测试学生",
            birth_date=date(2000, 1, 1),
            guardian_consent_receipt=None,
            ip_address="203.0.113.7",
            device_id="browser-device-001",
            now=NOW + timedelta(seconds=62),
        )
        self.assertEqual(attempt.status, RegistrationStatus.INVALID_CODE)
        self.assertIsNotNone(second.challenge_id)

    def test_cooldown_and_captcha_escalation(self) -> None:
        accepted = self.request()
        limited = self.request(now=NOW + timedelta(seconds=10))
        self.assertEqual(accepted.status, SendCodeStatus.ACCEPTED)
        self.assertEqual(limited.status, SendCodeStatus.RETRY_LATER)
        self.assertIsNotNone(limited.challenge_id)
        self.assertGreaterEqual(limited.retry_after_seconds, 50)
        self.request(now=NOW + timedelta(seconds=61))
        captcha = self.request(now=NOW + timedelta(seconds=122))
        self.assertEqual(captcha.status, SendCodeStatus.CAPTCHA_REQUIRED)
        passed = self.request(now=NOW + timedelta(seconds=122), captcha="captcha-once")
        self.assertEqual(passed.status, SendCodeStatus.ACCEPTED)

    def test_invalid_attempts_lock_challenge(self) -> None:
        sent = self.request()
        for _ in range(4):
            result = self._register(sent.challenge_id, "000000", date(2000, 1, 1))
            self.assertEqual(result.status, RegistrationStatus.INVALID_CODE)
        fifth = self._register(sent.challenge_id, "000000", date(2000, 1, 1))
        self.assertEqual(fifth.status, RegistrationStatus.LOCKED)
        correct = self._register(sent.challenge_id, self.sender.deliveries[0][1], date(2000, 1, 1))
        self.assertEqual(correct.status, RegistrationStatus.LOCKED)

    def test_minor_requires_guardian_then_registration_is_single_use(self) -> None:
        sent = self.request()
        code = self.sender.deliveries[0][1]
        blocked = self._register(sent.challenge_id, code, date(2012, 1, 1))
        self.assertEqual(blocked.status, RegistrationStatus.GUARDIAN_CONSENT_REQUIRED)
        forged = self._register(
            sent.challenge_id,
            code,
            date(2012, 1, 1),
            guardian_consent_receipt="client-invented-receipt",
        )
        self.assertEqual(forged.status, RegistrationStatus.GUARDIAN_CONSENT_REQUIRED)
        complete = self._register(
            sent.challenge_id,
            code,
            date(2012, 1, 1),
            guardian_consent_receipt="guardian-consent-verified-001",
        )
        self.assertEqual(complete.status, RegistrationStatus.COMPLETE)
        self.assertTrue(complete.session_token)
        reused = self._register(
            sent.challenge_id,
            code,
            date(2012, 1, 1),
            guardian_consent_receipt="guardian-consent-verified-001",
        )
        self.assertEqual(reused.status, RegistrationStatus.INVALID_CODE)

    def test_guardian_receipt_validity_is_not_revealed_before_phone_verification(self) -> None:
        sent = self.request()
        result = self._register(
            sent.challenge_id,
            "000000",
            date(2012, 1, 1),
            guardian_consent_receipt="guardian-consent-verified-001",
        )
        self.assertEqual(result.status, RegistrationStatus.INVALID_CODE)

    def test_provider_failure_does_not_leave_active_challenge(self) -> None:
        self.sender.fail = True
        result = self.request()
        self.assertEqual(result.status, SendCodeStatus.TEMPORARILY_UNAVAILABLE)
        self.assertTrue(all(item.status not in {"pending", "sent"} for item in self.store.challenges.values()))

    def test_expired_code_cannot_create_session(self) -> None:
        sent = self.request()
        result = self.service.register(
            challenge_id=sent.challenge_id,
            phone="13800138000",
            code=self.sender.deliveries[0][1],
            display_name="测试学生",
            birth_date=date(2000, 1, 1),
            guardian_consent_receipt=None,
            ip_address="203.0.113.7",
            device_id="browser-device-001",
            now=NOW + timedelta(minutes=6),
        )
        self.assertEqual(result.status, RegistrationStatus.EXPIRED)
        self.assertFalse(self.store.sessions)

    def test_session_plaintext_is_returned_once_but_only_hash_is_stored(self) -> None:
        sent = self.request()
        result = self._register(sent.challenge_id, self.sender.deliveries[0][1], date(2000, 1, 1))
        self.assertEqual(result.status, RegistrationStatus.COMPLETE)
        self.assertNotIn(result.session_token, self.store.sessions)
        self.assertEqual(len(self.store.sessions), 1)

    def test_challenge_cannot_cross_server_resolved_tenant_scope(self) -> None:
        sent = self.request()
        result = self.service.register(
            challenge_id=sent.challenge_id,
            phone="13800138000",
            code=self.sender.deliveries[0][1],
            display_name="测试学生",
            birth_date=date(2000, 1, 1),
            guardian_consent_receipt=None,
            ip_address="203.0.113.7",
            device_id="browser-device-001",
            tenant_scope="another-tenant",
            now=NOW + timedelta(seconds=30),
        )
        self.assertEqual(result.status, RegistrationStatus.INVALID_CODE)
        self.assertFalse(self.store.sessions)

    def test_concurrent_requests_reserve_only_one_sms(self) -> None:
        def request_once(_: int):
            return self.service.request_code(
                phone="13800138000",
                ip_address="203.0.113.7",
                device_id="browser-device-001",
                now=NOW,
            ).status

        with ThreadPoolExecutor(max_workers=20) as executor:
            statuses = list(executor.map(request_once, range(50)))
        self.assertEqual(statuses.count(SendCodeStatus.ACCEPTED), 1)
        self.assertEqual(len(self.sender.deliveries), 1)

    def test_mysql_migration_has_no_plaintext_otp_column(self) -> None:
        sql = (
            Path(__file__).resolve().parents[1]
            / "services"
            / "web_auth"
            / "migrations"
            / "0001_phone_registration.sql"
        ).read_text(encoding="utf-8")
        lowered = sql.lower()
        self.assertIn("code_hash", lowered)
        self.assertIn("session_hash", lowered)
        self.assertNotIn("plaintext", lowered.replace("plaintext is never stored", ""))
        self.assertNotRegex(lowered, r"\b(code|otp|session_token)\s+(varchar|char)")

    def _register(
        self,
        challenge_id: str,
        code: str,
        birth_date: date,
        guardian_consent_receipt: str | None = None,
    ):
        return self.service.register(
            challenge_id=challenge_id,
            phone="13800138000",
            code=code,
            display_name="测试学生",
            birth_date=birth_date,
            guardian_consent_receipt=guardian_consent_receipt,
            ip_address="203.0.113.7",
            device_id="browser-device-001",
            now=NOW + timedelta(seconds=30),
        )


if __name__ == "__main__":
    unittest.main()
