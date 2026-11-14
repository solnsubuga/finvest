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

run "production_with_access" {
  command = plan
  variables {
    environment         = "production"
    admin_access_emails = ["staff@example.com"]
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.admin) == 1
    error_message = "Production puts the back office behind Cloudflare Access."
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

run "back_office_open_from_anywhere_without_office_networks" {
  command = plan
  variables {
    environment         = "production"
    admin_access_emails = ["staff@example.com"]
  }
  assert {
    condition     = length(cloudflare_zero_trust_list.office_networks) == 0 && length(cloudflare_zero_trust_access_policy.admin_office) == 0
    error_message = "With no office networks the back office keeps the email-only policy."
  }
}

run "back_office_only_from_office_networks" {
  command = plan
  variables {
    environment           = "production"
    admin_access_emails   = ["staff@example.com"]
    admin_office_networks = ["203.0.113.0/24", "198.51.100.7/32"]
  }
  assert {
    condition     = toset([for i in cloudflare_zero_trust_list.office_networks[0].items : i.value]) == toset(["203.0.113.0/24", "198.51.100.7/32"])
    error_message = "The office list holds exactly the configured networks."
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_policy.admin_office[0].require) == 1
    error_message = "The office policy requires the office list."
  }
  assert {
    condition     = length(cloudflare_zero_trust_access_application.app) == 1
    error_message = "The client app keeps the email-only staff policy before launch."
  }
}

run "office_networks_must_be_cidr" {
  command = plan
  variables {
    environment           = "production"
    admin_access_emails   = ["staff@example.com"]
    admin_office_networks = ["office"]
  }
  expect_failures = [var.admin_office_networks]
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
