"""Tests for prepare_release.py. Run with: python3 -m unittest discover -s infra/deployment"""

import json
import os
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
        local = {name: f"localhost:15000/finvest-x@{DIGEST}" for name in ("BACKEND_IMAGE", "CONSOLE_IMAGE", "SITE_IMAGE")}
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

    def test_bank_transfer_accounts_reach_the_api_only_on_one_line(self):
        accounts = '{\n  "UGX": {"bank_name": "Bank", "account_name": "Finvest Trust", "account_number": "0123", "branch": null}\n}'
        pr.prepare(self.out, settings(FINVEST_BANK_TRANSFER_ACCOUNTS=accounts))
        value = read_env(self.out / "runtime" / "api.env")["FINVEST_BANK_TRANSFER_ACCOUNTS"]
        self.assertEqual(json.loads(value)["UGX"]["account_number"], "0123")
        self.assertNotIn("\n", value)
        for service in ("worker", "scheduler", "migrate"):
            self.assertNotIn("FINVEST_BANK_TRANSFER_ACCOUNTS", read_env(self.out / "runtime" / f"{service}.env"))

    def test_bank_transfer_accounts_are_left_out_when_unset(self):
        pr.prepare(self.out, settings())
        self.assertNotIn("FINVEST_BANK_TRANSFER_ACCOUNTS", read_env(self.out / "runtime" / "api.env"))

    def test_malformed_bank_transfer_accounts_are_refused(self):
        for value in ("not json", "[]", "{}", '{"UGX": "x"}'):
            with self.assertRaises(pr.SettingError):
                pr.prepare(self.out, settings(FINVEST_BANK_TRANSFER_ACCOUNTS=value))

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
            "FINVEST_SMS_BACKEND": "iotec",
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
            "FINVEST_SCREENING_PROVIDER": "none",
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
        self.assertFalse([name for name in files["migrate"] if "IOTEC" in name or "PROVIDER" in name])

    def test_iotec_settings_are_left_out_when_unset(self):
        pr.prepare(self.out, settings())
        for service in ("api", "worker", "scheduler"):
            found = read_env(self.out / "runtime" / f"{service}.env")
            self.assertFalse([name for name in found if "IOTEC" in name or "PROVIDER" in name])

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
        self.assertNotIn("site_upstream", site)
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
