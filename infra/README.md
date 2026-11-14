# infra

Infrastructure as code for Finvest, modelled on Fidiba's provisioning. Terraform creates every DigitalOcean resource, from the host to the Spaces buckets, inside one DigitalOcean project called `finvest`, and puts Cloudflare in front. Owned by the infrastructure thread. Feature PRs do not change it.

| Path | Contents |
| --- | --- |
| `terraform/shared/` | The `finvest` project, the Cloudflare zone settings and the zone's one rate limiting rule, which every environment shares. Provision it first. |
| `terraform/` | One environment: host, network, database, buckets, DNS, back office access, alerts, and the settings it writes into GitHub. |
| `terraform/environments/*.tfvars` | What differs between `staging` and `production`. Each has its own state, so planning one never touches the other. |
| `terraform/tests/` | `terraform test` with mock providers: the rules below that must not drift. |
| `deployment/` | The deployed stack and its scripts, ported from Fidiba: `compose.yaml`, `Caddyfile`, `deploy.sh`, `prepare_release.py`, `ship-release.sh`, the console and website images, `prepare-host.sh`, `create-roles.sh`, and `rehearse.sh`. |

## What an environment gets

| Resource | Staging | Production | Why |
| --- | --- | --- | --- |
| VPC | `10.20.0.0/20` | `10.10.0.0/20` | Keeps database traffic private |
| Droplet | Ubuntu 24.04, `s-1vcpu-2gb` | `s-2vcpu-4gb`, weekly backups | Runs the Compose stack: Caddy, API, worker, scheduler, frontends. 2 GB swap. |
| Reserved IP | Assigned to the droplet | Same | Stable address for DNS and `DEPLOY_HOST`, kept if the droplet is rebuilt |
| Cloud firewall | Same rules | Same rules | 80 and 443 only from Cloudflare's ranges, so nobody can skip the edge. SSH only from `SSH_SOURCE_ADDRESSES`, plus the Provision run while it prepares the host. |
| Managed PostgreSQL 17 | `db-s-1vcpu-1gb` | `db-s-1vcpu-2gb` | One node, database `finvest`, daily backups and point in time recovery, rehearsed by the drill in [docs/backup-and-restore.md](../docs/backup-and-restore.md). Only the droplet may connect. |
| Database roles | Same | Same | `finvest_migrator`, `finvest_app`, `finvest_worker`, created from the host with `backend/db/init-roles.sql` and generated passwords |
| Spaces bucket and key | `finvest-staging-documents` | `finvest-production-documents` | Private, versioned, read by the console through short-lived links (no browser uploads), and a key limited to the bucket |
| Cloudflare | Proxied records, Origin CA certificate | Same, plus Access on the back office, and on the client app until switch day | Full (strict) TLS from the edge to the host |
| GitHub settings | Written into the `dev` environment | Written into `prod` | See below |
| Alerts | When `ALERT_EMAILS` is set | Same | Uptime on `https://<api>/health/ready` and `/health/operations`, host CPU and disk, database CPU, memory and disk. What each alert means: [docs/observability.md](../docs/observability.md) |
| Tag | `finvest-staging` | `finvest-production` | Tells the environments apart inside the one project |

The droplet, database cluster, bucket and project have `prevent_destroy`: Terraform refuses any plan that would delete them. Changing the droplet's SSH key or image later does not rebuild it, because cloud-init runs only on first boot.

| Address | Production | Staging |
| --- | --- | --- |
| Public website | finvestug.com, www.finvestug.com | staging.finvestug.com |
| Client app | app.finvestug.com | app-staging.finvestug.com |
| Back office | admin.finvestug.com, behind Cloudflare Access | admin-staging.finvestug.com |
| API | api.finvestug.com | api-staging.finvestug.com |

Staging names sit one level below the zone so Cloudflare's free certificate covers them. `api.finvestug.com` is still an open question for the owner in `docs/decisions.md`.

### Settings Terraform writes into GitHub

Terraform owns these names in the `dev` (staging) and `prod` (production) GitHub environments. Change them in `infra/terraform`, not in GitHub, or the next Provision run puts them back. The deploy workflow reads them.

| Kind | Names |
| --- | --- |
| Variables | `SITE_DOMAINS`, `APP_DOMAIN`, `ADMIN_DOMAIN`, `API_DOMAIN`, `DEPLOY_HOST`, `DEPLOY_USER`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `FINVEST_OBJECT_STORAGE_BUCKET`, `FINVEST_OBJECT_STORAGE_ENDPOINT`, `FINVEST_OBJECT_STORAGE_REGION` |
| Secrets | `DATABASE_CA_CERT`, `DATABASE_MIGRATOR_PASSWORD`, `DATABASE_APP_PASSWORD`, `DATABASE_WORKER_PASSWORD`, `FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID`, `FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY`, `ORIGIN_TLS_CERT`, `ORIGIN_TLS_KEY` |

GitHub refuses to create a variable that already exists, so do not create these by hand.

### Secrets in Terraform state

Like Fidiba, Terraform generates and holds these credentials, so they are in its state file: the database admin connection, the three role passwords, the documents bucket key and the origin certificate key. This is the one recorded exception to "no secrets in Terraform state" (see `docs/decisions.md`). It is kept safe like this:

- The state bucket is private, and only the Provision workflow's Spaces keys can read it.
- The Provision workflow runs only from `main`, and its GitHub environments are limited to `main`. Keep the tokens in the `prod` environment, not as repository secrets, so no pull request or other branch ever gets one.
- The application never uses the database admin login. Each process gets only its own role.

### Monthly cost estimate

DigitalOcean list prices at the time of writing. Check the pricing page before applying.

| Item | Staging | Production |
| --- | --- | --- |
| Droplet | about $12 | about $24, plus about $5 for backups |
| PostgreSQL | about $15 | about $30 |
| Spaces | $5 a month for the account, shared by every bucket including Terraform state | Same |
| Cloudflare | Free plan. Access is free for up to 50 users. | Same |

That is roughly $27 for staging, $59 for production and $5 for Spaces. **Staging is on hold to save cost**, so today it is about $64 a month for production alone, and $91 once staging is switched on.

## How changes are made

1. Change Terraform in a pull request. **Terraform checks** formats, validates, runs `terraform test` and shellcheck, with no credentials.
2. Merge after review.
3. **Actions → Provision → Run workflow** on `main`. Pick `shared`, `staging` or `production`, and `plan` or `apply`.
   - **Plan** runs in the GitHub environment (`prod` for `shared` and `production`, `dev` for `staging`) and shows the whole plan in the run summary. With required reviewers on the environment, approve it to start. That first approval only lets the plan run, before anyone has seen it: the review that matters is the second one, on apply, after you have read the plan.
   - **Apply** (only with `apply`) runs in the same environment and waits for your approval. Read the plan, then approve. Apply plans again and stops if that plan differs from the one you read. After applying it admits its own address to SSH, installs Docker on the host with `prepare-host.sh`, creates the database roles from the host, and removes its address again.

Nothing applies on merge. Nothing that costs money is created until someone runs an apply and approves it.


## One time setup

Everything else is created by Terraform. These are made by hand, once:

| Made by hand | Why |
| --- | --- |
| The Spaces bucket for Terraform state | Terraform needs it before it can run |
| The DigitalOcean, Spaces, Cloudflare and GitHub tokens | Terraform runs with them |
| The `finvestug.com` zone on Cloudflare | It belongs to the domain registration |
| The `prod` and `dev` GitHub environments, their main-only limits, required reviewers and secrets | They protect the tokens Terraform runs with |
| The deploy SSH key and the repository variables below | Inputs to Terraform |

### 1. In DigitalOcean, Cloudflare and GitHub tokens

1. **State bucket.** Create a private Spaces bucket in `lon1` with versioning on, for example `finvest-terraform-state`. It is the only thing made by hand, because Terraform needs it before it can run. It holds secrets, so keep it private.
2. **Tokens.** Plan and apply use the same ones:

| Secret name | Token |
| --- | --- |
| `TERRAFORM_DIGITALOCEAN_TOKEN` | API → Tokens, full access, or custom scopes with write on droplet, vpc, firewall, reserved_ip, database, project, ssh_key, monitoring, uptime, tag. Deploys also use it to open and close SSH. |
| `SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY` | A Spaces key with full access. Terraform uses it to create buckets and keys and to keep state. |
| `CLOUDFLARE_API_TOKEN` | Zone DNS Edit, Zone Settings Edit, Zone WAF Edit, SSL and Certificates Edit, Account Access: Apps and Policies Edit. Terraform finds the account from the zone, so there is no account ID to set. |
| `TERRAFORM_GITHUB_TOKEN` | A fine-grained GitHub token for this repository, read and write on Environments, Secrets and Variables |

3. **Cloudflare zone.** `finvestug.com` must already be a zone on the account.

### 2. In GitHub

Create the environments under **Settings → Environments**, and limit each to the `main` branch under **Deployment branches**. `prod` also serves `shared`. `dev` is needed only when staging is switched on.

| Environment | Serves | Protection | Secrets |
| --- | --- | --- | --- |
| `prod` | `shared` and `production` | Main only, you as required reviewer | The tokens above, plus `DEPLOY_SSH_KEY` |
| `dev` | `staging` | Main only, you as required reviewer | The same |

`DEPLOY_SSH_KEY` is a private key for the deploy user, made with `ssh-keygen -t ed25519 -N '' -f finvest-deploy`, with no passphrase. Provision installs its public half on the host, so there is no public key to set. Both environments can use the same key.

Repository variables under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
| --- | --- |
| `TERRAFORM_STATE_BUCKET` | The state bucket's name |
| `SSH_SOURCE_ADDRESSES` | Your own addresses, comma separated, such as `203.0.113.10` |
| `ADMIN_ACCESS_EMAILS` | Staff emails allowed into the back office, comma separated, such as `a@finvestug.com,b@finvestug.com`. Required for production. |
| `ALERT_EMAILS` | Who gets alerts, comma separated like `ADMIN_ACCESS_EMAILS`. Optional, but set two people before launch. |
| `CLIENT_APP_PUBLIC` | Leave unset until switch day. Until then the production client app host lets in only `ADMIN_ACCESS_EMAILS`, through Cloudflare Access, while the API stays public. On switch day ([docs/launch.md](../docs/launch.md)) set it to `true` and run Provision `production` (plan, then apply) to lift Access from the app host. The back office keeps it. |

## Provision

1. Provision `shared` with `plan`, then `apply`. It creates the `finvest` project, the zone settings and the API rate limit.
2. Provision `production` with `plan`, read it, then `apply`. Staging is on hold: Provision refuses it until the repository variable `STAGING_ENABLED` is `true` (see Staging on hold below).
3. Database roles are created only once `backend/db/init-roles.sql` is on `main`. If it is not there yet, the run says so; run apply again later. It never changes an existing role's password.
4. Turn on deploys for the environment (see Deploy below).

## Deploy

Deploys follow Fidiba. A push to the source branch deploys its environment, and you can also run the workflow by hand on that branch.

| Workflow | Source branch | GitHub environment |
| --- | --- | --- |
| Deploy Staging | `main` | `dev` |
| Deploy Production | `release` | `prod`, so it waits for your approval |

A workflow does nothing until its environment is listed in the repository variable `DEPLOY_ENVIRONMENTS` by its GitHub name (`prod` while staging is on hold, `dev,prod` later). Add an environment only after Provision has applied it.

### Staging on hold

Staging costs about $27 a month, so it is switched off for now (the owner's decision). Everything for it stays in the repository and is checked on every pull request, but nothing creates or deploys it:

- Provision refuses `staging` with a message, for both plan and apply.
- Deploy Staging skips every push to `main`.
- Its GitHub environment (`dev`), settings and secrets need not exist yet.

The CI rehearsal still runs a whole deployment on the runner on every infrastructure pull request, using staging's names. It creates nothing in the cloud and costs nothing.

Without staging, a release goes from `main` to production by updating the `release` branch, so check a change in the rehearsal and in review before you push `release`.

To switch staging on: set the repository variable `STAGING_ENABLED` to `true`, create the `dev` GitHub environment and its settings (above), run Provision for `staging`, then add `dev` to `DEPLOY_ENVIRONMENTS`. To switch it off again, first destroy its resources (ask the infrastructure thread), then set `STAGING_ENABLED` back.

Each run:

1. Builds the backend, console and website images, pushes them to GitHub's container registry and deploys their exact digests. The console is built with `VITE_DEPLOY_ENV` set to the environment, so a production console cannot contain the sample sign-in.
2. Writes one release bundle (`prepare_release.py`). Each process gets a runtime file with only its own database role, verified TLS to the database against DigitalOcean's CA, and the sign-in settings for the environment's domains.
3. Admits its own address to SSH on the firewall, copies the bundle to `/opt/finvest/releases/<id>` and runs `deploy.sh` there, then removes its address again.

On the host, `deploy.sh`:

1. Takes a lock, validates the Compose and Caddy configuration and pulls the pinned images.
2. Checks all three database roles are inside their boundaries, then runs migrations as `finvest_migrator` while the current release keeps serving.
3. Starts the new colour (blue or green) beside the old one and waits for every health check.
4. Switches Caddy to the new colour with a reload, which drops no connections.
5. Checks the API, the console and the website (where it is served) through the public address. If that fails, it switches back and removes the new colour. If it passes, it records the colour and stops the old one.

Migrations must be able to run beside the previous release: add first, remove in a later release. Before migrating, `deploy.sh` runs the release preflight from the new image (`python -m app.release_preflight`, ported from Fidiba) as `finvest_migrator`. It only reads, and it:

- refuses a database at a revision the image does not know, which means a newer release already migrated it (deploy that release, or restore its backup);
- stops the old release first when a pending migration says `zero_downtime = False`, so visitors see "being updated" until the new one is up.

A unit test (`backend/tests/unit/test_migration_zero_downtime.py`) fails any migration that drops or renames a table, column or function, makes a column required, or adds a required column with no default, unless it says `zero_downtime` either way. It reads only the shape of the SQL: it cannot see a new trigger, guard or CHECK that refuses what the old release writes, so that stays the author's and the reviewer's job. To force a stop for one deploy anyway, set the environment variable `STOP_BEFORE_MIGRATE=true`.

To go back to an earlier version, revert the commit on the source branch and let it deploy. Rerunning an old run is refused, because a deploy only accepts the branch head.

### Settings you add for deploys

In each of the `prod` and `dev` GitHub environments, beside what Terraform writes:

| Name | Kind | Value |
| --- | --- | --- |
| `FINVEST_AUTHENTICATOR_KEY` | Secret | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, one per environment. It protects staff authenticator secrets. Never change it once staff have enrolled, and keep an offline copy. |
| `FINVEST_IDENTITY_DATA_KEY` | Secret | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, one per environment. It encrypts clients' document numbers, dates of birth and TINs, and reaches the API and worker only. Never change it once clients have been checked: saved checks become unreadable. Keep an offline copy. |
| `FINVEST_PAYMENTS_PROVIDER`, `FINVEST_IOTEC_CURRENCY_CODE` | Variables | `iotec` and `UGX` once ioTec Pay is set up ([docs/payments.md](../docs/payments.md)). Leave unset until then. The API, worker and scheduler get them. `ITX` (an ioTec test wallet) is refused in production. |
| `FINVEST_IOTEC_CLIENT_ID`, `FINVEST_IOTEC_WALLET_ID` | Variables | From the ioTec Pay portal. API and worker only. Payments and payouts share them. |
| `FINVEST_IOTEC_CLIENT_SECRET` | Secret | From the ioTec Pay portal. API and worker only. |
| `FINVEST_IOTEC_CALLBACK_TOKEN` | Secret | A random value you choose (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). Enter the same value in ioTec's portal as the security header `Authorization: Bearer <value>` on the wallet's collection callback, pointing at `https://api.finvestug.com/provider-callbacks/collections/iotec`. API only. |
| `FINVEST_IOTEC_TOKEN_URL`, `FINVEST_IOTEC_PAY_URL`, `FINVEST_IOTEC_TIMEOUT_SECONDS` | Variables, optional | Default to `https://id.iotec.io/connect/token`, `https://pay.iotec.io` and `30`. |
| `FINVEST_IDENTITY_PROVIDER` | Variable | Unset (manual review) until the ioTec Verify credentials exist, then `iotec` ([docs/identity-checks.md](../docs/identity-checks.md)). API and worker. |
| `FINVEST_IOTEC_VERIFY_CLIENT_ID`, `FINVEST_IOTEC_VERIFY_API_KEY` | Secrets | From the ioTec Verify portal (Management, Settings, Configuration). Required once the provider is `iotec`. API and worker. |
| `FINVEST_IOTEC_VERIFY_BASE_URL` | Variable, optional | Defaults to `https://verify-api.iotec.io`. |
| `FINVEST_PAYOUT_NAME_PROVIDER` | Variable | Unset (`none`) until the ioTec Verify credentials exist, then `iotec`: the worker reads the name a network holds for a payout number with the same Verify credentials. Until then a new payout number waits for proof. Worker only. |
| `FINVEST_SMS_BACKEND`, `FINVEST_SMS_IOTEC_CLIENT_ID` | Variables | `iotec`, and the client id from the ioTec Messaging portal (Management, Settings, Configuration). Only the worker gets them; it sends every SMS. |
| `FINVEST_SMS_IOTEC_API_KEY` | Secret | The ioTec Messaging API key from the same page. Worker only. |
| `FINVEST_SMS_IOTEC_BASE_URL` | Variable, optional | Defaults to `https://messaging-api.iotec.io`. The host's firewall already allows outbound HTTPS. |
| `FINVEST_EMAIL_HASH_KEY` | Secret | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, one per environment. It keys the hash under which email addresses are kept on the suppression list and in delivery events, and reaches the API and worker only. Never change it once set: every stored hash would stop matching. Keep an offline copy. |
| `FINVEST_RESEND_API_KEY` | Secret | An API key from Finvest's own Resend account, once email is set up. Deploy hands it to the worker as `FINVEST_EMAIL_RESEND_API_KEY`. |
| `FINVEST_EMAIL_BACKEND`, `FINVEST_EMAIL_FROM` | Variables | `resend` and `Finvest <no-reply@finvestug.com>` |
| `RESEND_HOOK_SECRET` | Secret | The signing secret (`whsec_...`) of the webhook in Finvest's own Resend account, pointed at `https://<api>/webhooks/resend`. Not the Resend API key. Only the API gets it, to check each delivery event. Without it the webhook answers 404 and delivery events are lost. |
| `FINVEST_CLIENT_SIGNUP_OPEN` | Variable | `true` opens client sign-up on the API. Leave it unset in production until switch day ([docs/launch.md](../docs/launch.md)). In staging, set it only for the pen test weeks. Anything else keeps sign-up closed. |
| `FINVEST_BANK_TRANSFER_ACCOUNTS` | Variable | The trust account clients pay bank transfers into, as JSON keyed by currency: `{"UGX": {"bank_name": "...", "account_name": "...", "account_number": "...", "branch": null}}`. It must be the account whose statements reconciliation imports. Not a secret. Until it is set, paying by bank transfer is unavailable and the app offers mobile money. |
| `SITE_PUBLISHED` | Variable, production only | The owner's decision, nobody else's: `true` puts the public website live at the production address. Set it when the owner launches the website. |
| `SITE_INDEXABLE` | Variable, production only | `true` lets search engines index the website. Leave it unset until launch. Staging, the API, the back office and the client app address always send `X-Robots-Tag: noindex`, whatever this says. |

`DEPLOY_SSH_KEY` is already there from provisioning, and Provision records `DEPLOY_HOST_KEY` itself.

Every process also gets `FINVEST_ENVIRONMENT` (`staging` or `production`), taken from the environment being deployed, so code that must not run in production can refuse to start there.

### What is served today

| Address | Serves |
| --- | --- |
| API | The backend |
| Back office | The console, behind Cloudflare Access in production |
| Public website | The website from `apps/site`, built for the environment's own addresses. Staging always serves it. Production answers "on its way" until `SITE_PUBLISHED` is `true`. |
| Client app | A "coming soon" answer until the web app is built |

### Rehearsal

`bash infra/deployment/rehearse.sh` runs the whole deployment on one machine with Docker: real images, roles, migrations, two switches while polling the API, and a failed check that must switch back. CI runs it on every infrastructure pull request.

## Host access

- The deploy user has passwordless sudo, which `prepare-host.sh` needs, and is in the `docker` group. Both are root equivalent. Treat `DEPLOY_SSH_KEY` like a root key.
- SSH keys are written once at first boot. To change who can log in, edit `/home/deploy/.ssh/authorized_keys` on the host and update `DEPLOY_SSH_KEY` so a rebuilt host matches.
- The firewall reads Cloudflare's ranges at apply time. A range Cloudflare adds later stays blocked until the next apply, so run Provision apply now and then even with no changes.
- The host's SSH key is pinned. The first Provision run that reaches a new host trusts the key it sees at the reserved IP, because DigitalOcean publishes no host key to check it against, and records it as the `DEPLOY_HOST_KEY` variable of the environment. Every later Provision run and every deploy accepts only that key. If the server is rebuilt, delete `DEPLOY_HOST_KEY` from the environment and run Provision apply, which records the new one.
- A run opens port 22 to its own runner and closes it when it ends. The runner's address comes from two public services (`infra/deployment/runner-address.sh`) that must agree. A deploy fails loudly if its rule is still there at the end. Provision apply resets the firewall to `SSH_SOURCE_ADDRESSES`, so run it to clear anything a broken run left behind.
- There is no state lock yet. Runs for one environment queue behind each other, so do not apply from a laptop. Terraform's lock file (`use_lockfile = true`) has not been tested on Spaces.

## Open before production

- The firewall proves traffic came through Cloudflare, not through this zone. Turn on Authenticated Origin Pulls in Caddy, or have the console check the Access token, before production.
- Production runs one database node. A standby (`database_node_count = 2`) roughly doubles the database cost.

## Checks without credentials

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```
