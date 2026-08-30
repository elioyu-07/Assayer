import getpass
import unittest
from unittest.mock import patch

from assayer_host import (CredentialVault, LocalCredentialIntake,
                          LoginCoordinator, LoginResult, LoginSecret)


class ReadingLoginAdapter:
    def __init__(self, result=None):
        self.result = result or LoginResult(
            "succeeded", current_page_state_id="page-login-001",
            capabilities=("runtime", "dom"),
        )
        self.secret_ref = None
        self.observed = None

    def authenticate(self, url, secret):
        self.secret_ref = secret
        self.observed = (url, secret.reveal_username(), secret.reveal_password())
        return self.result


class UnclearableSecret(LoginSecret):
    def clear(self):
        return None


class ExplodingAuthAdapter:
    def authenticate(self, url, secret):
        self.secret_ref = secret
        raise RuntimeError("password leaked in exception text")


class AnonymousAdapter:
    def __init__(self, result=None):
        self.result = result or LoginResult(
            "succeeded", current_page_state_id="page-anonymous-001",
            capabilities=("runtime", "dom"),
        )
        self.calls = 0

    def authenticate_anonymous(self, url):
        self.calls += 1
        return self.result


class AuthenticationBoundaryTest(unittest.TestCase):
    def test_secret_repr_never_contains_values_and_clear_is_terminal(self):
        secret = LoginSecret("alice@example.com", "correct-horse")
        self.assertEqual(repr(secret), "LoginSecret([REDACTED])")
        self.assertEqual(str(secret), "LoginSecret([REDACTED])")
        self.assertEqual(secret.reveal_username(), "alice@example.com")
        secret.clear(); secret.clear()
        self.assertTrue(secret.is_cleared)
        with self.assertRaises(RuntimeError):
            secret.reveal_password()

    def test_vault_requires_typed_secret_and_consumes_once(self):
        vault = CredentialVault()
        with self.assertRaises(TypeError):
            vault.put("credential-1", "plain-text")
        secret = LoginSecret("alice", "password")
        vault.put("credential-1", secret)
        with self.assertRaises(ValueError):
            vault.put("credential-1", LoginSecret("bob", "password"))
        self.assertIs(vault.consume("credential-1"), secret)
        self.assertIsNone(vault.consume("credential-1"))
        bad_handle_secret = LoginSecret("alice", "password")
        long_ttl_secret = LoginSecret("alice", "password")
        with self.assertRaises(ValueError):
            vault.put("bad handle", bad_handle_secret)
        with self.assertRaises(ValueError):
            vault.put("credential-long", long_ttl_secret, ttl_seconds=301)
        bad_handle_secret.clear(); long_ttl_secret.clear()

    def test_expired_discarded_and_shutdown_secrets_are_cleared(self):
        vault = CredentialVault()
        expired = LoginSecret("expired", "password")
        with patch("assayer_host.auth.monotonic", side_effect=[0.0, 2.0]):
            vault.put("expired", expired, ttl_seconds=1)
            self.assertIsNone(vault.consume("expired"))
        self.assertTrue(expired.is_cleared)
        discarded = LoginSecret("discarded", "password")
        remaining = LoginSecret("remaining", "password")
        vault.put("discarded", discarded); vault.put("remaining", remaining)
        vault.discard("discarded")
        self.assertTrue(discarded.is_cleared)
        vault.clear_all()
        self.assertTrue(remaining.is_cleared)
        self.assertEqual(len(vault), 0)

    def test_local_intake_uses_separate_no_echo_password_reader(self):
        vault = CredentialVault()
        prompts = []

        def username_reader(prompt):
            prompts.append(("username", prompt)); return "local-user"

        def password_reader(prompt):
            prompts.append(("password", prompt)); return "local-password"

        intake = LocalCredentialIntake(
            vault, username_reader=username_reader, password_reader=password_reader,
            handle_factory=lambda: "credential-local",
        )
        self.assertEqual(intake.capture_username_password(), "credential-local")
        secret = vault.consume("credential-local")
        self.assertEqual(secret.reveal_username(), "local-user")
        self.assertEqual(secret.reveal_password(), "local-password")
        secret.clear()
        self.assertEqual([kind for kind, _ in prompts], ["username", "password"])
        self.assertIs(LocalCredentialIntake.__init__.__kwdefaults__["password_reader"], getpass.getpass)

    def test_successful_login_clears_secret_after_adapter_returns(self):
        adapter = ReadingLoginAdapter()
        secret = LoginSecret("alice", "password")
        outcome = LoginCoordinator().authenticate("https://test.example.com", secret, adapter)
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.phases, ("credential_consumed", "authenticating", "succeeded", "credential_cleared"))
        self.assertEqual(adapter.observed, ("https://test.example.com", "alice", "password"))
        self.assertTrue(outcome.secret_cleared)
        self.assertTrue(adapter.secret_ref.is_cleared)

    def test_anonymous_access_has_no_credential_phase_or_secret(self):
        adapter = AnonymousAdapter()
        outcome = LoginCoordinator().authenticate_anonymous("https://test.example.com", adapter)
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.phases, ("anonymous_access", "authenticating", "succeeded"))
        self.assertTrue(outcome.secret_cleared)
        self.assertEqual(adapter.calls, 1)

    def test_anonymous_failure_redacts_credential_assignments(self):
        adapter = AnonymousAdapter(LoginResult("failed", reason="token=abc page unavailable"))
        outcome = LoginCoordinator().authenticate_anonymous("https://test.example.com", adapter)
        self.assertEqual(outcome.error_code, "LOGIN_FAILED")
        self.assertNotIn("token=abc", outcome.reason)

    def test_failed_login_redacts_secret_values_and_clears(self):
        adapter = ReadingLoginAdapter(LoginResult("failed", reason="alice password=password token=abc"))
        secret = LoginSecret("alice", "password")
        outcome = LoginCoordinator().authenticate("https://test.example.com", secret, adapter)
        self.assertEqual(outcome.error_code, "LOGIN_FAILED")
        self.assertNotIn("alice", outcome.reason)
        self.assertNotIn("token=abc", outcome.reason)
        self.assertTrue(secret.is_cleared)

    def test_invalid_adapter_result_is_internal_failure_without_page_facts(self):
        invalid = ReadingLoginAdapter(LoginResult(
            "failed", reason="no", current_page_state_id="page-forged",
            capabilities=("runtime",),
        ))
        outcome = LoginCoordinator().authenticate(
            "https://test.example.com", LoginSecret("alice", "password"), invalid,
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "INTERNAL_FAILURE")
        self.assertIsNone(outcome.result)

    def test_adapter_exception_is_generic_and_secret_is_cleared(self):
        secret = LoginSecret("alice", "password")
        adapter = ExplodingAuthAdapter()
        outcome = LoginCoordinator().authenticate("https://test.example.com", secret, adapter)
        self.assertEqual(outcome.error_code, "INTERNAL_FAILURE")
        self.assertEqual(outcome.reason, "登录适配器执行失败")
        self.assertTrue(secret.is_cleared)
        self.assertTrue(adapter.secret_ref.is_cleared)

    def test_clear_failure_overrides_adapter_success(self):
        secret = UnclearableSecret("alice", "password")
        outcome = LoginCoordinator().authenticate(
            "https://test.example.com", secret, ReadingLoginAdapter(),
        )
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_code, "CREDENTIAL_CHANNEL_FAILED")
        self.assertFalse(outcome.secret_cleared)
        LoginSecret.clear(secret)


if __name__ == "__main__":
    unittest.main()
