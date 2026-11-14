# Writes the settings a deploy needs into this environment's GitHub environment, so nothing is
# copied by hand. Terraform owns these names: change them here, not in GitHub.

locals {
  # The GitHub environments are named prod and dev.
  github_environment = var.environment == "production" ? "prod" : "dev"

  github_variables = {
    SITE_DOMAINS = join(",", var.site_domains)
    APP_DOMAIN   = var.app_domain
    ADMIN_DOMAIN = var.admin_domain
    API_DOMAIN   = var.api_domain
    DEPLOY_HOST  = digitalocean_reserved_ip.app.ip_address
    DEPLOY_USER  = var.deploy_user
    # The deploy run admits its own address to SSH on this firewall, then removes it.
    DEPLOY_FIREWALL_ID              = digitalocean_firewall.app.id
    DATABASE_HOST                   = digitalocean_database_cluster.postgres.private_host
    DATABASE_PORT                   = tostring(digitalocean_database_cluster.postgres.port)
    DATABASE_NAME                   = digitalocean_database_db.finvest.name
    FINVEST_OBJECT_STORAGE_BUCKET   = digitalocean_spaces_bucket.documents.name
    FINVEST_OBJECT_STORAGE_ENDPOINT = "https://${var.region}.digitaloceanspaces.com"
    FINVEST_OBJECT_STORAGE_REGION   = var.region
  }

  # Keys are fixed so for_each is known at plan time; only the values are secret.
  github_secret_names = toset([
    "DATABASE_CA_CERT",
    "DATABASE_MIGRATOR_PASSWORD",
    "DATABASE_APP_PASSWORD",
    "DATABASE_WORKER_PASSWORD",
    "FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID",
    "FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY",
    "ORIGIN_TLS_CERT",
    "ORIGIN_TLS_KEY",
  ])

  github_secrets = {
    DATABASE_CA_CERT                         = data.digitalocean_database_ca.postgres.certificate
    DATABASE_MIGRATOR_PASSWORD               = random_password.database["migrator"].result
    DATABASE_APP_PASSWORD                    = random_password.database["app"].result
    DATABASE_WORKER_PASSWORD                 = random_password.database["worker"].result
    FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID     = digitalocean_spaces_key.documents.access_key
    FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY = digitalocean_spaces_key.documents.secret_key
    ORIGIN_TLS_CERT                          = cloudflare_origin_ca_certificate.this.certificate
    ORIGIN_TLS_KEY                           = tls_private_key.origin.private_key_pem
  }
}

# Passwords of finvest_migrator, finvest_app and finvest_worker. The Provision run creates the roles
# with them through backend/db/init-roles.sql, which never changes an existing role's password.
resource "random_password" "database" {
  for_each = toset(["migrator", "app", "worker"])
  length   = 40
  special  = false
}

resource "github_actions_environment_variable" "deploy" {
  for_each      = local.github_variables
  repository    = var.github_repository
  environment   = local.github_environment
  variable_name = each.key
  value         = each.value
}

resource "github_actions_environment_secret" "deploy" {
  for_each        = local.github_secret_names
  repository      = var.github_repository
  environment     = local.github_environment
  secret_name     = each.key
  plaintext_value = local.github_secrets[each.key]
}
