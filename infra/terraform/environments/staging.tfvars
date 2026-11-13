environment  = "staging"
region       = "lon1"
vpc_ip_range = "10.20.0.0/20"

# One level below the zone so Cloudflare's free Universal SSL covers them.
site_domains = ["staging.finvestug.com"]
app_domain   = "app-staging.finvestug.com"
admin_domain = "admin-staging.finvestug.com"
api_domain   = "api-staging.finvestug.com"

droplet_size    = "s-1vcpu-2gb"
droplet_backups = false
database_size   = "db-s-1vcpu-1gb"

documents_bucket = "finvest-staging-documents"
