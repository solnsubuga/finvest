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

output "project_id" {
  value = digitalocean_project.finvest.id
}
