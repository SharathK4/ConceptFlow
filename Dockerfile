# ConceptFlow AI - single-container deployment (Cloud Run, Hugging Face Spaces, any Docker host).
#
#   docker build -t conceptflow .
#   docker run -p 8010:8010 --env-file .env -v conceptflow-data:/app/data conceptflow
#
# Stage 1 builds the page; stage 2 is Python + Manim + LaTeX serving the API,
# the page, the A2A agent (/a2a/writers-room) and the MCP server (/mcp) on one
# port. The port comes from $PORT, which Cloud Run sets (8080); it defaults to 8010.

FROM node:22-slim AS page
WORKDIR /page
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8010 \
    CREWAI_DISABLE_TELEMETRY=true \
    CREWAI_TRACING_ENABLED=false
# Cairo/Pango for Manim's text, LaTeX + dvisvgm for its maths, fontconfig for
# the bundled fonts. (Video is encoded by PyAV, which bundles its own FFmpeg.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libcairo2-dev libpango1.0-dev fontconfig \
        texlive-latex-base texlive-latex-recommended texlive-latex-extra \
        texlive-fonts-recommended texlive-science dvisvgm \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user (uid 1000, which Hugging Face Spaces also uses).
RUN useradd --create-home --uid 1000 studio
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt
COPY backend/ backend/
COPY assets/ assets/
COPY scripts/ scripts/
COPY --from=page /page/dist frontend/dist
COPY .env.example ./
RUN mkdir -p data && chown -R studio:studio /app
USER studio

EXPOSE 8010
CMD ["sh", "-c", "exec python -m uvicorn conceptflow.server:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8010} --proxy-headers --forwarded-allow-ips '*'"]
