# TapeJudge — production image.
#
# The app is a small Flask application with one third-party runtime dependency
# (Flask) and a SQLite store. This image installs it, runs it as a non-root user
# behind gunicorn, and keeps the database on a mountable volume.
#
# Build:  docker build -t tapejudge:latest .
# Run:    docker run -p 8080:8080 -v tapejudge-data:/data \
#           -e TAPEJUDGE_SECRET=<32+ random bytes> tapejudge:latest

FROM python:3.13-slim AS base

# Keep the image quiet and predictable.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so a code change does not reinstall them.
COPY pyproject.toml README.md ./
COPY tapejudge ./tapejudge
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
RUN pip install --no-cache-dir . gunicorn && chmod 755 ./scripts/docker-entrypoint.sh

# A non-root user owns the data directory (the SQLite file lives on a volume).
# The source tree is made world-readable: the build context can carry a
# restrictive umask, and the runtime user is not the build user.
RUN useradd --create-home --uid 10001 tapejudge \
    && mkdir -p /data && chown tapejudge:tapejudge /data \
    && chmod -R a+rX /app
USER tapejudge

# Bind to all interfaces *inside the container*; publish only 8080 on the host.
ENV TAPEJUDGE_DB=/data/tapejudge.db \
    TAPEJUDGE_HOST=0.0.0.0 \
    TAPEJUDGE_PORT=8080 \
    TAPEJUDGE_WORKERS=1

EXPOSE 8080
VOLUME ["/data"]

# The image's own health check uses the app's honest health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
