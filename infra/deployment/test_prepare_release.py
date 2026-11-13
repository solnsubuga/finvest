"""Tests for prepare_release.py. Run with: python3 -m unittest discover -s infra/deployment"""

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
        local = {name: f"localhost:15000/finvest-x@{DIGEST}" for name in ("BACKEND_IMAGE", "CONSOLE_IMAGE")}
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

    def test_www_redirects_to_the_apex(self):
        pr.prepare(self.out, settings())
        site = (self.out / "edge" / "site.caddy").read_text()
        self.assertIn("www.finvestug.com {", site)
        self.assertIn("redir https://finvestug.com{uri} 308", site)


if __name__ == "__main__":
    unittest.main()
