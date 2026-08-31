output "demo_bucket_name" {
  description = "Name of the demo bucket, used as DEMO_BUCKET_NAME when deploying the agents"
  value       = google_storage_bucket.demo_bucket.name
}

output "demo_bucket_url" {
  description = "GCS URL of the demo bucket"
  value       = google_storage_bucket.demo_bucket.url
}

output "demo_bucket_public_access_prevention" {
  description = "The public_access_prevention value Terraform expects this bucket to have"
  value       = google_storage_bucket.demo_bucket.public_access_prevention
}
