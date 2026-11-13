variable "environment" {
  description = "Environment name, either staging or production."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "region" {
  description = "DigitalOcean region for every resource in this environment. Confirm against Uganda's Data Protection and Privacy Act before production."
  type        = string
  default     = "fra1"
}

variable "vpc_ip_range" {
  description = "Private network range for the environment's VPC. Must not overlap another VPC in the account."
  type        = string
}

variable "zone_name" {
  description = "Cloudflare zone that holds every hostname."
  type        = string
  default     = "finvestug.com"
}

variable "cloudflare_account_id" {
  description = "Cloudflare account id. Needed only when admin_access_emails is set."
  type        = string
  default     = ""
}

variable "hostnames" {
  description = "Public hostnames for each surface, all inside zone_name."
  type = object({
    marketing = list(string)
    app       = string
    admin     = string
    api       = string
  })
}

variable "manage_zone_settings" {
  description = "Set true in exactly one environment, because Cloudflare zone settings are shared by the whole zone."
  type        = bool
  default     = false
}

variable "droplet_size" {
  description = "Size slug for the application host."
  type        = string
}

variable "droplet_backups" {
  description = "Turn on DigitalOcean weekly droplet backups. The host is rebuildable, so this is a convenience, not the data backup."
  type        = bool
  default     = false
}

variable "ssh_public_keys" {
  description = "Map of name to SSH public key allowed to log in as the deploy user."
  type        = map(string)
}

variable "ssh_allowed_cidrs" {
  description = "Source ranges allowed to reach SSH on the host. Keep this narrow."
  type        = list(string)
}

variable "db_size" {
  description = "Size slug for the managed PostgreSQL cluster."
  type        = string
}

variable "db_node_count" {
  description = "Number of PostgreSQL nodes. 2 or more adds a standby for failover."
  type        = number
  default     = 1
}

variable "db_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "17"
}

variable "documents_bucket_name" {
  description = "Globally unique name of the private Spaces bucket for KYC documents and statements."
  type        = string
}

variable "admin_access_emails" {
  description = "Staff email addresses allowed through Cloudflare Access to the back office. Empty leaves Access off."
  type        = list(string)
  default     = []
}

variable "alert_emails" {
  description = "Addresses that receive uptime and resource alerts."
  type        = list(string)
  default     = []
}
