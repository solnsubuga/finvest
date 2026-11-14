variable "environment" {
  description = "Environment name, which is also the name of its GitHub environment."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  description = "DigitalOcean region. London, as the owner decided (docs/decisions.md)."
  type        = string
  default     = "lon1"
}

variable "vpc_ip_range" {
  description = "Private network range for the environment's VPC. Must not overlap another VPC in the account."
  type        = string
}

variable "project_name" {
  description = "DigitalOcean project that holds every environment's resources. Created by infra/terraform/shared."
  type        = string
  default     = "finvest"
}

variable "zone_name" {
  description = "Cloudflare zone that holds every hostname."
  type        = string
  default     = "finvestug.com"
}

variable "site_domains" {
  description = "Hostnames of the public website. The first is canonical; Caddy redirects the others to it."
  type        = list(string)
}

variable "app_domain" {
  description = "Hostname of the client web app."
  type        = string
}

variable "admin_domain" {
  description = "Hostname of the back office console."
  type        = string
}

variable "api_domain" {
  description = "Hostname of the backend API."
  type        = string
}

variable "droplet_size" {
  description = "Size slug for the application host."
  type        = string
}

variable "droplet_backups" {
  description = "Weekly droplet backups. The host is rebuildable; the database has its own backups."
  type        = bool
  default     = false
}

variable "swap_size" {
  description = "Swap file size on the host, as cloud-init takes it."
  type        = string
  default     = "2G"
}

variable "deploy_user" {
  description = "Account that deployments use on the host."
  type        = string
  default     = "deploy"
}

variable "deploy_ssh_public_key" {
  description = "Public half of the environment's DEPLOY_SSH_KEY. Installed for the deploy user at first boot."
  type        = string
}

variable "ssh_source_addresses" {
  description = "Addresses allowed to reach SSH. Optional: empty means only the Provision or deploy run, while it works."
  type        = list(string)
  default     = []
}

variable "provisioner_address" {
  description = "Set only by the Provision run's host step, to admit that runner to SSH for a few minutes. Empty in every plan."
  type        = string
  default     = ""
}

variable "database_size" {
  description = "Size slug for the managed PostgreSQL cluster."
  type        = string
}

variable "database_node_count" {
  description = "PostgreSQL nodes. 2 or more adds a standby for failover."
  type        = number
  default     = 1
}

variable "database_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "17"
}

variable "documents_bucket" {
  description = "Globally unique name of the private Spaces bucket for KYC documents and statements."
  type        = string
}

variable "admin_access_emails" {
  description = "Staff emails allowed through Cloudflare Access to the back office. Required in production."
  type        = list(string)
  default     = []

  validation {
    condition     = var.environment != "production" || length(var.admin_access_emails) > 0
    error_message = "Production needs at least one admin_access_emails entry, so the back office is never public."
  }
}

variable "client_app_public" {
  description = "Production only. false keeps the client app host behind Cloudflare Access for staff (admin_access_emails) until switch day (docs/launch.md). The API host is never behind Access."
  type        = bool
  default     = false
}

variable "alert_emails" {
  description = "Addresses that receive uptime and resource alerts."
  type        = list(string)
  default     = []
}

variable "github_owner" {
  description = "Owner of the GitHub repository whose environment receives the deployment settings."
  type        = string
}

variable "github_repository" {
  description = "Name of the GitHub repository whose environment receives the deployment settings."
  type        = string
}

variable "admin_office_networks" {
  description = "Office addresses (CIDR) allowed into the back office through Cloudflare Access. Empty: any address, with a staff email."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for n in var.admin_office_networks : can(cidrhost(n, 0))])
    error_message = "admin_office_networks entries must be CIDR ranges, such as 203.0.113.10/32."
  }
}
