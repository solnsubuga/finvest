# DNS: every hostname is proxied through Cloudflare to the reserved IP.

resource "cloudflare_dns_record" "hosts" {
  for_each = toset(local.all_hostnames)

  zone_id = data.cloudflare_zone.this.id
  name    = each.value
  type    = "A"
  content = digitalocean_reserved_ip.app.ip_address
  proxied = true
  ttl     = 1
  comment = "Managed by Terraform (${var.environment})"
}

# Zone wide settings. Only one environment manages these.

locals {
  zone_settings = var.manage_zone_settings ? {
    ssl                      = "strict"
    always_use_https         = "on"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    automatic_https_rewrites = "on"
    opportunistic_encryption = "on"
  } : {}
}

resource "cloudflare_zone_setting" "this" {
  for_each = local.zone_settings

  zone_id    = data.cloudflare_zone.this.id
  setting_id = each.key
  value      = each.value
}

# Back office behind Cloudflare Access, on top of the app's own staff sign-in.

resource "cloudflare_zero_trust_access_policy" "admin_staff" {
  count = length(var.admin_access_emails) > 0 ? 1 : 0

  account_id = var.cloudflare_account_id
  name       = "${local.name} staff"
  decision   = "allow"

  include = [for e in var.admin_access_emails : {
    email = {
      email = e
    }
  }]
}

resource "cloudflare_zero_trust_access_application" "admin" {
  count = length(var.admin_access_emails) > 0 ? 1 : 0

  zone_id          = data.cloudflare_zone.this.id
  name             = "${local.name} back office"
  domain           = var.hostnames.admin
  type             = "self_hosted"
  session_duration = "12h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.admin_staff[0].id
    precedence = 1
  }]
}
