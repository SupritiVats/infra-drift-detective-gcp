variable "project_id" {
  description = "GCP project ID where the demo resource will be created"
  type        = string
}

variable "region" {
  description = "GCP region for the demo resource"
  type        = string
  default     = "asia-south1"
}

variable "bucket_name_suffix" {
  description = "Suffix appended to the project ID to form the demo bucket name"
  type        = string
  default     = "drift-demo"
}
