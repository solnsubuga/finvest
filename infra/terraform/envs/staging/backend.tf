# State lives in a private DigitalOcean Spaces bucket, created once by hand (see infra/README.md).
# Credentials come from AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, set to a Spaces access key.
# GitHub Actions runs one job per environment at a time, which stands in for state locking.
terraform {
  backend "s3" {
    bucket = "finvest-tfstate"
    key    = "staging/terraform.tfstate"
    region = "us-east-1"

    endpoints = {
      s3 = "https://fra1.digitaloceanspaces.com"
    }

    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_s3_checksum            = true
  }
}
