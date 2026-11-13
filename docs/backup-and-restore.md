# Backups and the restore drill

A backup counts only once it has been restored and checked. This page says what is backed up, how a restore is rehearsed, and what to do in a real one. The infrastructure is in [infra/README.md](../infra/README.md).

## What is backed up

| What | How | Kept for | Restored by |
| --- | --- | --- | --- |
| PostgreSQL (every client, investment, ledger entry and audit event) | DigitalOcean managed database: daily backups and point in time recovery from the write-ahead log | 7 days (DigitalOcean's standard retention; confirm on the cluster's Backups tab) | A fork of the cluster to a chosen minute |
| Identity documents and other files | Spaces bucket with versioning on | Every version, until deleted by a lifecycle rule | Restoring an earlier object version |
| The host | Weekly droplet backups (production) | 4 weeks | Not needed: the host is rebuilt by Provision and a deploy |
| Configuration and secrets | Terraform state and the GitHub environments | In the state bucket | A Provision run |

The database is the only thing whose loss loses money records, so it is the one the drill rehearses.

**Targets (proposed, see Open in [decisions.md](decisions.md)).** At most 5 minutes of data lost (point in time recovery) and the service back within 4 hours.

## The drill

Staging is switched off to save cost, so the drill always forks production. Run it before the parallel run starts, when production holds no client data yet, then once a quarter, and after any change to the database's size, version or region. Once clients are on 2.0 the fork holds a full copy of their data: only the person running the drill can reach it, it never writes to the real cluster, and it is deleted the same day.

1. **Pick a restore point** a few minutes in the past, and write it down in UTC. Straight after, run the checks in step 4 against the live cluster and keep the output: it is read only and gives the figures to compare with.
2. **Fork the cluster.** In DigitalOcean, open the database cluster, choose Actions, then Restore from backup, choose the point in time, and name the fork `finvest-<env>-drill-<yyyymmdd>`. Use the smallest size. Terraform does not manage forks, so making it by hand is expected. Note the time it took.
3. **Let in only the drill machine.** Add the IP address of the machine running the checks to the fork's trusted sources. Nothing else.
4. **Run the checks** as the fork's `doadmin` user:

   ```sh
   psql "postgresql://doadmin@<fork-host>:25060/finvest?sslmode=verify-full&sslrootcert=<ca.crt>" \
     -v ON_ERROR_STOP=1 -f backend/db/restore-checks.sql
   ```

   psql asks for the password, so it never appears in the command, the shell history or the process list. `backend/db/restore-checks.sql` is read only. It fails if any journal entry does not sum to zero, has fewer than two lines, or if any stored balance differs from the sum of its lines. Otherwise it prints the migration head, the last posting and row counts for the main tables. It prints no personal data and no amounts.
5. **Compare** with the live output from step 1. The migration head must match. The last posting must not be later than the restore point. Every row count must be no higher than the live count, and close to it: a large gap means the fork is older than the point chosen.
6. **Delete the fork** in DigitalOcean the same day. It bills by the hour and holds a full copy of client data.
7. **Record the drill** in the table below: date, environment, restore point, how long the fork took, whether the checks passed, and who ran it.

| Date | Environment | Restore point (UTC) | Fork took | Checks | By |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

## A real restore

Use this only when the live database is lost or its data is wrong, and only with Solo's go-ahead, because it changes production.

1. Put the API in maintenance: stop the `api`, `worker` and `scheduler` containers so nothing writes or sends to a provider.
2. Fork the cluster to the last good minute, as in the drill, at the production size.
3. Run `restore-checks.sql` against the fork. Do not go on if it fails.
4. Point the environment at the fork. This is a change to Terraform (the infrastructure thread) so the database host, CA certificate and role passwords in the GitHub environment follow the new cluster.
5. Deploy, which runs migrations and starts the processes.
6. Reconcile everything that happened between the restore point and the outage with each provider before anything new is sent: a payment or payout the provider holds but the restored database does not know about is an uncertain outcome, and the rules in [payments.md](payments.md) apply. Nothing is re-sent until the provider has said what happened.
7. Tell clients and the regulator what was affected, as the incident process requires.
