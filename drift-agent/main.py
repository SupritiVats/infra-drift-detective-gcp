import json
import os

import functions_framework
import requests
import vertexai
from google.cloud import storage
from vertexai.generative_models import GenerativeModel

PROJECT_ID = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT"))
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")
TF_STATE_BUCKET = os.environ.get("TF_STATE_BUCKET")
TF_STATE_PREFIX = os.environ.get("TF_STATE_PREFIX", "infra-drift-demo")
DEMO_BUCKET_NAME = os.environ.get("DEMO_BUCKET_NAME")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

vertexai.init(project=PROJECT_ID, location=VERTEX_LOCATION)
_storage_client = storage.Client(project=PROJECT_ID)

# Fields we care about comparing between Terraform's expected state and live reality.
WATCHED_FIELDS = ["public_access_prevention", "uniform_bucket_level_access"]


def fetch_terraform_state():
    """Reads the Terraform state file directly from the GCS backend bucket, no terraform binary needed."""
    bucket = _storage_client.bucket(TF_STATE_BUCKET)
    blob = bucket.blob(f"{TF_STATE_PREFIX}/default.tfstate")
    state_json = json.loads(blob.download_as_text())

    for resource in state_json.get("resources", []):
        if resource.get("type") == "google_storage_bucket":
            for instance in resource.get("instances", []):
                attrs = instance.get("attributes", {})
                return {
                    "public_access_prevention": attrs.get("public_access_prevention"),
                    "uniform_bucket_level_access": attrs.get("uniform_bucket_level_access"),
                }
    return {}


def fetch_live_bucket_config():
    """Reads the bucket's actual live configuration via the Storage API."""
    bucket = _storage_client.get_bucket(DEMO_BUCKET_NAME)
    return {
        "public_access_prevention": bucket.iam_configuration.public_access_prevention,
        "uniform_bucket_level_access": bucket.iam_configuration.uniform_bucket_level_access_enabled,
    }


def detect_drift(expected, live):
    drift = []
    for field in WATCHED_FIELDS:
        expected_value = expected.get(field)
        live_value = live.get(field)
        if expected_value is not None and str(expected_value) != str(live_value):
            drift.append({
                "field": field,
                "expected": expected_value,
                "actual": live_value,
            })
    return drift


def explain_drift(drift):
    model = GenerativeModel("gemini-2.5-flash")
    prompt = (
        f"The following infrastructure drift was detected on a GCS bucket. "
        f"Terraform expects certain settings, but the live resource does not match.\n\n"
        f"Drift found: {json.dumps(drift)}\n\n"
        "Write a short, plain-English Slack message: what changed, why it is risky "
        "(especially if it relates to public access), and what the recommended fix is. "
        "Do not say you are applying any fix yourself — a human must approve any change."
    )
    resp = model.generate_content(prompt)
    return resp.text


def post_to_slack(message):
    if not SLACK_WEBHOOK_URL:
        print("No SLACK_WEBHOOK_URL configured; message:", message)
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
    except Exception as e:
        print("Failed to post to Slack:", e)


@functions_framework.http
def check_drift(request):
    print("--- DRIFT CHECK STARTED ---")
    expected = fetch_terraform_state()
    if not expected:
        msg = "Could not find the demo bucket resource in Terraform state."
        print(msg)
        return msg, 200

    live = fetch_live_bucket_config()
    drift = detect_drift(expected, live)

    if not drift:
        msg = f"No drift detected on {DEMO_BUCKET_NAME}. Live config matches Terraform state."
        print(msg)
        return msg, 200

    print("Drift found:", drift)
    explanation = explain_drift(drift)
    message = f":rotating_light: *Infrastructure drift detected* on `{DEMO_BUCKET_NAME}`\n\n{explanation}"
    post_to_slack(message)
    return f"Drift detected: {json.dumps(drift)}", 200
