"""
GPU segmentation worker for GrainVision.

A tiny FastAPI service that the Django app calls (via SAM2_REMOTE_URL) to run
per-grain SAM2 segmentation on a GPU. Deploy it to Azure Container Apps on a
serverless GPU profile so it scales to zero and bills per second.

Endpoints:
  GET  /health   -> {"status","device","cuda_available",...}
  POST /segment  -> multipart image + min_area/max_area -> {"polygons":[...],"count":N}
"""
import io
import os

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

import engine

app = FastAPI(title="GrainVision GPU segmentation worker")
_TOKEN = os.environ.get("WORKER_TOKEN", "")


@app.get("/health")
def health():
    return {"status": "ok", **engine.device_info()}


@app.post("/segment")
async def segment(
    image: UploadFile = File(...),
    min_area: int = Form(50),
    max_area: int = Form(200000),
    x_worker_token: str = Header(default=""),
):
    if _TOKEN and x_worker_token != _TOKEN:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    raw = await image.read()
    arr = np.frombuffer(raw, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return JSONResponse({"error": "could not decode image"}, status_code=400)
    polys = engine.segment(bgr, int(min_area), int(max_area))
    return {"polygons": polys, "count": len(polys)}
