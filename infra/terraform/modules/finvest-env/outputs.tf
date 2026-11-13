output "reserved_ip" {
  description = "Public address of the application host."
  value       = digitalocean_reserved_ip.app.ip_address
}

output "droplet_id" {
  value = digitalocean_droplet.app.id
}

output "database_private_host" {
  description = "Private hostname for PostgreSQL, reachable only inside the VPC."
  value       = digitalocean_database_cluster.postgres.private_host
}

output "database_port" {
  value = digitalocean_database_cluster.postgres.port
}

output "database_name" {
  value = digitalocean_database_db.finvest.name
}

output "documents_bucket" {
  value = digitalocean_spaces_bucket.documents.name
}

output "documents_bucket_endpoint" {
  value = "https://${var.region}.digitaloceanspaces.com"
}

output "hostnames" {
  value = var.hostnames
}
