# Deploy to Google Cloud Run

One container serves everything: the page, the API, the A2A agent
(`/a2a/writers-room`) and the MCP server (`/mcp`). Cloud Run builds it from the
`Dockerfile`, runs it on a public HTTPS URL and scales it to zero when idle.

**Cost.** Cloud Run, Cloud Build, Artifact Registry and Secret Manager all have
monthly free allowances, and a class demo stays inside them. The exception is
image storage: the image is about 2.5 GB (LaTeX), against 0.5 GB of free
Artifact Registry storage, so expect roughly $0.20 a month while it is stored.
Google requires a billing account even for free-tier use. Set a budget alert
(step 8), and delete the service when you are done (step 9).

The commands below work in PowerShell and in bash. Replace `YOUR_PROJECT_ID`.

## 1. Sign in and choose a project

```bash
gcloud auth login
gcloud projects create YOUR_PROJECT_ID   # or use an existing project
gcloud config set project YOUR_PROJECT_ID
```

Link a billing account to the project: Cloud Console → Billing → *Link a billing
account*.

## 2. Turn on the services

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

## 3. Store the keys as secrets

Create only the secrets you have keys for. Only `GOOGLE_API_KEY` is needed for
live lessons.

```bash
echo YOUR_GEMINI_KEY | gcloud secrets create gemini-api-key --data-file=-
echo YOUR_ELEVENLABS_KEY | gcloud secrets create elevenlabs-api-key --data-file=-
echo YOUR_SARVAM_KEY | gcloud secrets create sarvam-api-key --data-file=-
echo YOUR_LANGFUSE_PUBLIC_KEY | gcloud secrets create langfuse-public-key --data-file=-
echo YOUR_LANGFUSE_SECRET_KEY | gcloud secrets create langfuse-secret-key --data-file=-
```

The studio strips the trailing newline that `echo` adds.

Let Cloud Run's runtime account read them. The project number is shown by
`gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)"`:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```

## 4. Build the image (about 15 minutes, once)

```bash
gcloud artifacts repositories create conceptflow --repository-format=docker --location=us-central1
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/conceptflow/studio:latest --timeout=3600s
```

`us-central1` is a Tier 1 region, which has the lowest prices.

## 5. Deploy

```bash
gcloud run deploy conceptflow --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/conceptflow/studio:latest --region us-central1 --allow-unauthenticated --cpu 4 --memory 8Gi --timeout 3600 --no-cpu-throttling --max-instances 1 --concurrency 40 --set-env-vars "DEFAULT_QUALITY=draft,LLM_MODE=auto" --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest,ELEVENLABS_API_KEY=elevenlabs-api-key:latest,SARVAM_API_KEY=sarvam-api-key:latest,LANGFUSE_PUBLIC_KEY=langfuse-public-key:latest,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest"
```

Leave out of `--set-secrets` any secret you did not create in step 3.

Why these flags:

| Flag | Reason |
|---|---|
| `--no-cpu-throttling` | Lessons are made in the background after the request that starts them returns. Without this flag Cloud Run removes the CPU between requests and rendering stalls. |
| `--max-instances 1` | The job queue and live event streams are held in memory, so every visitor must reach the same instance. |
| `--cpu 4 --memory 8Gi` | Manim renders scenes in parallel. LaTeX and Cairo need the memory. |
| `--timeout 3600` | The page follows a lesson over one long Server-Sent Events request. |
| `DEFAULT_QUALITY=draft` | 720p renders about twice as fast as 1080p on 4 vCPUs. |

## 6. Tell the studio its public address

The A2A Agent Card advertises this address to other agents.

```bash
gcloud run services describe conceptflow --region us-central1 --format="value(status.url)"
gcloud run services update conceptflow --region us-central1 --update-env-vars PUBLIC_URL=https://conceptflow-XXXX-uc.a.run.app
```

## 7. Check it

Open the URL and make a lesson. Then check the protocol endpoints:

```bash
curl https://conceptflow-XXXX-uc.a.run.app/api/health
curl https://conceptflow-XXXX-uc.a.run.app/a2a/writers-room/.well-known/agent-card.json
```

The MCP server is at `https://…/mcp/`. You can point the MCP Inspector at it,
or run `npx @modelcontextprotocol/inspector`.

Run the end-to-end evaluation against the deployment:

```bash
python scripts/evaluate.py --url https://conceptflow-XXXX-uc.a.run.app --prompts "Explain the derivative"
```

## 8. Set a budget alert

Cloud Console → Billing → Budgets & alerts → *Create budget*. Set an amount of
$1 with alerts at 50% and 100%.

## 9. Remove it when you are done

```bash
gcloud run services delete conceptflow --region us-central1
gcloud artifacts repositories delete conceptflow --location us-central1
```

## Notes

- **Storage is temporary.** Lessons are written inside the container and
  disappear when the instance is replaced. To keep them, mount a Cloud
  Storage bucket at `/app/data` (Cloud Run → Edit → Volumes) and set
  `DATA_DIR=/app/data`.
- **Building on your own machine** (Docker Desktop) instead of Cloud Build:
  ```bash
  gcloud auth configure-docker us-central1-docker.pkg.dev
  docker build -t us-central1-docker.pkg.dev/YOUR_PROJECT_ID/conceptflow/studio:latest .
  docker push us-central1-docker.pkg.dev/YOUR_PROJECT_ID/conceptflow/studio:latest
  ```
