module "env" {
  source = "../../modules/finvest-env"

  environment  = "production"
  region       = "fra1"
  vpc_ip_range = "10.10.0.0/20"

  hostnames = {
    marketing = ["finvestug.com", "www.finvestug.com"]
    app       = "app.finvestug.com"
    admin     = "admin.finvestug.com"
    api       = "api.finvestug.com"
  }

  # Production owns the zone wide Cloudflare settings.
  manage_zone_settings = true

  droplet_size    = "s-2vcpu-4gb"
  droplet_backups = true

  db_size       = "db-s-1vcpu-2gb"
  db_node_count = 1

  documents_bucket_name = "finvest-production-documents"

  ssh_public_keys       = var.ssh_public_keys
  ssh_allowed_cidrs     = var.ssh_allowed_cidrs
  cloudflare_account_id = var.cloudflare_account_id
  admin_access_emails   = var.admin_access_emails
  alert_emails          = var.alert_emails
}
