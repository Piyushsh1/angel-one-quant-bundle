# ==============================================================================
#  algo-barbell — single application image
# ==============================================================================
#  The SAME image runs every role (three trading engines, the EOD regime job,
#  and the dashboard) in all three environments.  Behaviour is decided purely
#  by environment variables — APP_ENV picks the database and whether orders are
#  simulated or real.  Nothing environment-specific is baked into the image.
#
#  Build:
#      docker build -t algo-barbell:latest .
#
#  Run a role:
#      docker run --env-file env/dev.env algo-barbell python src/macro_engine.py
# ==============================================================================
# pandas-ta 0.4.71b0 requires Python >= 3.12, so 3.12 is the floor here.
FROM python:3.12-slim

# Asia/Kolkata everywhere: every trading-window calculation assumes IST.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata \
    PYTHONPATH=/app/src

WORKDIR /app

# tzdata is required for ZoneInfo("Asia/Kolkata"); curl backs the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so edits to source don't invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY verify/ ./verify/

# Run unprivileged. logs/ and data/ are the only writable paths the app needs.
RUN useradd --create-home --uid 10001 algo \
 && mkdir -p /app/logs /app/data \
 && chown -R algo:algo /app
USER algo

# No default long-running role: this image is invoked per task (an engine, the
# preflight checker, flatten, metrics). Compose and the control script pass an
# explicit command. Default to the preflight so a bare `docker run` does
# something safe and informative rather than trading.
CMD ["python", "verify/verify.py"]
