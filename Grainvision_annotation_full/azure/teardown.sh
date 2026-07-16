#!/usr/bin/env bash
# Delete EVERYTHING created by deploy.sh (the whole resource group).
set -euo pipefail
RG="${RG:?set RG (e.g. export RG=grainvision-rg)}"
read -p "Delete resource group '$RG' and ALL its resources? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted"; exit 1; }
az group delete -n "$RG" --yes --no-wait
echo "Deletion started for $RG."
