"""Tests for prepare_release.py. Run with: python3 -m unittest discover -s infra/deployment"""

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

import prepare_release as pr

DIGEST = "sha256:" + "a" * 64
CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
KEY = "-----BEGIN EC PRIVATE KEY-----\nMHc\n-----END EC PRIVATE KEY-----"


def settings(**overrides):
    values = {
        "DEPLOY_ENVIRONMENT": "production",
        "API_DOMAIN": "api.finvestug.com",
        "ADMIN_DOMAIN": "admin.finvestug.com",
        "APP_DOMAIN": "app.finvestug.com",
        "SITE_DOMAINS": "finvestug.com,www.finvestug.com",
        "BACKEND_IMAGE": f"ghcr.io/solnsubuga/finvest-backend@{DIGEST}",
        "CONSOLE_IMAGE": f"ghcr.io/solnsubuga/finvest-console@{DIGEST}",
        "APP_IMAGE": f"ghcr.io/solnsubuga/finvest-app@{DIGEST}",
        "SITE_IMAGE": f"ghcr.io/solnsubuga/finvest-site@{DIGEST}",
        "DATABASE_HOST": "private-finvest-production.db.ondigitalocean.com",
        "DATABASE_PORT": "25060",
        "DATABASE_NAME": "finvest",
        "DATABASE_MIGRATOR_PASSWORD": "migrator/secret@x",
        "DATABASE_APP_PASSWORD": "app-secret",
        "DATABASE_WORKER_PASSWORD": "worker-secret",
        "DATABASE_CA_CERT": CERT,
        "ORIGIN_TLS_CERT": CERT,
        "ORIGIN_TLS_KEY": KEY,
        "FINVEST_EMAIL_BACKEND": "resend",
        "FINVEST_EMAIL_FROM": "hello@finvestug.com",
        "FINVEST_EMAIL_RESEND_API_KEY": "re_test_not_a_real_key",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


class PrepareRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "release"

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_process_gets_only_its_own_role(self):
        pr.prepare(self.out, settings())
        for service, role in pr.ROLES.items():
            env = read_env(self.out / "runtime" / f"{service}.env")
            self.assertIn(f"//finvest_{role}:", env["FINVEST_DATABASE_URL"])
        everything_but_migrate = "".join(
            (self.out / "runtime" / f"{s}.env").read_text() for s in ("api", "worker", "scheduler")
        )
        self.assertNotIn("migrator", everything_but_migrate)

    def test_every_process_is_told_its_environment(self):
        for environment in pr.ENVIRONMENTS:
            pr.prepare(self.out, settings(DEPLOY_ENVIRONMENT=environment))
            for service in pr.ROLES:
                env = read_env(self.out / "runtime" / f"{service}.env")
                self.assertEqual(env["FINVEST_ENVIRONMENT"], environment)

    def worker_email_backend(self, env):
        pr.prepare(self.out, env)
        backend = read_env(self.out / "runtime" / "worker.env").get("FINVEST_EMAIL_BACKEND")
        shutil.rmtree(self.out)
        return backend

    def test_production_sends_through_resend_when_the_backend_is_blank(self):
        for blank in (None, ""):
            env = settings(FINVEST_EMAIL_BACKEND=blank)
            self.assertEqual(self.worker_email_backend(env), "resend")
            self.assertEqual(pr.email_warnings(env), [])

    def test_production_without_email_is_warned_about_but_still_prepared(self):
        for missing in (
            {"FINVEST_EMAIL_BACKEND": "none"},
            {"FINVEST_EMAIL_BACKEND": None, "FINVEST_EMAIL_FROM": None},
            {"FINVEST_EMAIL_BACKEND": None, "FINVEST_EMAIL_RESEND_API_KEY": None},
        ):
            with self.subTest(missing=missing):
                env = settings(**missing)
                self.assertEqual(len(pr.email_warnings(env)), 1)
                # Left off rather than resend, so the worker still starts without a key.
                self.assertIn(self.worker_email_backend(env), (None, "none"))

    def test_production_with_email_has_no_warning(self):
        self.assertEqual(pr.email_warnings(settings()), [])
        self.assertEqual(self.worker_email_backend(settings()), "resend")

    def test_staging_and_rehearsals_may_leave_email_off(self):
        for env in (
            settings(DEPLOY_ENVIRONMENT="staging", FINVEST_EMAIL_BACKEND=None),
            settings(FINVEST_REHEARSAL="true", FINVEST_EMAIL_BACKEND=None),
        ):
            self.assertEqual(pr.email_warnings(env), [])
        self.assertIsNone(self.worker_email_backend(settings(DEPLOY_ENVIRONMENT="staging", FINVEST_EMAIL_BACKEND=None)))

    def test_password_is_percent_encoded(self):
        pr.prepare(self.out, settings())
        env = read_env(self.out / "runtime" / "migrate.env")
        self.assertIn("finvest_migrator:migrator%2Fsecret%40x@", env["FINVEST_DATABASE_URL"])

    def test_database_tls_is_verified_against_the_provider_ca(self):
        pr.prepare(self.out, settings())
        env = read_env(self.out / "runtime" / "api.env")
        self.assertEqual(env["FINVEST_DATABASE_SSL_MODE"], "verify-full")
        self.assertEqual(env["FINVEST_DATABASE_SSL_ROOT_CERT"], pr.CA_PATH)
        self.assertTrue((self.out / "database-ca" / "ca.crt").read_text().startswith("-----BEGIN"))

    def test_ca_is_required_outside_a_rehearsal(self):
        with self.assertRaises(pr.SettingError):
            pr.prepare(self.out, settings(DATABASE_CA_CERT=None))

    def test_rehearsal_is_refused_with_real_images(self):
        with self.assertRaises(pr.SettingError):
            pr.prepare(self.out, settings(FINVEST_REHEARSAL="true", DATABASE_CA_CERT=None))

    def test_rehearsal_with_local_images_turns_database_tls_off(self):
        local = {name: f"localhost:15000/finvest-x@{DIGEST}" for name in ("BACKEND_IMAGE", "CONSOLE_IMAGE", "APP_IMAGE", "SITE_IMAGE")}
        pr.prepare(self.out, settings(FINVEST_REHEARSAL="true", DATABASE_CA_CERT=None, **local))
        self.assertEqual(read_env(self.out / "runtime" / "api.env")["FINVEST_DATABASE_SSL_MODE"], "disable")

    def test_secrets_are_private(self):
        pr.prepare(self.out, settings())
        for path in [*self.out.glob("runtime/*.env"), self.out / "release.env", self.out / "tls" / "origin.key"]:
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path)

    def test_images_must_be_pinned_by_digest(self):
        with self.assertRaises(pr.SettingError):
            pr.prepare(self.out, settings(BACKEND_IMAGE="ghcr.io/solnsubuga/finvest-backend:latest"))

    def test_multiline_values_are_refused(self):
        with self.assertRaises(pr.SettingError):
            pr.prepare(self.out, settings(DATABASE_APP_PASSWORD="a\nFINVEST_API_DOCS=true"))

    def test_nothing_is_written_when_a_setting_is_missing(self):
        with self.assertRaises(pr.SettingError):
            pr.prepare(self.out, settings(DATABASE_WORKER_PASSWORD=None))
        self.assertFalse(self.out.exists())

    def test_sign_in_settings_follow_the_domains(self):
        pr.prepare(self.out, settings())
        api = read_env(self.out / "runtime" / "api.env")
        self.assertEqual(api["FINVEST_CLIENT_ALLOWED_ORIGINS"], '["https://app.finvestug.com"]')
        self.assertEqual(api["FINVEST_STAFF_ALLOWED_ORIGINS"], '["https://admin.finvestug.com"]')
        self.assertEqual(api["FINVEST_CLIENT_PASSKEY_SITE"], "finvestug.com")
        self.assertEqual(api["FINVEST_STAFF_PASSKEY_SITE"], "admin.finvestug.com")
        self.assertEqual(api["FINVEST_TRUSTED_PROXIES"], f'["{pr.EDGE_SUBNET}"]')

    def test_phone_app_passkey_origins_are_off_until_the_apps_exist(self):
        pr.prepare(self.out, settings())
        api = read_env(self.out / "runtime" / "api.env")
        self.assertEqual(api["FINVEST_CLIENT_PASSKEY_APP_ORIGINS"], "[]")

    def test_phone_app_passkey_origins_follow_the_app_identities(self):
        cert = ":".join(["AB"] * 32)
        pr.prepare(self.out, settings(APPLE_TEAM_ID="ABCDE12345", ANDROID_CERT_SHA256=f"{cert}, {cert}"))
        api = read_env(self.out / "runtime" / "api.env")
        digest = "q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s"
        self.assertEqual(
            json.loads(api["FINVEST_CLIENT_PASSKEY_APP_ORIGINS"]),
            ["https://finvestug.com", f"android:apk-key-hash:{digest}", f"android:apk-key-hash:{digest}"],
        )
        for service in ("worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_CLIENT_PASSKEY_APP_ORIGINS", read_env(self.out / "runtime" / f"{service}.env"))

    def test_phone_app_identities_are_checked(self):
        for bad in ({"APPLE_TEAM_ID": "abc"}, {"ANDROID_CERT_SHA256": "AB:CD"}):
            with self.assertRaises(pr.SettingError):
                pr.prepare(self.out, settings(**bad))

    def test_staging_keeps_its_own_passkey_site(self):
        pr.prepare(
            self.out,
            settings(
                DEPLOY_ENVIRONMENT="staging",
                API_DOMAIN="api-staging.finvestug.com",
                ADMIN_DOMAIN="admin-staging.finvestug.com",
                APP_DOMAIN="app-staging.finvestug.com",
                SITE_DOMAINS="staging.finvestug.com",
            ),
        )
        api = read_env(self.out / "runtime" / "api.env")
        self.assertEqual(api["FINVEST_CLIENT_PASSKEY_SITE"], "app-staging.finvestug.com")
        self.assertNotIn("www", (self.out / "edge" / "site.caddy").read_text())

    def test_authenticator_key_reaches_the_api_only(self):
        pr.prepare(self.out, settings(FINVEST_AUTHENTICATOR_KEY="k" * 43))
        self.assertIn("FINVEST_AUTHENTICATOR_KEY", read_env(self.out / "runtime" / "api.env"))
        for service in ("worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_AUTHENTICATOR_KEY", read_env(self.out / "runtime" / f"{service}.env"))

    def test_client_signup_is_closed_unless_opened(self):
        for value in (None, "", "false", "TRUE", "yes"):
            extra = {} if value is None else {"FINVEST_CLIENT_SIGNUP_OPEN": value}
            pr.prepare(self.out, settings(**extra))
            self.assertEqual(read_env(self.out / "runtime" / "api.env")["FINVEST_CLIENT_SIGNUP_OPEN"], "false")

    def test_client_signup_opens_on_the_api_only(self):
        pr.prepare(self.out, settings(FINVEST_CLIENT_SIGNUP_OPEN="true"))
        self.assertEqual(read_env(self.out / "runtime" / "api.env")["FINVEST_CLIENT_SIGNUP_OPEN"], "true")
        for service in ("worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_CLIENT_SIGNUP_OPEN", read_env(self.out / "runtime" / f"{service}.env"))

    def test_bank_transfer_accounts_are_no_longer_passed(self):
        # Staff set the account on console Settings (docs/payments.md), so a leftover variable is ignored.
        pr.prepare(self.out, settings(FINVEST_BANK_TRANSFER_ACCOUNTS='{"UGX": {"account_number": "0123"}}'))
        for service in ("api", "worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_BANK_TRANSFER_ACCOUNTS", read_env(self.out / "runtime" / f"{service}.env"))

    def test_resend_hook_secret_reaches_the_api_only(self):
        pr.prepare(self.out, settings(RESEND_HOOK_SECRET="whsec_" + "h" * 32))
        self.assertIn("RESEND_HOOK_SECRET", read_env(self.out / "runtime" / "api.env"))
        for service in ("worker", "scheduler", "migrate"):
            self.assertNotIn("RESEND_HOOK_SECRET", read_env(self.out / "runtime" / f"{service}.env"))

    def test_fidiba_environment_tag_is_no_longer_passed(self):
        # Finvest uses its own Resend account, so there is no router tag to send.
        pr.prepare(self.out, settings(FINVEST_EMAIL_FIDIBA_ENVIRONMENT="app_finvest_prod"))
        for service in ("api", "worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_EMAIL_FIDIBA_ENVIRONMENT", read_env(self.out / "runtime" / f"{service}.env"))

    def test_email_hash_key_reaches_the_api_and_worker_only(self):
        pr.prepare(self.out, settings(FINVEST_EMAIL_HASH_KEY="e" * 43))
        for service in ("api", "worker"):
            self.assertEqual(read_env(self.out / "runtime" / f"{service}.env")["FINVEST_EMAIL_HASH_KEY"], "e" * 43)
        for service in ("scheduler", "migrate"):
            self.assertNotIn("FINVEST_EMAIL_HASH_KEY", read_env(self.out / "runtime" / f"{service}.env"))

    def test_sms_settings_reach_the_worker_only(self):
        sms = {
            "FINVEST_SMS_BACKEND": "africastalking",
            "FINVEST_SMS_FALLBACK": "iotec",
            "FINVEST_SMS_AFRICASTALKING_USERNAME": "finvest",
            "FINVEST_SMS_AFRICASTALKING_API_KEY": "a" * 64,
            "FINVEST_SMS_AFRICASTALKING_SENDER_ID": "FINVEST",
            "FINVEST_SMS_AFRICASTALKING_BASE_URL": "https://api.africastalking.com",
            "FINVEST_SMS_IOTEC_CLIENT_ID": "client",
            "FINVEST_SMS_IOTEC_API_KEY": "k" * 32,
            "FINVEST_SMS_IOTEC_BASE_URL": "https://messaging-api.iotec.io",
        }
        pr.prepare(self.out, settings(**sms))
        worker = read_env(self.out / "runtime" / "worker.env")
        for name, value in sms.items():
            self.assertEqual(worker[name], value)
        for service in ("api", "scheduler", "migrate"):
            found = read_env(self.out / "runtime" / f"{service}.env")
            self.assertFalse([name for name in sms if name in found])

    def test_the_sms_callback_token_reaches_the_api_only(self):
        token = "t" * 43
        pr.prepare(self.out, settings(FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN=token))
        api = read_env(self.out / "runtime" / "api.env")
        self.assertEqual(api["FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN"], token)
        for service in ("worker", "scheduler", "migrate"):
            found = read_env(self.out / "runtime" / f"{service}.env")
            self.assertNotIn("FINVEST_SMS_AFRICASTALKING_CALLBACK_TOKEN", found)

    def test_iotec_settings_reach_only_the_processes_that_read_them(self):
        everywhere = {"FINVEST_PAYMENTS_PROVIDER": "iotec", "FINVEST_IOTEC_CURRENCY_CODE": "UGX"}
        api_and_worker = {
            "FINVEST_IOTEC_CLIENT_ID": "pay-client",
            "FINVEST_IOTEC_CLIENT_SECRET": "s" * 32,
            "FINVEST_IOTEC_WALLET_ID": "wallet",
            "FINVEST_IOTEC_TOKEN_URL": "https://id.iotec.io/connect/token",
            "FINVEST_IOTEC_PAY_URL": "https://pay.iotec.io",
            "FINVEST_IOTEC_TIMEOUT_SECONDS": "30",
            "FINVEST_IDENTITY_PROVIDER": "iotec",
            "FINVEST_IOTEC_VERIFY_CLIENT_ID": "verify-client",
            "FINVEST_IOTEC_VERIFY_API_KEY": "v" * 32,
            "FINVEST_IOTEC_VERIFY_BASE_URL": "https://verify-api.iotec.io",
            "FINVEST_PAYOUT_NAME_PROVIDER": "iotec",
            "FINVEST_SCREENING_PROVIDER": "sigma360",
            "FINVEST_SIGMA360_API_KEY": "k" * 32,
            "FINVEST_SIGMA360_FILTER_SET_ID": "42",
            "FINVEST_SIGMA360_BASE_URL": "https://api.sigma360.com/external/v2",
            "FINVEST_CUSTODIAN_READER": "claude",
            "FINVEST_ANTHROPIC_API_KEY": "a" * 40,
            "FINVEST_OPENAI_API_KEY": "o" * 40,
            "FINVEST_GEMINI_API_KEY": "g" * 40,
            "FINVEST_CUSTODIAN_READER_MODEL": "reader-model",
        }
        api_only = {"FINVEST_IOTEC_CALLBACK_TOKEN": "c" * 43}
        pr.prepare(self.out, settings(**everywhere, **api_and_worker, **api_only))
        files = {s: read_env(self.out / "runtime" / f"{s}.env") for s in ("api", "worker", "scheduler", "migrate")}
        for name, value in everywhere.items():
            for service in ("api", "worker", "scheduler"):
                self.assertEqual(files[service][name], value)
        for name, value in api_and_worker.items():
            self.assertEqual(files["api"][name], value)
            self.assertEqual(files["worker"][name], value)
            self.assertNotIn(name, files["scheduler"])
        self.assertEqual(files["api"]["FINVEST_IOTEC_CALLBACK_TOKEN"], "c" * 43)
        for service in ("worker", "scheduler"):
            self.assertNotIn("FINVEST_IOTEC_CALLBACK_TOKEN", files[service])
        self.assertFalse(
            [name for name in files["migrate"] if any(k in name for k in ("IOTEC", "PROVIDER", "SIGMA360", "CUSTODIAN", "ANTHROPIC", "OPENAI", "GEMINI"))]
        )

    def test_cloudflare_staff_list_settings_reach_the_worker_only(self):
        values = {
            "FINVEST_CLOUDFLARE_ACCOUNT_ID": "account",
            "FINVEST_CLOUDFLARE_STAFF_LIST_ID": "list",
            "FINVEST_CLOUDFLARE_ACCESS_TOKEN": "t" * 40,
        }
        pr.prepare(self.out, settings(**values))
        worker = read_env(self.out / "runtime" / "worker.env")
        for name, value in values.items():
            self.assertEqual(worker[name], value)
        for service in ("api", "scheduler", "migrate"):
            found = read_env(self.out / "runtime" / f"{service}.env")
            self.assertFalse([name for name in found if name.startswith("FINVEST_CLOUDFLARE_")])

    def test_cloudflare_staff_list_settings_are_left_out_when_unset(self):
        pr.prepare(self.out, settings())
        self.assertFalse(
            [name for name in read_env(self.out / "runtime" / "worker.env") if name.startswith("FINVEST_CLOUDFLARE_")]
        )

    def test_iotec_settings_are_left_out_when_unset(self):
        pr.prepare(self.out, settings())
        for service in ("api", "worker", "scheduler"):
            found = read_env(self.out / "runtime" / f"{service}.env")
            self.assertFalse([name for name in found if any(k in name for k in ("IOTEC", "PROVIDER", "SIGMA360"))])

    def test_identity_data_key_reaches_the_api_and_worker_only(self):
        pr.prepare(self.out, settings(FINVEST_IDENTITY_DATA_KEY="d" * 43))
        for service in ("api", "worker"):
            self.assertIn("FINVEST_IDENTITY_DATA_KEY", read_env(self.out / "runtime" / f"{service}.env"))
        for service in ("scheduler", "migrate"):
            self.assertNotIn("FINVEST_IDENTITY_DATA_KEY", read_env(self.out / "runtime" / f"{service}.env"))

    def test_staging_serves_the_website(self):
        pr.prepare(self.out, settings(DEPLOY_ENVIRONMENT="staging"))
        site = (self.out / "edge" / "site.caddy").read_text()
        self.assertIn("import site_upstream", site)
        self.assertIn("import unlisted", site)
        self.assertEqual(read_env(self.out / "release.env")["FINVEST_SITE_DOMAIN"], "finvestug.com")

    def test_production_holds_the_website_back_until_published(self):
        pr.prepare(self.out, settings())
        site = (self.out / "edge" / "site.caddy").read_text()
        # Only the phone apps' association files reach the site image before it is published.
        self.assertEqual(site.count("import site_upstream"), 1)
        self.assertIn(
            "@app_links path /.well-known/apple-app-site-association /.well-known/assetlinks.json", site
        )
        self.assertIn(" 503", site)
        self.assertNotIn("FINVEST_SITE_DOMAIN", read_env(self.out / "release.env"))

    def test_production_serves_the_website_once_published(self):
        pr.prepare(self.out, settings(SITE_PUBLISHED="true"))
        site = (self.out / "edge" / "site.caddy").read_text()
        self.assertIn("import site_upstream", site)
        self.assertNotIn("unlisted", site)
        self.assertEqual(read_env(self.out / "release.env")["FINVEST_SITE_DOMAIN"], "finvestug.com")

    def test_file_storage_reaches_the_api_and_worker_when_complete(self):
        bucket = {
            "FINVEST_OBJECT_STORAGE_BUCKET": "finvest-production-documents",
            "FINVEST_OBJECT_STORAGE_ENDPOINT": "https://lon1.digitaloceanspaces.com",
            "FINVEST_OBJECT_STORAGE_REGION": "lon1",
            "FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID": "key-id",
            "FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY": "key-secret",
        }
        pr.prepare(self.out, settings(**bucket))
        for service in ("api", "worker"):
            self.assertEqual(read_env(self.out / "runtime" / f"{service}.env")["FINVEST_OBJECT_STORAGE_BACKEND"], "s3")
        for service in ("scheduler", "migrate"):
            self.assertNotIn("OBJECT_STORAGE", (self.out / "runtime" / f"{service}.env").read_text())
        self.assertEqual(
            read_env(self.out / "release.env")["FINVEST_STORAGE_ORIGIN"],
            "https://finvest-production-documents.lon1.digitaloceanspaces.com",
        )

    def test_partial_file_storage_stays_off(self):
        pr.prepare(self.out, settings(FINVEST_OBJECT_STORAGE_BUCKET="finvest-production-documents"))
        self.assertNotIn("FINVEST_OBJECT_STORAGE_BACKEND", read_env(self.out / "runtime" / "api.env"))
        self.assertEqual(read_env(self.out / "release.env")["FINVEST_STORAGE_ORIGIN"], "")

    def test_www_redirects_to_the_apex(self):
        pr.prepare(self.out, settings())
        site = (self.out / "edge" / "site.caddy").read_text()
        self.assertIn("www.finvestug.com {", site)
        self.assertIn("redir https://finvestug.com{uri} 308", site)


if __name__ == "__main__":
    unittest.main()
