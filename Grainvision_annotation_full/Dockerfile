# GrainVision AI — production image
# Builds the Django app with SAM2 (CPU build of PyTorch) and bakes the checkpoint
# into the image so the container starts without any runtime download.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SAM2_BUILD_CUDA=0

# ── OS deps: OpenCV runtime libs, git (for the sam2 pip install), curl ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (torch CPU first so SAM2 doesn't pull the CUDA build) ──
COPY requirements.txt requirements-gpu.txt .
RUN pip install --upgrade pip setuptools wheel \
    && pip install torch==2.3.1 torchvision==0.18.1 \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-build-isolation -r requirements.txt -r requirements-gpu.txt

# ── SAM2 checkpoint (hiera-small, ~176 MB) baked into the image ──
RUN mkdir -p ml_models \
    && curl -L -o ml_models/sam2.1_hiera_small.pt \
        https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt

# ── App source ──
COPY . .

# ── Collect static at build time (no DB or model load needed) ──
RUN SECRET_KEY=build-time-only DEBUG=False ALLOWED_HOSTS=* \
    SAM2_ENABLED=False USE_POSTGRES=False \
    python manage.py collectstatic --noinput

RUN chmod +x docker/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
