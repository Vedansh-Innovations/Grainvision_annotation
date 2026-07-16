"""
Modal deployment for the GrainVision GPU segmentation worker.

This runs ONLY the SAM2 per-grain segmentation on a serverless GPU that
scales to zero (you pay per second, nothing while idle). The Django app on
DigitalOcean calls it over HTTPS via SAM2_REMOTE_URL and falls back to the
CPU OpenCV engine if it is ever unreachable.

Deploy:
    pip install modal
    modal token new
    modal secret create grainvision-worker WORKER_TOKEN=<a-long-random-secret>
    modal deploy modal_app.py

The command prints a public URL like
    https://<you>--grainvision-seg-fastapi.modal.run
Put that (no trailing slash) in the Django app's SAM2_REMOTE_URL, and the
SAME WORKER_TOKEN in SAM2_REMOTE_TOKEN.

"High sensitivity — detect the slightest part" is controlled by the env
values in SEG_ENV below (points-per-side, crop layers, tiling). Raise them
for more sensitivity (slower / a little more cost per plate); lower them if
plates time out or cost too much.
"""
import modal

CHECKPOINT_URL = "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt"
CHECKPOINT_PATH = "/models/sam2.1_hiera_small.pt"

# ── High-sensitivity segmentation settings (baked into the worker) ──
# These are read by worker/engine.py. Tune here, then `modal deploy` again.
SEG_ENV = {
    "SAM2_CHECKPOINT": CHECKPOINT_PATH,
    "SAM2_MODEL_CFG": "configs/sam2.1/sam2.1_hiera_s.yaml",
    "SAM2_DEVICE": "cuda",
    "SAM2_POINTS_PER_SIDE": "64",     # ↑ = catch smaller specks (48–96 typical)
    "SAM2_POINTS_PER_BATCH": "128",
    "SAM2_PRED_IOU_THRESH": "0.70",
    "SAM2_STABILITY_SCORE_THRESH": "0.85",
    "SAM2_BOX_NMS_THRESH": "0.70",
    "SAM2_CROP_N_LAYERS": "1",        # extra zoom pass for tiny grains
    "SAM2_CROP_DOWNSCALE": "2",
    "SAM2_TILE_SIZE": "1024",         # tile large plates so nothing is missed
}

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "libgl1", "libglib2.0-0", "wget")
    # CUDA torch first, then SAM2 + the worker's service libs
    .pip_install(
        "torch==2.5.1", "torchvision==0.20.1",
        extra_index_url="https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "git+https://github.com/facebookresearch/sam2.git@main",
        "fastapi==0.111.0", "uvicorn[standard]==0.30.1",
        "python-multipart==0.0.9", "opencv-python-headless==4.10.0.84",
        "numpy==1.26.4", "pillow==10.3.0",
    )
    # download the model into the image so cold starts don't fetch it
    .run_commands(
        "mkdir -p /models",
        f"wget -q -O {CHECKPOINT_PATH} {CHECKPOINT_URL}",
    )
    # bake the worker code (app.py, engine.py, tiling.py) into the image
    .add_local_dir("worker", "/worker", copy=True)
    .env(SEG_ENV)
)

app = modal.App("grainvision-seg")


@app.function(
    image=image,
    gpu="L4",                       # 24 GB; plenty for SAM2 at high settings
    secrets=[modal.Secret.from_name("grainvision-worker")],  # provides WORKER_TOKEN
    scaledown_window=300,           # stay warm 5 min between plates, then → 0
    timeout=600,                    # max seconds per segmentation request
    max_containers=10,
)
@modal.concurrent(max_inputs=4)     # a few plates per warm container
@modal.asgi_app()
def fastapi():
    import sys
    sys.path.insert(0, "/worker")
    import app as worker            # worker/app.py  (FastAPI instance = worker.app)
    return worker.app
