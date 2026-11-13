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
| Back office | Cloudflare Access in front of the console, so only listed staff emails reach it, on top of the console's own password and passkey sign in. Required in production: a plan fails without staff emails. Optional in staging. |
| Alerts | Uptime check on `https://<api>/health`, and CPU and disk alerts on the host, when `alert_emails` is set. |

| Address | Production | Staging |
| --- | --- | --- |
| Public website | finvestug.com, www.finvestug.com | staging.finvestug.com |
| Client app | app.finvestug.com | app-staging.finvestug.com |
| Back office | admin.finvestug.com | admin-staging.finvestug.com |
| API | api.finvestug.com | api-staging.finvestug.com |

Staging names sit one level below the zone so Cloudflare's free certificate covers them. `api.finvestug.com` is still an open question for the owner in `docs/decisions.md`. Changing it later is a one-line change in `terraform/envs/production/main.tf`.

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

1. Change Terraform in a pull request. The **Terraform** workflow checks formatting and validates both environments. Pull request runs hold no credentials at all.
2. Merge after review.
3. Plan and apply by hand: **Actions → Terraform → Run workflow** on `main`, and pick the environment.
   - **Plan** runs in the `<env>-plan` environment with read-only tokens and shows the full plan in the run summary.
   - **Apply** runs in the `<env>` environment, which holds the write tokens and waits for your approval. Read the plan, then approve. Apply plans again and stops if that plan differs from the one you read.

On the first staging run, check that Apply's plan matches Plan's (the read-only and write tokens must see the same state). If Apply stops with "The plan changed" while nothing changed, the plan tokens are missing a read scope.

Nothing applies on merge. Nothing that costs money is created until someone runs step 3 and approves it.

## One time setup

### 1. GitHub environments and tokens

Create four environments under **Settings → Environments**. Limit every one of them to the `main` branch under **Deployment branches**, so no other branch can read their secrets.

| Environment | Protection | Secrets |
| --- | --- | --- |
| `staging-plan`, `production-plan` | Main branch only | Read-only tokens |
| `staging`, `production` | Main branch only, you as required reviewer | Write tokens |

Each environment gets the same four secret names. They are never committed.

| Secret | Plan environments (read only) | Apply environments (read and write) |
| --- | --- | --- |
| `DIGITALOCEAN_TOKEN` | DigitalOcean → API → Tokens, custom scopes, read only on droplet, vpc, firewall, reserved_ip, database, project, ssh_key, monitoring, uptime and tag | The same scopes with read and write |
| `SPACES_ACCESS_KEY_ID`, `SPACES_SECRET_ACCESS_KEY` | Spaces → Access Keys, a key with read on `finvest-tfstate` and the documents buckets | A key with full access. It creates the documents buckets and writes state. |
| `CLOUDFLARE_API_TOKEN` | My Profile → API Tokens, Zone DNS Read, Zone Settings Read, and Account Access: Apps and Policies Read | The same with Edit |

### 2. Variables, as GitHub Actions variables

Under **Settings → Secrets and variables → Actions → Variables**. These are not secret, but they are kept out of the repository.

| Variable | Example |
| --- | --- |
| `TF_SSH_PUBLIC_KEYS` | `{"solo":"ssh-ed25519 AAAA... solo"}` |
| `TF_SSH_ALLOWED_CIDRS` | `["203.0.113.10/32"]` |
| `CLOUDFLARE_ACCOUNT_ID` | The account id from the Cloudflare dashboard. Needed only for Access. |
| `TF_ADMIN_ACCESS_EMAILS` | `["staff@finvestug.com"]`. Required for production. |
| `TF_ALERT_EMAILS` | `["ops@finvestug.com"]`. Leave unset for no alerts. |

### 3. State bucket

Create a private Spaces bucket named `finvest-tfstate` in `fra1` by hand, with versioning on. If the name is taken, change `bucket` in both `terraform/envs/*/backend.tf`. There is no state lock yet. The workflow queues manual runs for one environment behind each other instead, so do not run `apply` from a laptop. Terraform's own lock file (`use_lockfile = true`) needs a conditional write that has not been tested on Spaces. Try it against the state bucket once it exists.

### 4. Cloudflare zone

`finvestug.com` must already be a zone on the Cloudflare account, with the registrar pointing at Cloudflare's name servers.

## Deliberately outside Terraform

These hold secrets, and Terraform would copy them into its state file, so they are made by hand once and handed to the deploy as GitHub secrets.

- **Origin certificate for Caddy.** Cloudflare → SSL/TLS → Origin Server → Create certificate, for the hostnames above. It goes on the host at `/etc/finvest/tls/`.
- **The app's Spaces key.** A key limited to the documents bucket, for the API and worker.
- **Database roles.** The app's least-privilege roles and row policies are created by migrations, not Terraform.

One secret cannot be kept out: DigitalOcean returns the database admin password to Terraform, so it is in the state file. The state bucket is private and only the workflow's Spaces key can read it.

## Host access

- The `deploy` user's SSH keys are written once, at first boot. Changing `TF_SSH_PUBLIC_KEYS` later updates the DigitalOcean key list but not the host, so removing someone's key there does not lock them out. Add and remove keys in `/home/deploy/.ssh/authorized_keys` on the host, and update the variable to match so a rebuilt host gets the same list.
- `deploy` is in the `docker` group, which is root equivalent. Treat its keys like root keys.
- The firewall reads Cloudflare's address ranges at apply time. A range Cloudflare adds later stays blocked until the next apply, so apply now and then even with no changes.

## Open before production

- The region is `fra1` (Frankfurt). DigitalOcean has no region in Africa. Confirm a European region against Uganda's Data Protection and Privacy Act before production data is stored.
- The firewall proves traffic came through Cloudflare, not through this Cloudflare zone. Another Cloudflare account could point its own hostname at the host and skip Access. Turn on Authenticated Origin Pulls with a zone certificate in Caddy, or have the console check the Access token, before production.
- Production runs one database node. Add a standby (`db_node_count = 2`, about double the database cost) when downtime during a node failure stops being acceptable.

## Checks without the secrets

```sh
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform/envs/staging init -backend=false
terraform -chdir=infra/terraform/envs/staging validate
```
