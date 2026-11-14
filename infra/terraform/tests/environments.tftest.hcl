# Run with -var-file=environments/<env>.tfvars (see .github/workflows/terraform-checks.yml): each
# committed environment file must plan. Only values the files do not set are given here.
mock_provider "digitalocean" {}
mock_provider "github" {}
mock_provider "random" {}
mock_provider "tls" {}
mock_provider "cloudflare" {}

variables {
  github_owner          = "owner"
  github_repository     = "finvest"
  deploy_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEx deploy"
  ssh_source_addresses  = ["203.0.113.1/32"]
  admin_access_emails   = ["staff@example.com"]
}

run "environment_file_plans" {
  command = plan
  assert {
    condition     = digitalocean_droplet.app.name == "finvest-${var.environment}"
    error_message = "The environment file must plan its own environment."
  }
}
