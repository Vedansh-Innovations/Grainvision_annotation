"""
Auto "suspected foreign matter" flagging (CPU, no ML model needed).

Foreign matter (stones, husk, other seeds, dirt) usually stands out from the
dominant grain population in colour, size, or shape. We compute robust outlier
scores over the per-particle features that segmentation already extracted and
mark the strong outliers so the annotation canvas can highlight them for the
assayer. This is a *hint*, not a verdict — the human still labels.
"""
import numpy as np


def flag_foreign_suspects(particles):
    """Mutate each particle's `features` dict in place, adding:
       features["foreign_suspect"] : bool
       features["foreign_score"]   : float (higher = more unusual)
    """
    feats = [p.get("features") or {} for p in particles]
    n = len(feats)
    if n < 8:
        for f in feats:
            f["foreign_suspect"] = False
            f["foreign_score"] = 0.0
        return particles

    def column(key, idx=None):
        out = []
        for f in feats:
            v = f.get(key)
            if idx is not None and isinstance(v, (list, tuple)) and len(v) > idx:
                v = v[idx]
            out.append(float(v) if isinstance(v, (int, float)) else np.nan)
        return np.array(out, dtype=float)

    dims = {
        "area": column("area"),
        "solidity": column("solidity"),
        "aspect": column("aspect_ratio"),
        "L": column("mean_lab", 0),
        "a": column("mean_lab", 1),
        "b": column("mean_lab", 2),
    }
    # Colour is the strongest foreign-matter signal (stones grey, husk pale,
    # other seeds off-colour). Size is de-emphasised so merged grain clumps
    # (a separate "merge" problem) don't get mislabelled as foreign.
    weights = {"a": 1.6, "b": 1.6, "L": 0.9, "area": 0.35, "solidity": 0.7, "aspect": 0.7}

    def robust_z(v):
        ok = ~np.isnan(v)
        z = np.zeros_like(v)
        if ok.sum() < 4:
            return z
        med = np.median(v[ok])
        mad = np.median(np.abs(v[ok] - med)) or 1e-6
        z[ok] = 0.6745 * (v[ok] - med) / mad
        return z

    z = {k: robust_z(v) for k, v in dims.items()}
    score = np.zeros(n)
    for k, zz in z.items():
        score += weights[k] * np.abs(zz)

    # Conservative cutoff: only genuine outliers. Foreign matter is normally a
    # small minority, so cap the flags at ~10% and require a clear deviation.
    smed = np.median(score)
    smad = np.median(np.abs(score - smed)) or 1e-6
    cutoff = smed + 4.0 * 1.4826 * smad
    extreme = (np.abs(z["a"]) > 5.0) | (np.abs(z["b"]) > 5.0)
    suspect = (score > cutoff) | extreme

    if suspect.sum() > 0.08 * n:
        thresh = np.quantile(score, 0.92)
        suspect = score >= thresh

    smax = float(score.max()) or 1.0
    for i, f in enumerate(feats):
        f["foreign_suspect"] = bool(suspect[i])
        f["foreign_score"] = round(float(score[i] / smax), 3)
    return particles
