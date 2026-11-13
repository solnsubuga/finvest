# Resources that staging and production share. Provision this root first.

terraform {
  required_version = ">= 1.10, < 2.0.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.107"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.29"
    }
  }

  # Same bucket as the environments, key finvest/shared.tfstate.
  backend "s3" {
    endpoints                   = { s3 = "https://lon1.digitaloceanspaces.com" }
    region                      = "us-east-1"
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}

provider "digitalocean" {}

provider "cloudflare" {}

variable "zone_name" {
  description = "Cloudflare zone for every Finvest hostname."
  type        = string
  default     = "finvestug.com"
}

# The console project that every environment adds its resources to.
resource "digitalocean_project" "finvest" {
  name        = "finvest"
  description = "Finvest staging and production. Tags finvest-staging and finvest-production tell them apart."
  purpose     = "Web Application"

  lifecycle {
    prevent_destroy = true
  }
}

data "cloudflare_zone" "this" {
  filter = {
    name = var.zone_name
  }
}

# Zone wide settings, shared by every environment's hostnames.
locals {
  zone_settings = {
    ssl                      = "strict"
    always_use_https         = "on"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    automatic_https_rewrites = "on"
  }
}

resource "cloudflare_zone_setting" "this" {
  for_each = local.zone_settings

  zone_id    = data.cloudflare_zone.this.id
  setting_id = each.key
  value      = each.value
}

# A flood guard on the API's own paths, for both environments. The zone is on the Free plan, which
# allows one rate limiting rule per zone, matching on the path only, counted per visitor address
# over 10 seconds. It is a zone entrypoint, so it lives here rather than in each environment.
# Sign-in steps have their own tighter limits in PostgreSQL; this only stops a flood before it
# reaches the server. Many people in Uganda share one carrier address (CGNAT), so the limit is
# far above what any one person sends. Payment provider callbacks are never limited.
variable "api_flood_requests_per_10s" {
  description = "Requests one address may send to the API's client, staff and public paths in 10 seconds before Cloudflare blocks it for 10 seconds."
  type        = number
  default     = 400

  validation {
    condition     = var.api_flood_requests_per_10s >= 100
    error_message = "Keep the flood guard at 100 or more: carrier addresses are shared by many people."
  }
}

resource "cloudflare_ruleset" "rate_limit" {
  zone_id     = data.cloudflare_zone.this.id
  name        = "Finvest rate limits"
  description = "Managed by Terraform (shared)"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules = [{
    ref         = "api_flood_guard"
    description = "API flood guard: client, staff and public paths"
    enabled     = true
    action      = "block"
    expression  = "(http.request.uri.path wildcard \"/client/*\") or (http.request.uri.path wildcard \"/staff/*\") or (http.request.uri.path wildcard \"/public/*\")"
    ratelimit = {
      characteristics     = ["ip.src", "cf.colo.id"]
      period              = 10
      requests_per_period = var.api_flood_requests_per_10s
      mitigation_timeout  = 10
    }
  }]
}

output "project_id" {
  value = digitalocean_project.finvest.id
}
