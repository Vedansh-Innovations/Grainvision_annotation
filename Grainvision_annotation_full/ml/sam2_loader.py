"""
SAM2 (Segment Anything Model 2) wrapper.

SAM2 is the platform's segmentation engine. Loading is lazy and cached: the
heavy model is built once on first use and reused for every subsequent image.

  * available()           — enabled + checkpoint present + torch/sam2 importable
  * get_mask_generator()  — cached SAM2AutomaticMaskGenerator (or None)
  * load_error()          — human-readable reason if the model could not load

When settings.SAM2_REQUIRED is True (default) the segmentation service raises a
clear configuration error rather than silently degrading. Set SAM2_REQUIRED=False
to permit the classical OpenCV watershed engine to stand in.
"""
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# Serialises SAM2 inference so two concurrent captures on a single-worker,
# multi-threaded server can't race on the shared generator (cheap-mode safe).
inference_lock = threading.Lock()
_generator = None
_load_attempted = False
_load_error = None


def load_error():
    """Return the last load-failure message, or None."""
    return _load_error


def unavailable_reason():
    """Best-effort human-readable reason SAM2 is not usable right now."""
    return _load_error or _diagnose()


_DEFAULT_CKPT_URL = (
    "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt"
)


def _ensure_checkpoint():
    """Download the SAM2 checkpoint if it's missing (one-time)."""
    path = settings.SAM2_CHECKPOINT
    if os.path.exists(path):
        return
    url = os.environ.get("SAM2_CHECKPOINT_URL", _DEFAULT_CKPT_URL)
    if not url:
        return
    try:
        import urllib.request
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".part"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, path)
    except Exception:
        pass


def available():
    """True if SAM2 is enabled, importable, and the checkpoint exists on disk."""
    if not settings.SAM2_ENABLED:
        return False
    _ensure_checkpoint()
    if not os.path.exists(settings.SAM2_CHECKPOINT):
        return False
    try:
        import torch  # noqa: F401
        import sam2  # noqa: F401
    except Exception:
        return False
    return True


def _diagnose():
    """Explain why SAM2 is not available (for a clear startup error)."""
    if not settings.SAM2_ENABLED:
        return "SAM2_ENABLED is False."
    if not os.path.exists(settings.SAM2_CHECKPOINT):
        return f"Checkpoint not found at {settings.SAM2_CHECKPOINT}."
    try:
        import torch  # noqa: F401
    except Exception as e:
        return f"PyTorch is not importable: {e}"
    try:
        import sam2  # noqa: F401
    except Exception as e:
        return f"The 'sam2' package is not importable: {e}"
    return "Unknown SAM2 availability error."


def get_mask_generator():
    """Return a cached SAM2AutomaticMaskGenerator, or None if it cannot load."""
    global _generator, _load_attempted, _load_error
    if _generator is not None:
        return _generator
    if _load_attempted:
        return None

    with _lock:
        if _generator is not None:
            return _generator
        if _load_attempted:
            return None
        _load_attempted = True

        if not available():
            _load_error = _diagnose()
            logger.warning("SAM2 unavailable: %s", _load_error)
            return None

        try:
            import torch
            from sam2.build_sam import build_sam2
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

            device = settings.SAM2_DEVICE
            # On CPU, use all available cores for the Hiera image encoder.
            if device == "cpu":
                try:
                    torch.set_num_threads(os.cpu_count() or 1)
                except Exception:
                    pass

            model = build_sam2(
                settings.SAM2_MODEL_CFG,
                settings.SAM2_CHECKPOINT,
                device=device,
                # The optional compiled extension (_C) isn't built on CPU-only
                # installs; disabling postprocessing avoids the harmless warning
                # it prints. Masks are unaffected. Stays on for GPU.
                apply_postprocessing=(device != "cpu"),
            )
            _generator = SAM2AutomaticMaskGenerator(
                model,
                points_per_side=settings.SAM2_POINTS_PER_SIDE,
                points_per_batch=settings.SAM2_POINTS_PER_BATCH,
                pred_iou_thresh=settings.SAM2_PRED_IOU_THRESH,
                stability_score_thresh=settings.SAM2_STABILITY_SCORE_THRESH,
                box_nms_thresh=settings.SAM2_BOX_NMS_THRESH,
                min_mask_region_area=settings.SAM2_MIN_MASK_REGION_AREA,
                crop_n_layers=settings.SAM2_CROP_N_LAYERS,
                crop_n_points_downscale_factor=settings.SAM2_CROP_DOWNSCALE,
            )
            logger.info(
                "SAM2 mask generator loaded on %s (points_per_side=%d)",
                device, settings.SAM2_POINTS_PER_SIDE,
            )
        except Exception as e:
            _load_error = f"SAM2 failed to initialise: {e}"
            logger.exception("Failed to initialise SAM2.")
            _generator = None
    return _generator
