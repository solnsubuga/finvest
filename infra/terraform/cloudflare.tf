data "cloudflare_zone" "this" {
  filter = {
    name = var.zone_name
  }
}

data "cloudflare_ip_ranges" "this" {}

# Every hostname is proxied through Cloudflare to the reserved IP.
resource "cloudflare_dns_record" "hosts" {
  for_each = toset(local.hostnames)

  zone_id = data.cloudflare_zone.this.id
  name    = each.value
  type    = "A"
  content = digitalocean_reserved_ip.app.ip_address
  proxied = true
  ttl     = 1
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

# Back office behind Cloudflare Access, on top of the console's own password and passkey sign in.

resource "cloudflare_zero_trust_access_policy" "admin_staff" {
  count = length(var.admin_access_emails) > 0 ? 1 : 0

  account_id = data.cloudflare_zone.this.account.id
  name       = "${local.name} staff"
  decision   = "allow"

  include = [for e in var.admin_access_emails : {
    email = {
      email = e
    }
  }]
}

# Until switch day the production client app is for staff only, like the back office. The API
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
  count = length(var.admin_access_emails) > 0 ? 1 : 0

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
