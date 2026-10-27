# Deploy to Hugging Face Spaces (free, no card)

A Docker Space runs the studio's `Dockerfile` on free hardware: 2 vCPU,
16 GB RAM, a public HTTPS URL. It needs no credit card. Free Spaces sleep after
48 hours without visitors and wake on the next visit. Lessons are kept only
until the Space restarts.

## 1. Create the Space

1. Sign up at <https://huggingface.co> (free).
2. Go to **New → Space**. Give it a name, for example `conceptflow`, then
   choose **SDK: Docker → Blank** and **Hardware: CPU basic (free)**. Make it
   public or private.

## 2. Add the keys as secrets

In the Space, go to **Settings → Variables and secrets**.

| Kind | Name | Value |
|---|---|---|
| Secret | `GOOGLE_API_KEY` | your Gemini key (needed for live lessons) |
| Secret | `ELEVENLABS_API_KEY` | optional |
| Secret | `SARVAM_API_KEY` | optional |
| Secret | `LANGFUSE_PUBLIC_KEY` | optional, for Langfuse tracing |
| Secret | `LANGFUSE_SECRET_KEY` | optional |
| Variable | `DEFAULT_QUALITY` | `draft` (720p renders twice as fast on 2 vCPUs) |
| Variable | `PUBLIC_URL` | `https://YOUR_USER-conceptflow.hf.space` |

## 3. Upload the code

Create an access token with **write** permission (Profile → Settings → Access
Tokens), then from the project folder run:

```bash
pip install -U huggingface_hub
hf auth login
hf upload YOUR_USER/conceptflow . . --repo-type space --exclude ".env" ".venv/*" "data/*" "frontend/node_modules/*" "frontend/dist/*" "*/__pycache__/*" ".claude/*" "docs/screenshots/*"
hf upload YOUR_USER/conceptflow deploy/huggingface/README.md README.md --repo-type space
```

The second command replaces the Space's README with one whose header tells
Hugging Face that the app listens on port 8010. With an older `huggingface_hub`,
use `huggingface-cli login` and `huggingface-cli upload` with the same
arguments.

The Space builds the image itself. The first build takes about 15–20 minutes
because LaTeX is large. Follow it in the **Logs** tab. When it reads
`Running`, open `https://YOUR_USER-conceptflow.hf.space`.

## 4. Check it

```bash
curl https://YOUR_USER-conceptflow.hf.space/api/health
curl https://YOUR_USER-conceptflow.hf.space/a2a/writers-room/.well-known/agent-card.json
python scripts/evaluate.py --url https://YOUR_USER-conceptflow.hf.space --prompts "Explain the derivative"
```

## Notes

- **The display font.** The page's display font, *Against*, is free for
  personal, non-commercial use only. A course demo is non-commercial. For
  anything else, buy its licence, or change `--display` in
  `frontend/src/styles/tokens.css` to `'Instrument Serif'`. That font is under
  the OFL and already bundled.
- **Updating.** Run the two `hf upload` commands again. The Space rebuilds on
  every push.
