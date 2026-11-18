data "cloudflare_zone" "this" {
  filter = {
    name = var.zone_name
  }
}

data "cloudflare_ip_ranges" "this" {}

locals {
  admin_on_tailnet = var.admin_tailnet_address != ""
}

# Every hostname is proxied through Cloudflare to the reserved IP, except the back office once it
# is on the tailnet (owner, 2026-11-18). Its name then resolves to the server's Tailscale address,
# which only devices on Finvest's tailnet can reach. Cloudflare does not proxy it, so the edge gets
# a public certificate for it through a DNS challenge (FINVEST_EDGE_DNS_TOKEN). The address is no
# secret: it is unreachable from the internet. The record changes in place either way.
resource "cloudflare_dns_record" "hosts" {
  for_each = toset(local.hostnames)

  zone_id = data.cloudflare_zone.this.id
  name    = each.value
  type    = "A"
  content = local.admin_on_tailnet && each.value == var.admin_domain ? var.admin_tailnet_address : digitalocean_reserved_ip.app.ip_address
  proxied = !(local.admin_on_tailnet && each.value == var.admin_domain)
  ttl     = local.admin_on_tailnet && each.value == var.admin_domain ? 300 : 1
  comment = "Managed by Terraform (${var.environment})"
}

# Cloudflare Origin CA certificate, so the edge to origin hop is Full (strict) TLS. Caddy serves
# it; the deploy reads it from the GitHub environment. No ACME is needed, because the host only
# accepts Cloudflare.
resource "tls_private_key" "origin" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "origin" {
  private_key_pem = tls_private_key.origin.private_key_pem

  subject {
    common_name  = var.api_domain
    organization = "Finvest"
  }

  dns_names = local.hostnames
}

resource "cloudflare_origin_ca_certificate" "this" {
  csr                = tls_cert_request.origin.cert_request_pem
  hostnames          = local.hostnames
  request_type       = "origin-ecc"
  requested_validity = 5475
}

# Cloudflare Access guards the back office only until it moves onto the tailnet (owner,
# 2026-11-18); a name Cloudflare does not proxy cannot be behind Access. On the tailnet, the
# network and the console's own sign-in (a password and then a passkey or authenticator code for
# every member of staff) guard it. Access also holds the client app back before launch.

# Staff the console has invited. The worker adds an email when a team invite or reactivation is
# approved and removes it on deactivation, so Terraform creates the list but never touches its
# entries. ADMIN_ACCESS_EMAILS stays as the break-glass list that Terraform owns.
resource "cloudflare_zero_trust_list" "staff_emails" {
  count = length(var.admin_access_emails) > 0 ? 1 : 0

  account_id  = data.cloudflare_zone.this.account.id
  name        = "${local.name} staff invited in the console"
  description = "Managed by the Finvest worker from console Team changes. Do not edit by hand."
  type        = "EMAIL"

  lifecycle {
    ignore_changes = [items]
  }
}

locals {
  # Who Access lets in: the break-glass emails, and anyone on the console's list.
  staff_include = concat(
    [for e in var.admin_access_emails : { email = { email = e } }],
    [for l in cloudflare_zero_trust_list.staff_emails : { email_list = { id = l.id } }],
  )
}

resource "cloudflare_zero_trust_access_policy" "admin_staff" {
  count = length(var.admin_access_emails) > 0 ? 1 : 0

  account_id = data.cloudflare_zone.this.account.id
  name       = "${local.name} staff"
  decision   = "allow"
  include    = local.staff_include
}

# Until switch day the production client app is for staff only. The API
# stays public: provider callbacks, Resend webhooks and the phone apps call it directly. The
# phone apps are held back from the stores until this is lifted (docs/launch.md).
resource "cloudflare_zero_trust_access_application" "app" {
  count = var.environment == "production" && !var.client_app_public ? 1 : 0

  zone_id          = data.cloudflare_zone.this.id
  name             = "${local.name} client app (staff only until launch)"
  domain           = var.app_domain
  type             = "self_hosted"
  session_duration = "12h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.admin_staff[0].id
    precedence = 1
  }]
}

resource "cloudflare_zero_trust_access_application" "admin" {
  count = length(var.admin_access_emails) > 0 && !local.admin_on_tailnet ? 1 : 0

  zone_id          = data.cloudflare_zone.this.id
  name             = "${local.name} back office"
  domain           = var.admin_domain
  type             = "self_hosted"
  session_duration = "12h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.admin_staff[0].id
    precedence = 1
  }]
}
