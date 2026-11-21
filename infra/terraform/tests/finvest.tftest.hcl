# terraform test, with no cloud access: the rules this configuration must keep.
mock_provider "digitalocean" {}
mock_provider "github" {}
mock_provider "random" {}
mock_provider "tls" {}

mock_provider "cloudflare" {
  mock_data "cloudflare_ip_ranges" {
    defaults = {
      ipv4_cidrs = ["173.245.48.0/20"]
      ipv6_cidrs = ["2400:cb00::/32"]
    }
  }
  mock_data "cloudflare_zone" {
    defaults = {
      account = { id = "account" }
    }
  }
}

variables {
  github_owner          = "owner"
  github_repository     = "finvest"
  deploy_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEx deploy"
  ssh_source_addresses  = ["203.0.113.1/32"]
  vpc_ip_range          = "10.20.0.0/20"
  site_domains          = ["staging.finvestug.com"]
  app_domain            = "app-staging.finvestug.com"
  admin_domain          = "admin-staging.finvestug.com"
  api_domain            = "api-staging.finvestug.com"
  droplet_size          = "s-1vcpu-2gb"
  database_size         = "db-s-1vcpu-1gb"
  documents_bucket      = "finvest-staging-documents"
}

run "web_traffic_only_from_cloudflare" {
  command = plan
  variables {
    environment = "staging"
  }
  assert {
    condition = alltrue([
      for rule in digitalocean_firewall.app.inbound_rule :
      toset(rule.source_addresses) == toset(["173.245.48.0/20", "2400:cb00::/32"])
      if contains(["80", "443"], coalesce(rule.port_range, "-"))
    ])
    error_message = "Ports 80 and 443 must accept only Cloudflare's ranges."
  }
}

run "database_only_from_the_host" {
  command = plan
  variables {
    environment = "staging"
  }
  assert {
    condition     = length(digitalocean_database_firewall.postgres.rule) == 1 && one(digitalocean_database_firewall.postgres.rule).type == "droplet"
    error_message = "Only the host may reach the database."
  }
}

run "provisioner_only_reaches_ssh" {
  command = plan
  variables {
    environment         = "staging"
    provisioner_address = "198.51.100.7"
  }
  assert {
    condition = alltrue([
      for rule in digitalocean_firewall.app.inbound_rule :
      contains(rule.source_addresses, "198.51.100.7") == (coalesce(rule.port_range, "-") == "22")
    ])
    error_message = "The Provision run's address may reach SSH and nothing else."
  }
}

run "deploy_settings_reach_github" {
  command = plan
  variables {
    environment = "staging"
  }
  assert {
    condition     = length(github_actions_environment_secret.deploy) == 8 && alltrue([for s in github_actions_environment_secret.deploy : s.environment == "dev"])
    error_message = "Every deploy secret is written to the environment's own GitHub environment."
  }
}

run "production_requires_access" {
  command = plan
  variables {
    environment = "production"
  }
  expect_failures = [var.admin_access_emails]
}

run "production_requires_access_until_the_back_office_is_on_the_tailnet" {
  command = plan
  variables {
    environment       = "production"
    client_app_public = true
  }
  expect_failures = [var.admin_access_emails]
}

run "production_with_access" {
  command = plan
  variables {
    environment         = "production"
    admin_access_emails = ["staff@example.com"]
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.admin) == 1 && length(cloudflare_zero_trust_access_application.app) == 1
    error_message = "Before the tailnet and before launch, Access guards the back office and the client app."
  }
  assert {
    condition     = cloudflare_dns_record.hosts[var.admin_domain].proxied && !contains(keys(github_actions_environment_variable.deploy), "ADMIN_ON_TAILNET")
    error_message = "Off the tailnet, the back office is proxied through Cloudflare."
  }
  assert {
    condition     = length(cloudflare_zero_trust_list.staff_emails) == 1 && cloudflare_zero_trust_list.staff_emails[0].type == "EMAIL"
    error_message = "Production has the console's staff email list."
  }
  assert {
    condition     = length(local.staff_include) == 2
    error_message = "Access lets in the break-glass emails and the console's staff list."
  }
  assert {
    condition     = contains(keys(github_actions_environment_variable.deploy), "FINVEST_CLOUDFLARE_STAFF_LIST_ID")
    error_message = "The worker is told which list to keep in step."
  }
  assert {
    condition     = cloudflare_zero_trust_access_policy.admin_staff[0].account_id == "account"
    error_message = "Access uses the zone's own Cloudflare account."
  }
  assert {
    condition     = alltrue([for s in github_actions_environment_secret.deploy : s.environment == "prod"])
    error_message = "Production settings go to the prod GitHub environment."
  }
}

run "back_office_on_the_tailnet" {
  command = plan
  variables {
    environment           = "production"
    client_app_public     = true
    admin_tailnet_address = "100.101.102.103"
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.admin) == 0 && length(cloudflare_zero_trust_access_application.app) == 0
    error_message = "On the tailnet and after launch, nothing is behind Access."
  }
  assert {
    condition     = cloudflare_dns_record.hosts[var.admin_domain].content == "100.101.102.103" && !cloudflare_dns_record.hosts[var.admin_domain].proxied
    error_message = "The back office name points at the tailnet address, outside Cloudflare."
  }
  assert {
    condition     = cloudflare_dns_record.hosts[var.api_domain].proxied && cloudflare_dns_record.hosts[var.app_domain].proxied
    error_message = "Every other name stays proxied through Cloudflare."
  }
  assert {
    condition     = github_actions_environment_variable.deploy["ADMIN_ON_TAILNET"].value == "true"
    error_message = "The deploy is told to serve the back office only to the tailnet."
  }
}

run "tailnet_address_must_be_tailscale" {
  command = plan
  variables {
    environment           = "production"
    admin_access_emails   = ["staff@example.com"]
    admin_tailnet_address = "10.0.0.5"
  }
  expect_failures = [var.admin_tailnet_address]
}

run "no_ssh_rule_without_ssh_addresses" {
  command = plan
  variables {
    environment          = "staging"
    ssh_source_addresses = []
  }
  assert {
    condition     = length([for rule in digitalocean_firewall.app.inbound_rule : rule if coalesce(rule.port_range, "-") == "22"]) == 0
    error_message = "With no SSH addresses and no Provision run, nothing may reach SSH."
  }
}

run "roles_are_set_up_in_the_app_database" {
  command = plan
  variables {
    environment = "staging"
  }
  override_resource {
    target          = digitalocean_database_cluster.postgres
    override_during = plan
    values = {
      database    = "defaultdb"
      private_uri = "postgresql://doadmin:secret@private-db.example:25060/defaultdb?sslmode=require"
    }
  }
  assert {
    condition     = nonsensitive(output.database_admin_uri) == "postgresql://doadmin:secret@private-db.example:25060/finvest?sslmode=require"
    error_message = "Provision must create the roles and hand over schema public in the finvest database, not defaultdb."
  }
}

# Cloudflare returns the origin certificate's names sorted. Any other order plans a replacement,
# which revokes the certificate the edge is serving.
run "origin_certificate_names_are_sorted" {
  command = plan
  variables {
    environment = "staging"
  }
  assert {
    condition     = cloudflare_origin_ca_certificate.this.hostnames == sort(cloudflare_origin_ca_certificate.this.hostnames)
    error_message = "The origin certificate's hostnames must be sorted."
  }
}
