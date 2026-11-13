# infra

Infrastructure as code for Finvest, modelled on Fidiba's provisioning. Terraform creates every DigitalOcean resource, from the host to the Spaces buckets, inside one DigitalOcean project called `finvest`, and puts Cloudflare in front. Owned by the infrastructure thread. Feature PRs do not change it.

| Path | Contents |
| --- | --- |
| `terraform/shared/` | The `finvest` project and the Cloudflare zone settings that every environment shares. Provision it first. |
| `terraform/` | One environment: host, network, database, buckets, DNS, back office access, alerts, and the settings it writes into GitHub. |
| `terraform/environments/*.tfvars` | What differs between `staging` and `production`. Each has its own state, so planning one never touches the other. |
| `terraform/tests/` | `terraform test` with mock providers: the rules below that must not drift. |
| `deployment/` | Scripts the Provision run executes on the host: `prepare-host.sh` (from Fidiba) and `create-roles.sh`. |

## What an environment gets

| Resource | Staging | Production | Why |
| --- | --- | --- | --- |
| VPC | `10.20.0.0/20` | `10.10.0.0/20` | Keeps database traffic private |
| Droplet | Ubuntu 24.04, `s-1vcpu-2gb` | `s-2vcpu-4gb`, weekly backups | Runs the Compose stack: Caddy, API, worker, scheduler, frontends. 2 GB swap. |
| Reserved IP | Assigned to the droplet | Same | Stable address for DNS and `DEPLOY_HOST`, kept if the droplet is rebuilt |
| Cloud firewall | Same rules | Same rules | 80 and 443 only from Cloudflare's ranges, so nobody can skip the edge. SSH only from `SSH_SOURCE_ADDRESSES`, plus the Provision run while it prepares the host. |
| Managed PostgreSQL 17 | `db-s-1vcpu-1gb` | `db-s-1vcpu-2gb` | One node, database `finvest`, daily backups and point in time recovery. Only the droplet may connect. |
| Database roles | Same | Same | `finvest_migrator`, `finvest_app`, `finvest_worker`, created from the host with `backend/db/init-roles.sql` and generated passwords |
| Spaces bucket and key | `finvest-staging-documents` | `finvest-production-documents` | Private, versioned, CORS for presigned uploads, and a key limited to the bucket |
| Cloudflare | Proxied records, Origin CA certificate | Same, plus Access on the back office | Full (strict) TLS from the edge to the host |
| GitHub settings | Written into the `staging` environment | Written into `production` | See below |
| Alerts | When `ALERT_EMAILS` is set | Same | Uptime on `https://<api>/health/ready`, host CPU and disk |
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

Terraform owns these names in the `staging` and `production` GitHub environments. Change them in `infra/terraform`, not in GitHub, or the next Provision run puts them back. The deploy workflow reads them.

| Kind | Names |
| --- | --- |
| Variables | `SITE_DOMAINS`, `APP_DOMAIN`, `ADMIN_DOMAIN`, `API_DOMAIN`, `DEPLOY_HOST`, `DEPLOY_USER`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `FINVEST_OBJECT_STORAGE_BUCKET`, `FINVEST_OBJECT_STORAGE_ENDPOINT`, `FINVEST_OBJECT_STORAGE_REGION` |
| Secrets | `DATABASE_CA_CERT`, `DATABASE_MIGRATOR_PASSWORD`, `DATABASE_APP_PASSWORD`, `DATABASE_WORKER_PASSWORD`, `FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID`, `FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY`, `ORIGIN_TLS_CERT`, `ORIGIN_TLS_KEY` |

GitHub refuses to create a variable that already exists, so do not create these by hand.

### Secrets in Terraform state

Like Fidiba, Terraform generates and holds these credentials, so they are in its state file: the database admin connection, the three role passwords, the documents bucket key and the origin certificate key. This is the one recorded exception to "no secrets in Terraform state" (see `docs/decisions.md`). It is kept safe like this:

- The state bucket is private, and only the Provision workflow's Spaces keys can read it.
- The Provision workflow runs only from `main`, and its GitHub environments are limited to `main`, so no pull request or other branch ever gets a token.
- The application never uses the database admin login. Each process gets only its own role.

### Monthly cost estimate

DigitalOcean list prices at the time of writing. Check the pricing page before applying.

| Item | Staging | Production |
| --- | --- | --- |
| Droplet | about $12 | about $24, plus about $5 for backups |
| PostgreSQL | about $15 | about $30 |
| Spaces | $5 a month for the account, shared by every bucket including Terraform state | Same |
| Cloudflare | Free plan. Access is free for up to 50 users. | Same |

That is roughly $27 for staging, $59 for production and $5 for Spaces, about $91 a month together.

## How changes are made

1. Change Terraform in a pull request. **Terraform checks** formats, validates, runs `terraform test` and shellcheck, with no credentials.
2. Merge after review.
3. **Actions → Provision → Run workflow** on `main`. Pick `shared`, `staging` or `production`, and `plan` or `apply`.
   - **Plan** runs in `<env>-plan` with read-only tokens and shows the whole plan in the run summary.
   - **Apply** (only with `apply`) runs in `<env>`, which holds the write tokens and waits for your approval. Read the plan, then approve. Apply plans again and stops if that plan differs from the one you read. After applying it admits its own address to SSH, installs Docker on the host with `prepare-host.sh`, creates the database roles from the host, and removes its address again.

Nothing applies on merge. Nothing that costs money is created until someone runs an apply and approves it.

On the first staging run, check that Apply's plan matches Plan's. If Apply stops with "The plan changed" while nothing changed, a read-only token is missing a read scope.

## One time setup

Everything else is created by Terraform. These are made by hand, once:

| Made by hand | Why |
| --- | --- |
| The Spaces bucket for Terraform state | Terraform needs it before it can run |
| The DigitalOcean, Spaces, Cloudflare and GitHub tokens | Terraform runs with them |
| The `finvestug.com` zone on Cloudflare | It belongs to the domain registration |
| The four GitHub environments, their main-only limits, required reviewers and secrets | They protect the tokens Terraform runs with |
| The deploy SSH key pair and the repository variables below | Inputs to Terraform |

### 1. In DigitalOcean, Cloudflare and GitHub tokens

1. **State bucket.** Create a private Spaces bucket in `lon1` with versioning on, for example `finvest-terraform-state`. It is the only thing made by hand, because Terraform needs it before it can run. It holds secrets, so keep it private.
2. **Tokens.** A write set for applies and a read-only set for plans:

| Secret name | Read-only (plan environments) | Read and write (apply environments) |
| --- | --- | --- |
| `DIGITALOCEAN_TOKEN` | API → Tokens, custom scopes, read on droplet, vpc, firewall, reserved_ip, database, project, ssh_key, monitoring, uptime, tag | Full access, or the same scopes with write |
| `SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY` | A Spaces key with read on the state bucket and the documents buckets | A Spaces key with full access. Terraform uses it to create buckets and keys and to keep state. |
| `CLOUDFLARE_API_TOKEN` | Zone DNS Read, Zone Settings Read, SSL and Certificates Read, Account Access: Apps and Policies Read | The same with Edit |
| `TERRAFORM_GITHUB_TOKEN` | A fine-grained GitHub token for this repository, read on Environments, Secrets and Variables | The same with read and write |

3. **Cloudflare zone.** `finvestug.com` must already be a zone on the account.

### 2. In GitHub

Create four environments under **Settings → Environments**, and limit every one of them to the `main` branch under **Deployment branches**. `production-plan` and `production` also serve `shared`.

| Environment | Protection | Secrets |
| --- | --- | --- |
| `staging-plan`, `production-plan` | Main only | The read-only set |
| `staging`, `production` | Main only, you as required reviewer | The write set, plus `DEPLOY_SSH_KEY` |

`DEPLOY_SSH_KEY` is a private key for the deploy user, made with `ssh-keygen -t ed25519 -N '' -f finvest-deploy`. Both environments can use the same key.

Repository variables under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
| --- | --- |
| `TERRAFORM_STATE_BUCKET` | The state bucket's name |
| `DEPLOY_SSH_PUBLIC_KEY` | The public half of `DEPLOY_SSH_KEY` (`finvest-deploy.pub`) |
| `SSH_SOURCE_ADDRESSES` | Your own addresses, comma separated, such as `203.0.113.10` |
| `CLOUDFLARE_ACCOUNT_ID` | From the Cloudflare dashboard. Required for production. |
| `ADMIN_ACCESS_EMAILS` | Staff emails allowed into the back office, comma separated. Required for production. |
| `ALERT_EMAILS` | Optional, comma separated |

## Provision

1. Provision `shared` with `plan`, then `apply`. It creates the `finvest` project and the zone settings.
2. Provision `staging` with `plan`, read it, then `apply`. Then do the same for `production`.
3. Database roles are created only once `backend/db/init-roles.sql` is on `main`. If it is not there yet, the run says so; run apply again later. It never changes an existing role's password.
4. Deploy. The deploy workflow (images, blue and green rollout with checks and rollback, as in Fidiba) comes in its own pull request.

## Host access

- The deploy user has passwordless sudo, which `prepare-host.sh` needs, and is in the `docker` group. Both are root equivalent. Treat `DEPLOY_SSH_KEY` like a root key.
- SSH keys are written once at first boot. To change who can log in, edit `/home/deploy/.ssh/authorized_keys` on the host and update `DEPLOY_SSH_PUBLIC_KEY` so a rebuilt host matches.
- The firewall reads Cloudflare's ranges at apply time. A range Cloudflare adds later stays blocked until the next apply, so run Provision apply now and then even with no changes.
- The Provision run trusts the host key it sees on first contact with the reserved IP (`accept-new`). DigitalOcean does not publish droplet host keys through its API, so there is nothing to check it against before that. Each run starts with an empty known hosts file.
- There is no state lock yet. Runs for one environment queue behind each other, so do not apply from a laptop. Terraform's lock file (`use_lockfile = true`) has not been tested on Spaces.

## Open before production

- The region is `lon1` (London), as in Fidiba. DigitalOcean has no region in Africa. Confirm it against Uganda's Data Protection and Privacy Act before production data is stored.
- The firewall proves traffic came through Cloudflare, not through this zone. Turn on Authenticated Origin Pulls in Caddy, or have the console check the Access token, before production.
- Production runs one database node. A standby (`database_node_count = 2`) roughly doubles the database cost.

## Checks without credentials

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```
