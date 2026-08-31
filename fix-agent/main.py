import json
import os

import functions_framework
import requests
from google.cloud import storage

PROJECT_ID = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT"))
TF_STATE_BUCKET = os.environ.get("TF_STATE_BUCKET")
TF_STATE_PREFIX = os.environ.get("TF_STATE_PREFIX", "infra-drift-demo")
DEMO_BUCKET_NAME = os.environ.get("DEMO_BUCKET_NAME")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

_storage_client = storage.Client(project=PROJECT_ID)

# This function is intentionally narrow: it can only ever restore ONE field,
# on ONE specific bucket, back to what Terraform expects. It does not run
# terraform apply and cannot touch anything else in the project.


def fetch_expected_public_access_prevention():
    bucket = _storage_client.bucket(TF_STATE_BUCKET)
    blob = bucket.blob(f"{TF_STATE_PREFIX}/default.tfstate")
    state_json = json.loads(blob.download_as_text())

    for resource in state_json.get("resources", []):
        if resource.get("type") == "google_storage_bucket":
            for instance in resource.get("instances", []):
                return instance.get("attributes", {}).get("public_access_prevention")
    return None


def post_to_slack(message):
    if not SLACK_WEBHOOK_URL:
        print("No SLACK_WEBHOOK_URL configured; message:", message)
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
    except Exception as e:
        print("Failed to post to Slack:", e)


@functions_framework.http
def apply_fix(request):
    print("--- FIX REQUESTED (human-approved) ---")
    expected_value = fetch_expected_public_access_prevention()
    if not expected_value:
        return "Could not read expected value from Terraform state. No action taken.", 200

    bucket = _storage_client.get_bucket(DEMO_BUCKET_NAME)
    current_value = bucket.iam_configuration.public_access_prevention

    if current_value == expected_value:
        msg = f"No fix needed. {DEMO_BUCKET_NAME} already matches Terraform's expected value ({expected_value})."
        print(msg)
        return msg, 200

    bucket.iam_configuration.public_access_prevention = expected_value
    bucket.patch()

    message = (
        f":white_check_mark: *Fix applied* on `{DEMO_BUCKET_NAME}`\n"
        f"public_access_prevention restored from `{current_value}` to `{expected_value}` "
        f"(matching Terraform state). This was a human-approved action."
    )
    print(message)
    post_to_slack(message)
    return message, 200
