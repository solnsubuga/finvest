environment  = "production"
region       = "lon1"
vpc_ip_range = "10.10.0.0/20"

site_domains = ["finvestug.com", "www.finvestug.com"]
app_domain   = "app.finvestug.com"
admin_domain = "admin.finvestug.com"
# Still an open question for the owner in docs/decisions.md.
api_domain = "api.finvestug.com"

droplet_size    = "s-2vcpu-4gb"
droplet_backups = true
database_size   = "db-s-1vcpu-2gb"

documents_bucket = "finvest-production-documents"
