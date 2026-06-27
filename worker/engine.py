"""
SAM2 segmentation engine for the GPU worker.

Loads SAM2's automatic mask generator once (on CUDA) and exposes a `segment`
function that runs it tiled, returning per-grain polygons in image coordinates.
Config comes from environment variables so the same image runs anywhere.
"""
import os
import threading

import cv2
import numpy as np

import tiling

_DEVICE = os.environ.get("SAM2_DEVICE", "cuda")
_CKPT = os.environ.get("SAM2_CHECKPOINT", "/app/ml_models/sam2.1_hiera_small.pt")
_CFG = os.environ.get("SAM2_MODEL_CFG", "configs/sam2.1/sam2.1_hiera_s.yaml")

# Per-grain "power" defaults — dense grid + crop layers + tiling.
_PPS = int(os.environ.get("SAM2_POINTS_PER_SIDE", "64"))
_PPB = int(os.environ.get("SAM2_POINTS_PER_BATCH", "128"))
_IOU = float(os.environ.get("SAM2_PRED_IOU_THRESH", "0.7"))
_STAB = float(os.environ.get("SAM2_STABILITY_SCORE_THRESH", "0.85"))
_NMS = float(os.environ.get("SAM2_BOX_NMS_THRESH", "0.7"))
_CROP_LAYERS = int(os.environ.get("SAM2_CROP_N_LAYERS", "1"))
_CROP_DS = int(os.environ.get("SAM2_CROP_DOWNSCALE", "2"))
_TILE = int(os.environ.get("SAM2_TILE_SIZE", "0")) or None

_gen = None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()


def _build():
    global _gen
    if _gen is not None:
        return _gen
    with _load_lock:
        if _gen is not None:
            return _gen
        import torch
        from sam2.build_sam import build_sam2
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

        model = build_sam2(_CFG, _CKPT, device=_DEVICE, apply_postprocessing=True)
        _gen = SAM2AutomaticMaskGenerator(
            model,
            points_per_side=_PPS,
            points_per_batch=_PPB,
            pred_iou_thresh=_IOU,
            stability_score_thresh=_STAB,
            box_nms_thresh=_NMS,
            crop_n_layers=_CROP_LAYERS,
            crop_n_points_downscale_factor=_CROP_DS,
            min_mask_region_area=0,   # area filtering happens in tiling
        )
        try:
            print(f"[engine] SAM2 ready on {_DEVICE} "
                  f"(pps={_PPS}, crop_layers={_CROP_LAYERS}, cuda={torch.cuda.is_available()})",
                  flush=True)
        except Exception:
            pass
    return _gen


def device_info():
    try:
        import torch
        return {"device": _DEVICE, "cuda_available": torch.cuda.is_available(),
                "points_per_side": _PPS, "crop_n_layers": _CROP_LAYERS}
    except Exception as e:  # torch not importable
        return {"device": _DEVICE, "cuda_available": False, "error": str(e)}


def segment(bgr, min_area, max_area):
    """Tiled per-grain segmentation. Returns list of [[x,y],...] polygons."""
    gen = _build()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def generate_fn(rgb_tile):
        with _infer_lock:
            masks = gen.generate(rgb_tile)
        return [m["segmentation"].astype(np.uint8) for m in masks]

    polys = tiling.tiled_polygons(
        rgb, generate_fn, min_area=min_area, max_area=max_area, tile=_TILE,
    )
    return [c.reshape(-1, 2).tolist() for c in polys]
