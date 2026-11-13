locals {
  name = "finvest-${var.environment}"

  # Every public hostname points at the one host. Caddy routes by hostname.
  hostnames = concat(var.site_domains, [var.app_domain, var.admin_domain, var.api_domain])
}

# Every taggable resource carries the environment's name, such as finvest-production.
resource "digitalocean_tag" "environment" {
  name = local.name
}

resource "digitalocean_vpc" "main" {
  name     = local.name
  region   = var.region
  ip_range = var.vpc_ip_range
}

# Registered so DigitalOcean does not email a root password; cloud-init gives the same key to the
# deploy user. DigitalOcean refuses a second copy of a key, so a key the account already has (added
# by hand, or by the other environment) is reused. Keys are compared by type and body.
data "digitalocean_ssh_keys" "existing" {
  filter {
    key      = "public_key"
    values   = [join(" ", slice(split(" ", trimspace(var.deploy_ssh_public_key)), 0, 2))]
    match_by = "substring"
  }
}

locals {
  # This environment's own key is left out, or the next run would find it and stop managing it.
  reusable_ssh_keys = [
    for key in data.digitalocean_ssh_keys.existing.ssh_keys : key if key.name != "${local.name}-deploy"
  ]
}

resource "digitalocean_ssh_key" "deploy" {
  count      = length(local.reusable_ssh_keys) == 0 ? 1 : 0
  name       = "${local.name}-deploy"
  public_key = var.deploy_ssh_public_key
}

resource "digitalocean_droplet" "app" {
  name              = local.name
  region            = var.region
  size              = var.droplet_size
  image             = "ubuntu-24-04-x64"
  vpc_uuid          = digitalocean_vpc.main.id
  ssh_keys          = [try(local.reusable_ssh_keys[0].fingerprint, digitalocean_ssh_key.deploy[0].fingerprint)]
  ipv6              = true
  monitoring        = true
  backups           = var.droplet_backups
  graceful_shutdown = true
  tags              = [digitalocean_tag.environment.id]

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    deploy_user    = var.deploy_user
    ssh_public_key = trimspace(var.deploy_ssh_public_key)
    swap_size      = var.swap_size
  })

  lifecycle {
    # Replacing the host is a deliberate operation: remove this guard in a reviewed change first.
    prevent_destroy = true
    # cloud-init runs only on first boot; a changed key or image must not rebuild the host.
    ignore_changes = [user_data, ssh_keys, image]
  }
}

# A stable address for DNS and DEPLOY_HOST that survives rebuilding the droplet.
resource "digitalocean_reserved_ip" "app" {
  region = var.region
}

resource "digitalocean_reserved_ip_assignment" "app" {
  ip_address = digitalocean_reserved_ip.app.ip_address
  droplet_id = digitalocean_droplet.app.id
}

locals {
  cloudflare_ranges = concat(data.cloudflare_ip_ranges.this.ipv4_cidrs, data.cloudflare_ip_ranges.this.ipv6_cidrs)
}

resource "digitalocean_firewall" "app" {
  name        = local.name
  droplet_ids = [digitalocean_droplet.app.id]

  inbound_rule {
    protocol   = "tcp"
    port_range = "22"
    source_addresses = distinct(compact(concat(
      var.ssh_source_addresses,
      [var.provisioner_address],
    )))
  }

  # Web traffic only from Cloudflare, so nobody can skip the edge.
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = local.cloudflare_ranges
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = local.cloudflare_ranges
  }

  inbound_rule {
    protocol         = "icmp"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# Managed PostgreSQL with daily backups and point in time recovery, on the private network.

resource "digitalocean_database_cluster" "postgres" {
  name                 = local.name
  engine               = "pg"
  version              = var.database_version
  size                 = var.database_size
  region               = var.region
  node_count           = var.database_node_count
  private_network_uuid = digitalocean_vpc.main.id
  tags                 = [digitalocean_tag.environment.id]

  maintenance_window {
    day  = "sunday"
    hour = "01:00:00"
  }

  lifecycle {
    # Destroying the cluster deletes the data and its backups.
    prevent_destroy = true
  }
}

resource "digitalocean_database_db" "finvest" {
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "finvest"
}

# Only the host may connect. Roles are created from the host, inside the VPC, so the database is
# never opened to the internet, not even during provisioning.
resource "digitalocean_database_firewall" "postgres" {
  cluster_id = digitalocean_database_cluster.postgres.id

  rule {
    type  = "droplet"
    value = digitalocean_droplet.app.id
  }
}

data "digitalocean_database_ca" "postgres" {
  cluster_id = digitalocean_database_cluster.postgres.id
}

# Private object storage for KYC documents and statements.

resource "digitalocean_spaces_bucket" "documents" {
  name   = var.documents_bucket
  region = var.region
  acl    = "private"

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "digitalocean_spaces_bucket_cors_configuration" "documents" {
  bucket = digitalocean_spaces_bucket.documents.id
  region = var.region

  # Browsers upload documents straight to the bucket with presigned URLs.
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET"]
    allowed_origins = ["https://${var.app_domain}", "https://${var.admin_domain}"]
    max_age_seconds = 3000
  }
}

# The application's key: read, write and delete in this environment's bucket only.
resource "digitalocean_spaces_key" "documents" {
  name = "${local.name}-documents"

  grant {
    bucket     = digitalocean_spaces_bucket.documents.name
    permission = "readwrite"
  }
}

# One console project, "finvest", holds staging and production alike; the finvest-<env> tag tells
# them apart. It is created by infra/terraform/shared, so provision shared first.
data "digitalocean_project" "finvest" {
  name = var.project_name
}

resource "digitalocean_project_resources" "finvest" {
  project = data.digitalocean_project.finvest.id
  resources = [
    digitalocean_droplet.app.urn,
    digitalocean_reserved_ip.app.urn,
    digitalocean_database_cluster.postgres.urn,
    digitalocean_spaces_bucket.documents.urn,
  ]
}

# Monitoring

resource "digitalocean_uptime_check" "api" {
  count = length(var.alert_emails) > 0 ? 1 : 0

  name    = "${local.name}-api"
  target  = "https://${var.api_domain}/health/ready"
  type    = "https"
  regions = ["eu_west", "us_east"]
}

resource "digitalocean_uptime_alert" "api_down" {
  count = length(var.alert_emails) > 0 ? 1 : 0

  name       = "${local.name}-api-down"
  check_id   = digitalocean_uptime_check.api[0].id
  type       = "down"
  period     = "2m"
  comparison = "less_than"
  threshold  = 1

  notifications {
    email = var.alert_emails
  }
}

resource "digitalocean_monitor_alert" "cpu" {
  count = length(var.alert_emails) > 0 ? 1 : 0

  description = "${local.name} host CPU above 85%"
  type        = "v1/insights/droplet/cpu"
  compare     = "GreaterThan"
  value       = 85
  window      = "10m"
  entities    = [digitalocean_droplet.app.id]
  enabled     = true

  alerts {
    email = var.alert_emails
  }
}

resource "digitalocean_monitor_alert" "disk" {
  count = length(var.alert_emails) > 0 ? 1 : 0

  description = "${local.name} host disk above 80%"
  type        = "v1/insights/droplet/disk_utilization_percent"
  compare     = "GreaterThan"
  value       = 80
  window      = "10m"
  entities    = [digitalocean_droplet.app.id]
  enabled     = true

  alerts {
    email = var.alert_emails
  }
}
