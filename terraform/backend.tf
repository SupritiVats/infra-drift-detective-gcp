terraform {
  backend "gcs" {
    bucket = "<YOUR_PROJECT_ID>-tf-state"
    prefix = "infra-drift-demo"
  }
}
