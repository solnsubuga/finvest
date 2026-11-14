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
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.1"
    }
  }

  # State lives in a private Spaces bucket created once by hand. The Provision workflow passes
  # `bucket` and `key` (finvest/<environment>.tfstate) with -backend-config, and the Spaces key as
  # AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY. The workflow runs one job per environment at a time.
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

# Reads DIGITALOCEAN_TOKEN (from the TERRAFORM_DIGITALOCEAN_TOKEN secret), SPACES_ACCESS_KEY_ID and SPACES_SECRET_ACCESS_KEY from the environment.
provider "digitalocean" {}

# Reads CLOUDFLARE_API_TOKEN from the environment.
provider "cloudflare" {}

# Reads GITHUB_TOKEN from the environment: a fine-grained token for this repository that may read
# (plan) or write (apply) environments, variables and secrets.
provider "github" {
  owner = var.github_owner
}
