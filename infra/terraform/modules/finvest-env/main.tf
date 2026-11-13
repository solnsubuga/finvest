locals {
  name = "finvest-${var.environment}"
  tags = ["finvest", var.environment]

  # Every public hostname points at the one host. Caddy routes by hostname.
  all_hostnames = concat(var.hostnames.marketing, [var.hostnames.app, var.hostnames.admin, var.hostnames.api])
}

data "cloudflare_zone" "this" {
  filter = {
    name = var.zone_name
  }
}

data "cloudflare_ip_ranges" "this" {}

# Network

resource "digitalocean_vpc" "this" {
  name     = local.name
  region   = var.region
  ip_range = var.vpc_ip_range
}

# Application host. Runs the Docker Compose stack: Caddy, API, worker and static frontends.

resource "digitalocean_ssh_key" "this" {
  for_each = var.ssh_public_keys

  name       = "${local.name}-${each.key}"
  public_key = each.value
}

resource "digitalocean_droplet" "app" {
  name       = "${local.name}-app"
  image      = "ubuntu-24-04-x64"
  size       = var.droplet_size
  region     = var.region
  vpc_uuid   = digitalocean_vpc.this.id
  ssh_keys   = [for k in digitalocean_ssh_key.this : k.fingerprint]
  monitoring = true
  backups    = var.droplet_backups
  ipv6       = true
  tags       = local.tags

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    ssh_public_keys = values(var.ssh_public_keys)
  })

  lifecycle {
    # Changing cloud-init would rebuild the host. Host changes after first boot go through deploys.
    ignore_changes = [user_data, ssh_keys]
  }
}

resource "digitalocean_reserved_ip" "app" {
  region = var.region
}

resource "digitalocean_reserved_ip_assignment" "app" {
  ip_address = digitalocean_reserved_ip.app.ip_address
  droplet_id = digitalocean_droplet.app.id
}

resource "digitalocean_firewall" "app" {
  name        = "${local.name}-app"
  droplet_ids = [digitalocean_droplet.app.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_allowed_cidrs
  }

  # Web traffic only from Cloudflare, so nobody can skip the edge.
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = concat(data.cloudflare_ip_ranges.this.ipv4_cidrs, data.cloudflare_ip_ranges.this.ipv6_cidrs)
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = concat(data.cloudflare_ip_ranges.this.ipv4_cidrs, data.cloudflare_ip_ranges.this.ipv6_cidrs)
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

# Managed PostgreSQL with daily backups and point in time recovery, reachable only from the host.

resource "digitalocean_database_cluster" "postgres" {
  name                 = "${local.name}-pg"
  engine               = "pg"
  version              = var.db_version
  size                 = var.db_size
  region               = var.region
  node_count           = var.db_node_count
  private_network_uuid = digitalocean_vpc.this.id
  tags                 = local.tags

  maintenance_window {
    day  = "sunday"
    hour = "01:00:00"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "digitalocean_database_db" "finvest" {
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "finvest"
}

resource "digitalocean_database_firewall" "postgres" {
  cluster_id = digitalocean_database_cluster.postgres.id

  rule {
    type  = "droplet"
    value = digitalocean_droplet.app.id
  }
}

# Private object storage for KYC documents and statements.

resource "digitalocean_spaces_bucket" "documents" {
  name   = var.documents_bucket_name
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
    allowed_origins = ["https://${var.hostnames.app}", "https://${var.hostnames.admin}"]
    max_age_seconds = 3000
  }
}

resource "digitalocean_project" "this" {
  name        = local.name
  description = "Finvest ${var.environment}"
  purpose     = "Web Application"
  environment = var.environment == "production" ? "Production" : "Staging"
  resources = [
    digitalocean_droplet.app.urn,
    digitalocean_database_cluster.postgres.urn,
    digitalocean_spaces_bucket.documents.urn,
    digitalocean_reserved_ip.app.urn,
  ]
}

# Monitoring

resource "digitalocean_uptime_check" "api" {
  count = length(var.alert_emails) > 0 ? 1 : 0

  name    = "${local.name}-api"
  target  = "https://${var.hostnames.api}/health/ready"
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
