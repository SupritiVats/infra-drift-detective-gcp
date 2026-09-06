import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs

import functions_framework
import requests
from google.cloud import storage

PROJECT_ID = os.environ.get("GCP_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT"))
TF_STATE_BUCKET = os.environ.get("TF_STATE_BUCKET")
TF_STATE_PREFIX = os.environ.get("TF_STATE_PREFIX", "infra-drift-demo")
DEMO_BUCKET_NAME = os.environ.get("DEMO_BUCKET_NAME")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

_storage_client = storage.Client(project=PROJECT_ID)

# This function is intentionally narrow: it can only ever restore ONE field,
# on ONE specific bucket, back to what Terraform expects. It does not run
# terraform apply and cannot touch anything else in the project.
#
# This function is called directly by Slack (when someone clicks "Approve Fix"),
# not by a GCP-authenticated caller. Because of that, it is deployed with
# --allow-unauthenticated, and instead verifies every request cryptographically
# using Slack's own signing secret. This is the standard way any Slack app with
# interactive buttons works.


def verify_slack_signature(request):
    """Confirms this request genuinely came from Slack, not just anyone who found the URL."""
    if not SLACK_SIGNING_SECRET:
        print("SLACK_SIGNING_SECRET not configured; refusing request.")
        return False

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            print("Request timestamp too old, possible replay attack.")
            return False
    except ValueError:
        return False

    body = request.get_data(as_text=True)
    sig_basestring = f"v0:{timestamp}:{body}"
    computed_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    slack_signature = request.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(computed_signature, slack_signature)


def fetch_expected_public_access_prevention():
    bucket = _storage_client.bucket(TF_STATE_BUCKET)
    blob = bucket.blob(f"{TF_STATE_PREFIX}/default.tfstate")
    state_json = json.loads(blob.download_as_text())

    for resource in state_json.get("resources", []):
        if resource.get("type") == "google_storage_bucket":
            for instance in resource.get("instances", []):
                return instance.get("attributes", {}).get("public_access_prevention")
    return None


def post_confirmation(response_url, message):
    """Replies into the same Slack thread the button was clicked in, using the
    one-time response_url Slack includes in the click payload. Falls back to the
    static webhook if this is being tested manually without a real Slack click."""
    target_url = response_url or SLACK_WEBHOOK_URL
    if not target_url:
        print("No Slack URL available; message:", message)
        return
    try:
        requests.post(target_url, json={"text": message}, timeout=10)
    except Exception as e:
        print("Failed to post confirmation to Slack:", e)


def do_fix():
    expected_value = fetch_expected_public_access_prevention()
    if not expected_value:
        return "Could not read expected value from Terraform state. No action taken."

    bucket = _storage_client.get_bucket(DEMO_BUCKET_NAME)
    current_value = bucket.iam_configuration.public_access_prevention

    if current_value == expected_value:
        return f"No fix needed. {DEMO_BUCKET_NAME} already matches Terraform's expected value ({expected_value})."

    bucket.iam_configuration.public_access_prevention = expected_value
    bucket.patch()

    return (
        f":white_check_mark: *Fix applied* on `{DEMO_BUCKET_NAME}`\n"
        f"public_access_prevention restored from `{current_value}` to `{expected_value}` "
        f"(matching Terraform state). This was a human-approved action."
    )


@functions_framework.http
def apply_fix(request):
    print("--- FIX REQUESTED ---")

    # Real path: Slack calling us because someone clicked "Approve Fix".
    if request.headers.get("X-Slack-Signature"):
        if not verify_slack_signature(request):
            print("Invalid Slack signature, refusing request.")
            return "invalid signature", 403

        form = parse_qs(request.get_data(as_text=True))
        if "payload" not in form:
            return "missing payload", 400
        payload = json.loads(form["payload"][0])
        response_url = payload.get("response_url")

        print("Approved by:", payload.get("user", {}).get("username"))
        message = do_fix()
        print(message)
        post_confirmation(response_url, message)
        return "", 200

    # Manual test path: calling this function directly (e.g. gcloud functions call),
    # with no Slack signature present. Still works, just posts to the static webhook.
    message = do_fix()
    print(message)
    post_confirmation(None, message)
    return message, 200
