# Infra Drift Detective

An agent that compares your real cloud resources against your Terraform code and flags anything that was changed manually outside of Terraform, explaining exactly what changed and why it is risky. A second, separate agent can then apply the fix back to the correct Terraform-defined state, but only after a human triggers it.

Replace `<YOUR_PROJECT_ID>` and `<YOUR_REGION>` with your own project ID and region wherever they appear.

---

## Architecture

```
terraform/ (one demo GCS bucket, state stored in a GCS backend bucket)
        │
        ▼
drift-agent (Cloud Function, READ-ONLY)
        │
        ├─ reads the Terraform state file directly from the GCS backend
        ├─ reads the bucket's live config via the Storage API
        ├─ compares specific fields (e.g. public_access_prevention)
        │
        ├─ Gemini 2.5 (Vertex AI): explains the drift and the risk
        │
        └─ post_to_slack()  -> drift report, no changes made

fix-agent (Cloud Function, WRITE-SCOPED, triggered manually/by approval)
        │
        ├─ reads the same Terraform state to know the expected value
        ├─ patches ONLY that one field back to the expected value
        └─ post_to_slack()  -> confirms the fix
```

Two separate service accounts are used on purpose: `drift-agent` can only look and never touches anything. `fix-agent` can only act, and only on the one field it is scoped to fix, and only when a human runs it.

---

## Repo contents

| Path | Purpose |
|---|---|
| `terraform/main.tf` | The demo resource: one locked-down GCS bucket |
| `terraform/variables.tf` | Input variables (`project_id`, `region`, `bucket_name_suffix`) |
| `terraform/providers.tf` | The Google provider block |
| `terraform/versions.tf` | Terraform version + provider version constraints |
| `terraform/outputs.tf` | Outputs you'll need later (bucket name, expected config) |
| `terraform/backend.tf` | GCS backend for the Terraform state file |
| `terraform/terraform.tfvars.example` | Template for your own `terraform.tfvars` |
| `drift-agent/main.py` | Read-only Cloud Function: detects and explains drift |
| `fix-agent/main.py` | Write-scoped Cloud Function: fixes one field, on request |

---

## Prerequisites

1. A GCP project with billing enabled.
2. Owner or Editor IAM role on the project.
3. The `terraform` CLI installed locally or in Cloud Shell (Cloud Shell already has it — run `terraform -version` to check).
4. A Slack workspace with an Incoming Webhook URL ready.
5. `gcloud` CLI installed, or use Cloud Shell.

---

## Step 1 — Enable the required APIs

### Via the Console (UI)

1. Open `console.cloud.google.com` and select your project.
2. Go to **APIs & Services → Library**.
3. Enable each of these:
   - Cloud Storage API
   - Cloud Functions API
   - Cloud Build API
   - Vertex AI API

### Via Cloud Shell (gcloud)

```bash
gcloud services enable storage.googleapis.com \
  cloudfunctions.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  --project=<YOUR_PROJECT_ID>
```

---

## Step 2 — Create the Terraform state backend bucket

### Via the Console (UI)

1. Go to **Cloud Storage → Buckets → Create**.
2. Name: `<YOUR_PROJECT_ID>-tf-state`.
3. Region: `<YOUR_REGION>`.
4. Leave the rest as default and click **Create**.

### Via Cloud Shell (gcloud)

```bash
gsutil mb -l <YOUR_REGION> gs://<YOUR_PROJECT_ID>-tf-state
```

---

## Step 3 — Deploy the demo infrastructure with Terraform

```bash
cd infra-drift-detective-gcp/terraform

cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set your real `project_id`.

Edit `backend.tf` and replace `<YOUR_PROJECT_ID>` with your real project ID (Terraform does not support variables inside the `backend` block, so this one must be hardcoded).

```bash
terraform init
terraform plan
terraform apply
```

Type `yes` when prompted. Once it finishes, run:

```bash
terraform output
```

Note the `demo_bucket_name` value — you will need it as `DEMO_BUCKET_NAME` in Steps 6 and 7.

---

## Step 4 — Create the read-only service account for `drift-agent`

### Via the Console (UI)

1. Go to **IAM & Admin → Service Accounts → Create Service Account**.
2. Name: `drift-agent-readonly`.
3. Grant these roles:
   - Storage Object Viewer
   - Vertex AI User
4. Click **Done**.

### Via Cloud Shell (gcloud)

```bash
gcloud iam service-accounts create drift-agent-readonly \
  --display-name="Drift Agent (read-only)" \
  --project=<YOUR_PROJECT_ID>

for role in roles/storage.objectViewer roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
    --member="serviceAccount:drift-agent-readonly@<YOUR_PROJECT_ID>.iam.gserviceaccount.com" \
    --role="$role"
done
```

---

## Step 5 — Create the narrow write-scoped service account for `fix-agent`

This account should only be able to modify the **one demo bucket**, not anything else in the project.

### Via the Console (UI)

1. **IAM & Admin → Service Accounts → Create Service Account** → name it `drift-fix-agent`. Do not grant it any project-level roles.
2. Go to **Cloud Storage → Buckets**, click your demo bucket (`<YOUR_PROJECT_ID>-drift-demo`).
3. Go to the **Permissions** tab.
4. Click **Add Principal**.
5. Principal: `drift-fix-agent@<YOUR_PROJECT_ID>.iam.gserviceaccount.com`.
6. Role: **Storage Admin** (bucket-level, not project-level).
7. Click **Save**.

### Via Cloud Shell (gcloud)

```bash
gcloud iam service-accounts create drift-fix-agent \
  --display-name="Drift Fix Agent (write, scoped to one bucket)" \
  --project=<YOUR_PROJECT_ID>

gsutil iam ch \
  serviceAccount:drift-fix-agent@<YOUR_PROJECT_ID>.iam.gserviceaccount.com:roles/storage.admin \
  gs://<YOUR_PROJECT_ID>-drift-demo
```

Also grant it read access to the Terraform state bucket, since it needs to read the expected value from state:

```bash
gsutil iam ch \
  serviceAccount:drift-fix-agent@<YOUR_PROJECT_ID>.iam.gserviceaccount.com:roles/storage.objectViewer \
  gs://<YOUR_PROJECT_ID>-tf-state
```

---

## Step 6 — Deploy `drift-agent`

### Via Cloud Shell (gcloud)

```bash
cd ../drift-agent

gcloud functions deploy drift-agent \
  --gen2 \
  --runtime=python312 \
  --region=<YOUR_REGION> \
  --source=. \
  --entry-point=check_drift \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account=drift-agent-readonly@<YOUR_PROJECT_ID>.iam.gserviceaccount.com \
  --set-env-vars=GCP_PROJECT=<YOUR_PROJECT_ID>,VERTEX_LOCATION=us-central1,TF_STATE_BUCKET=<YOUR_PROJECT_ID>-tf-state,TF_STATE_PREFIX=infra-drift-demo,DEMO_BUCKET_NAME=<YOUR_PROJECT_ID>-drift-demo,SLACK_WEBHOOK_URL=<YOUR_WEBHOOK_URL> \
  --project=<YOUR_PROJECT_ID>
```

### Alternative: deploy as a Cloud Run function from the Console (no Docker)

1. Go to **Cloud Run → Create Service → Function** tab.
2. Service name: `drift-agent`.
3. Region: `<YOUR_REGION>`.
4. Runtime: **Python 3.12**.
5. Entry point: `check_drift`.
6. Paste `drift-agent/main.py` and `drift-agent/requirements.txt` into the inline editor.
7. Trigger: **HTTPS**, require authentication.
8. Service account: `drift-agent-readonly@<YOUR_PROJECT_ID>.iam.gserviceaccount.com`.
9. Environment variables: same as the `--set-env-vars` list above, one per field.
10. Click **Create**.

---

## Step 7 — Deploy `fix-agent`

### Via Cloud Shell (gcloud)

```bash
cd ../fix-agent

gcloud functions deploy fix-agent \
  --gen2 \
  --runtime=python312 \
  --region=<YOUR_REGION> \
  --source=. \
  --entry-point=apply_fix \
  --trigger-http \
  --no-allow-unauthenticated \
  --service-account=drift-fix-agent@<YOUR_PROJECT_ID>.iam.gserviceaccount.com \
  --set-env-vars=GCP_PROJECT=<YOUR_PROJECT_ID>,TF_STATE_BUCKET=<YOUR_PROJECT_ID>-tf-state,TF_STATE_PREFIX=infra-drift-demo,DEMO_BUCKET_NAME=<YOUR_PROJECT_ID>-drift-demo,SLACK_WEBHOOK_URL=<YOUR_WEBHOOK_URL> \
  --project=<YOUR_PROJECT_ID>
```

### Alternative: deploy from the Console

Same as Step 6's alternative, but service name `fix-agent`, entry point `apply_fix`, service account `drift-fix-agent@<YOUR_PROJECT_ID>.iam.gserviceaccount.com`.

---

## Step 8 — Allow yourself to invoke both functions for testing

```bash
gcloud functions add-invoker-policy-binding drift-agent \
  --region=<YOUR_REGION> \
  --member="user:<YOUR_EMAIL>" \
  --project=<YOUR_PROJECT_ID>

gcloud functions add-invoker-policy-binding fix-agent \
  --region=<YOUR_REGION> \
  --member="user:<YOUR_EMAIL>" \
  --project=<YOUR_PROJECT_ID>
```

**Via the Console:** go to **Cloud Run → (each service) → Permissions → Add Principal**, add your email with role **Cloud Run Invoker**.

---

## Step 9 — Test: confirm no drift exists yet

```bash
gcloud functions call drift-agent \
  --region=<YOUR_REGION> \
  --project=<YOUR_PROJECT_ID>
```

Expected output: `No drift detected on <bucket>. Live config matches Terraform state.`

---

## Step 10 — Simulate drift

Manually change the bucket's setting outside of Terraform, the way a real engineer might do it by accident:

**Via the Console (UI):**
1. Go to **Cloud Storage → Buckets → your demo bucket**.
2. Go to the **Permissions** tab.
3. Find **Public access prevention** and toggle it off (**Inherited** instead of **Enforced**).

**Via Cloud Shell (gcloud):**
```bash
gcloud storage buckets update gs://<YOUR_PROJECT_ID>-drift-demo --no-public-access-prevention
```

---

## Step 11 — Run the detector again

```bash
gcloud functions call drift-agent \
  --region=<YOUR_REGION> \
  --project=<YOUR_PROJECT_ID>
```

This time it should detect the mismatch and post a Slack message explaining the drift and the risk, written by Gemini based on the real before/after values.

---

## Step 12 — Apply the human-approved fix

```bash
gcloud functions call fix-agent \
  --region=<YOUR_REGION> \
  --project=<YOUR_PROJECT_ID>
```

Check Slack for the confirmation message. Then re-run Step 9's command — it should now say "No drift detected" again.

---

## Troubleshooting

**"The caller does not have permission" when testing:**
Re-run Step 8, or confirm the correct email with `gcloud config get-value account`.

**`drift-agent` can't read the Terraform state file:**
Confirm `TF_STATE_BUCKET` and `TF_STATE_PREFIX` env vars exactly match what's in `backend.tf`, and that `drift-agent-readonly` has `Storage Object Viewer` on the state bucket.

**`fix-agent` fails to patch the bucket:**
Confirm `drift-fix-agent` has the `Storage Admin` role at the **bucket** level (Step 5) — a project-level role is not required and should be avoided on purpose.

**Terraform `apply` fails with a backend error:**
Make sure the state bucket from Step 2 exists and `backend.tf` has your real project ID hardcoded in, not a variable.

---

## Expected end state

- A Terraform-managed demo bucket exists, state stored remotely in GCS.
- `drift-agent` correctly reports "no drift" when things match, and clearly explains drift when they don't.
- `fix-agent` can restore the one field it's scoped to, only when triggered, and confirms in Slack.
- Neither function can affect anything beyond the one demo bucket.

---

## Safety design

`drift-agent-readonly` can only read the Terraform state bucket and the demo bucket's metadata, and call Vertex AI — it cannot write or delete anything. `drift-fix-agent` has write access, but it is scoped to a single bucket via a bucket-level IAM binding, not a project-wide role, and it only ever changes the one field it detected drift on. Neither agent runs `terraform apply` or has any broader infrastructure access.
