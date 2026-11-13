module "env" {
  source = "../../modules/finvest-env"

  environment  = "staging"
  region       = "fra1"
  vpc_ip_range = "10.20.0.0/20"

  # One level below the zone so Cloudflare's free Universal SSL covers them.
  hostnames = {
    marketing = ["staging.finvestug.com"]
    app       = "app-staging.finvestug.com"
    admin     = "admin-staging.finvestug.com"
    api       = "api-staging.finvestug.com"
  }

  droplet_size    = "s-1vcpu-2gb"
  droplet_backups = false

  db_size       = "db-s-1vcpu-1gb"
  db_node_count = 1

  documents_bucket_name = "finvest-staging-documents"

  ssh_public_keys       = var.ssh_public_keys
  ssh_allowed_cidrs     = var.ssh_allowed_cidrs
  cloudflare_account_id = var.cloudflare_account_id
  admin_access_emails   = var.admin_access_emails
  alert_emails          = var.alert_emails
}
