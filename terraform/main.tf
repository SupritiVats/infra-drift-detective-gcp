resource "google_storage_bucket" "demo_bucket" {
  name                        = "${var.project_id}-${var.bucket_name_suffix}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  labels = {
    managed-by = "terraform"
    purpose    = "drift-demo"
  }
}
