output "deploy_host" {
  description = "Reserved IP of the application host."
  value       = digitalocean_reserved_ip.app.ip_address
}

output "database_admin_uri" {
  description = "doadmin connection on the private network. Used only by the Provision run to create roles."
  value       = digitalocean_database_cluster.postgres.private_uri
  sensitive   = true
}

output "database_role_passwords" {
  value     = { for role, password in random_password.database : role => password.result }
  sensitive = true
}

output "hostnames" {
  value = local.hostnames
}
