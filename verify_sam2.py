import os, sys, time, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "grainvision.settings")
django.setup()
import numpy as np, cv2
from ml import segmentation, sam2_loader

def log(m): print(m, flush=True)

log(f"SAM2 available: {sam2_loader.available()}  device={__import__('django').conf.settings.SAM2_DEVICE}  pps={__import__('django').conf.settings.SAM2_POINTS_PER_SIDE}")

# synthetic plate: white ceramic disc, ~28 brown grain blobs
w = h = 900
img = np.full((h, w, 3), 20, np.uint8)
cx, cy, r = w // 2, h // 2, int(w * 0.42)
cv2.circle(img, (cx, cy), r, (245, 245, 245), -1)
rng = np.random.default_rng(7)
for _ in range(28):
    a = rng.uniform(0, 2 * np.pi); rad = rng.uniform(0, r * 0.78)
    px, py = int(cx + rad * np.cos(a)), int(cy + rad * np.sin(a))
    cv2.ellipse(img, (px, py), (int(rng.integers(16, 26)), int(rng.integers(11, 17))),
                float(rng.uniform(0, 180)), 0, 360, (60, 90, 140), -1)

class C:
    min_particle_area_px = 60; max_particle_area_px = 60000; expected_min_count = 20

log("running segment_image (SAM2)…")
t = time.time()
res = segmentation.segment_image(img, C())
dt = time.time() - t
log(f"engine    : {res['engine']}")
log(f"particles : {len(res['particles'])}")
log(f"crop_size : {res['crop_size']}")
log(f"flagged   : {res['merge_flagged_count']}")
log(f"elapsed   : {dt:.1f}s")
ok = res["engine"] == "sam2" and len(res["particles"]) > 0
log("RESULT: " + ("SAM2 PIPELINE OK" if ok else "FAILED"))
sys.exit(0 if ok else 1)
