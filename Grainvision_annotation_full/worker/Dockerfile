# GrainVision GPU segmentation worker.
# CUDA + PyTorch base so SAM2 runs on the GPU; bakes the checkpoint in.
FROM pytorch/pytorch:2.3.1-cuda12.1-cudnn8-runtime

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 SAM2_DEVICE=cuda

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# SAM2 checkpoint (hiera-small by default; use the build arg for hiera-large).
ARG SAM2_CKPT_URL=https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt
ARG SAM2_CKPT_NAME=sam2.1_hiera_small.pt
RUN mkdir -p ml_models && curl -L -o ml_models/${SAM2_CKPT_NAME} ${SAM2_CKPT_URL}
ENV SAM2_CHECKPOINT=/app/ml_models/${SAM2_CKPT_NAME}

COPY tiling.py engine.py app.py ./

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
