#!/usr/bin/env bash
# Rebuild the image after code changes and roll it out.
# Uses the same names as deploy.sh — pass the same PREFIX/SUFFIX you used there,
# or export RG/ACR/APP explicitly.
set -euo pipefail
PREFIX="${PREFIX:-grainvision}"
RG="${RG:?set RG (e.g. export RG=grainvision-rg)}"
ACR="${ACR:?set ACR (e.g. export ACR=grainvisionacrXXXXXX)}"
APP="${APP:?set APP (e.g. export APP=grainvision-XXXXXX)}"
IMAGE="grainvision:latest"

echo "Rebuilding image in ACR ($ACR)..."
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . -o none
echo "Restarting web app ($APP)..."
az webapp restart -g "$RG" -n "$APP" -o none
echo "Done. Tail logs: az webapp log tail -g $RG -n $APP"
