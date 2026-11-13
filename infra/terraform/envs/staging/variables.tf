variable "ssh_public_keys" {
  description = "Map of name to SSH public key for the deploy user."
  type        = map(string)
}

variable "ssh_allowed_cidrs" {
  description = "Source ranges allowed to reach SSH."
  type        = list(string)
}

variable "cloudflare_account_id" {
  description = "Cloudflare account id, needed for Cloudflare Access on the back office."
  type        = string
  default     = ""
}

variable "admin_access_emails" {
  description = "Staff emails allowed through Cloudflare Access to the back office."
  type        = list(string)
  default     = []
}

variable "alert_emails" {
  description = "Addresses that receive uptime and resource alerts."
  type        = list(string)
  default     = []
}
