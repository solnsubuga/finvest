-- Idempotent role bootstrap, run with psql by a database administrator before migrations.
-- It creates the three roles and hands schema public to the migrator. Grants on tables belong to
-- migrations. Passwords come from the environment and are never written in this file.
--
--   FINVEST_MIGRATOR_PASSWORD=... FINVEST_APP_PASSWORD=... FINVEST_WORKER_PASSWORD=... \
--     psql "$ADMIN_DATABASE_URL" -f db/init-roles.sql
\set ON_ERROR_STOP on
\getenv migrator_password FINVEST_MIGRATOR_PASSWORD
\getenv app_password FINVEST_APP_PASSWORD
\getenv worker_password FINVEST_WORKER_PASSWORD

SELECT format('CREATE ROLE finvest_migrator LOGIN PASSWORD %L', :'migrator_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finvest_migrator') \gexec
SELECT format('CREATE ROLE finvest_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finvest_app') \gexec
SELECT format('CREATE ROLE finvest_worker LOGIN PASSWORD %L', :'worker_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finvest_worker') \gexec

-- Reassert attributes on every run. Only a superuser may change SUPERUSER, REPLICATION and
-- BYPASSRLS; a managed provider's administrator (DigitalOcean's doadmin) is not one, so the
-- check below covers that case. Roles it created never hold these attributes.
SELECT format('ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', role)
FROM unnest(ARRAY['finvest_migrator', 'finvest_app', 'finvest_worker']) AS role
WHERE (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) \gexec

DO $$
BEGIN
  IF EXISTS (
    SELECT FROM pg_roles
    WHERE rolname IN ('finvest_migrator', 'finvest_app', 'finvest_worker')
      AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'A Finvest role has a superuser, create, replication or bypass-RLS attribute; remove it as a superuser';
  END IF;
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- From PostgreSQL 16 a non-superuser administrator may hand a schema to a role only as a member
-- of that role. Superusers skip this.
SELECT format('GRANT finvest_migrator TO %I', current_user)
WHERE NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
  AND NOT pg_has_role(current_user, 'finvest_migrator', 'SET') \gexec
ALTER SCHEMA public OWNER TO finvest_migrator;
GRANT USAGE ON SCHEMA public TO finvest_app, finvest_worker;
