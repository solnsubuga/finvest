output "deploy_host" {
  description = "Reserved IP of the application host."
  value       = digitalocean_reserved_ip.app.ip_address
}

output "database_admin_uri" {
  description = "doadmin connection to the finvest database on the private network. Used only by the Provision run to create roles. The cluster's own URI names defaultdb, where handing over schema public would not reach the database the app uses."
  value = replace(
    digitalocean_database_cluster.postgres.private_uri,
    "/${digitalocean_database_cluster.postgres.database}?",
    "/${digitalocean_database_db.finvest.name}?",
  )
  sensitive = true

  precondition {
    condition     = strcontains(digitalocean_database_cluster.postgres.private_uri, "/${digitalocean_database_cluster.postgres.database}?")
    error_message = "The cluster URI does not name its default database as expected, so the roles could be set up in the wrong database."
  }
}

output "database_role_passwords" {
  value     = { for role, password in random_password.database : role => password.result }
  sensitive = true
}

output "hostnames" {
  value = local.hostnames
}
