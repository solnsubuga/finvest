# infra

Infrastructure as code for Finvest: Terraform for DigitalOcean and Cloudflare. Owned by the infrastructure thread. Feature PRs do not change it.

## Shape

Each environment (staging and production) is one Terraform root in `terraform/envs/<env>` that calls the shared module in `terraform/modules/finvest-env`.

| Piece | What it is |
| --- | --- |
| Network | A private VPC per environment. |
| Application host | One Ubuntu 24.04 droplet with Docker and Compose, a reserved IP, and a `deploy` user. It runs Caddy, the API, the worker and the static frontends. Root login and passwords over SSH are off. |
| Host firewall | Ports 80 and 443 accept traffic only from Cloudflare, so nobody can skip the edge. SSH only from `ssh_allowed_cidrs`. |
| Database | Managed PostgreSQL 17 with daily backups and point in time recovery, on the private network, reachable only from the host. Protected from `terraform destroy`. |
| Documents | A private, versioned Spaces bucket for ID photos, receipts and statements, with CORS for presigned uploads from the app and console. Protected from `terraform destroy`. |
| DNS and edge | Proxied Cloudflare records for every hostname. Production also owns the zone settings: Full (strict) TLS, HTTPS only, TLS 1.2 minimum. |
| Back office | Optional Cloudflare Access in front of the console, so only listed staff emails reach it, on top of the console's own password and passkey sign in. |
| Alerts | Uptime check on `https://<api>/health`, and CPU and disk alerts on the host, when `alert_emails` is set. |

| Address | Production | Staging |
| --- | --- | --- |
| Public website | finvestug.com, www.finvestug.com | staging.finvestug.com |
| Client app | app.finvestug.com | app-staging.finvestug.com |
| Back office | admin.finvestug.com | admin-staging.finvestug.com |
| API | api.finvestug.com | api-staging.finvestug.com |

Staging names sit one level below the zone so Cloudflare's free certificate covers them.

### Monthly cost estimate

DigitalOcean list prices at the time of writing. Check the pricing page before applying.

| Item | Staging | Production |
| --- | --- | --- |
| Droplet | s-1vcpu-2gb, about $12 | s-2vcpu-4gb, about $24, plus about $5 for weekly backups |
| PostgreSQL | db-s-1vcpu-1gb, about $15 | db-s-1vcpu-2gb, about $30 |
| Spaces | $5 a month for the account, shared by every bucket including Terraform state | |
| Cloudflare | Free plan. Access is free for up to 50 users. | |

That is roughly $27 for staging, $59 for production and $5 for Spaces, about $91 a month together.

## How changes are made

1. Change Terraform in a pull request. The **Terraform** workflow checks formatting, validates both environments and, once the secrets exist, posts a plan for each environment in the run summary.
2. Merge after review.
3. Apply by hand: **Actions → Terraform → Run workflow** on `main`, pick the environment. The run plans again, then waits for approval on the GitHub environment of the same name before applying.

Nothing applies on merge. Nothing that costs money is created until someone runs step 3 and approves it.

## One time setup

### 1. Tokens, as GitHub Actions secrets

Add these under **Settings → Secrets and variables → Actions → Secrets**. They are never committed.

| Secret | Where it comes from |
| --- | --- |
| `DIGITALOCEAN_TOKEN` | DigitalOcean → API → Tokens. A custom scoped token with read and write on droplet, vpc, firewall, reserved_ip, database, project, ssh_key, monitoring, uptime and tag. |
| `SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY` | DigitalOcean → Spaces Object Storage → Access Keys, with full access. Used for the state bucket and to create the documents bucket. |
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens. Zone: DNS Edit and Zone Settings Edit on finvestug.com, plus Account: Access: Apps and Policies Edit if Access is used. |

### 2. Variables, as GitHub Actions variables

Under **Settings → Secrets and variables → Actions → Variables**. These are not secret, but they are kept out of the repository.

| Variable | Example |
| --- | --- |
| `TF_SSH_PUBLIC_KEYS` | `{"solo":"ssh-ed25519 AAAA... solo"}` |
| `TF_SSH_ALLOWED_CIDRS` | `["203.0.113.10/32"]` |
| `CLOUDFLARE_ACCOUNT_ID` | The account id from the Cloudflare dashboard. Needed only for Access. |
| `TF_ADMIN_ACCESS_EMAILS` | `["staff@finvestug.com"]`. Leave unset to keep Access off. |
| `TF_ALERT_EMAILS` | `["ops@finvestug.com"]`. Leave unset for no alerts. |

### 3. GitHub environments

Create environments named `staging` and `production` under **Settings → Environments**, with yourself as a required reviewer and deployment limited to the `main` branch.

### 4. State bucket

Create a private Spaces bucket named `finvest-tfstate` in `fra1` by hand, with versioning on. If the name is taken, change `bucket` in both `terraform/envs/*/backend.tf`. There is no state lock. The workflow runs one job per environment at a time instead, so do not run `apply` from a laptop.

### 5. Cloudflare zone

`finvestug.com` must already be a zone on the Cloudflare account, with the registrar pointing at Cloudflare's name servers.

## Deliberately outside Terraform

These hold secrets, and Terraform would copy them into its state file, so they are made by hand once and handed to the deploy as GitHub secrets.

- **Origin certificate for Caddy.** Cloudflare → SSL/TLS → Origin Server → Create certificate, for the hostnames above. It goes on the host at `/etc/finvest/tls/`.
- **The app's Spaces key.** A key limited to the documents bucket, for the API and worker.
- **Database roles.** The app's least-privilege roles and row policies are created by migrations, not Terraform.

One secret cannot be kept out: DigitalOcean returns the database admin password to Terraform, so it is in the state file. The state bucket is private and only the workflow's Spaces key can read it.

## Open before production

- The region is `fra1` (Frankfurt). DigitalOcean has no region in Africa. Confirm a European region against Uganda's Data Protection and Privacy Act before production data is stored.
- Production runs one database node. Add a standby (`db_node_count = 2`, about double the database cost) when downtime during a node failure stops being acceptable.

## Checks without the secrets

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform/envs/staging init -backend=false
terraform -chdir=infra/terraform/envs/staging validate
```
