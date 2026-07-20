"""
Django settings for the GrainVision AI Data Annotation Platform.

Designed for OFFLINE / air-gapped deployment:
  * SQLite by default (zero external service); PostgreSQL optional via env.
  * WhiteNoise serves static assets — no CDN, no external fonts.
  * SAM2 runs locally from a checkpoint on disk (ml_models/).
  * Local filesystem media storage by default; MinIO/S3 optional via env.
"""
import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "dev-insecure-change-me-before-production"),
    ALLOWED_HOSTS=(list, ["*"]),
    USE_POSTGRES=(bool, False),
    USE_S3=(bool, False),
    SAM2_ENABLED=(bool, True),
    PLATE_INNER_DIAMETER_MM=(float, 300.0),
    SEG_WORKING_MAX_SIDE=(int, 1100),
)

# Read .env if present (never required for the app to boot).
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# ── Core ──────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # local apps
    "accounts",
    "core",
    "annotation",
    "qc",
    "dashboard",
    "ml",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "grainvision.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.platform_context",
            ],
        },
    },
]

WSGI_APPLICATION = "grainvision.wsgi.application"
ASGI_APPLICATION = "grainvision.asgi.application"

# ── Database ──────────────────────────────────────────────────────
if env("USE_POSTGRES"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", default="grainvision"),
            "USER": env("POSTGRES_USER", default="grainvision"),
            "PASSWORD": env("POSTGRES_PASSWORD", default="grainvision"),
            "HOST": env("POSTGRES_HOST", default="127.0.0.1"),
            "PORT": env("POSTGRES_PORT", default="5432"),
            "OPTIONS": {"sslmode": env("POSTGRES_SSLMODE", default="prefer")},
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # Override with SQLITE_PATH to keep the DB outside the deploy folder
            # (e.g. /home/data/grainvision.sqlite3 on Azure App Service) so it
            # survives redeploys.
            "NAME": env("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
        }
    }

# ── Auth ──────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Account lockout (PRD §13.1) is enforced in accounts.backends.
AXES_FAILURE_LIMIT = 5
AXES_LOCKOUT_MINUTES = 30

# bcrypt cost factor 12 (PRD §13.1)
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# Session expiry tuned to mirror the JWT access-token window (PRD §13.1).
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8h working day; refresh on activity
SESSION_SAVE_EVERY_REQUEST = True

# ── i18n ──────────────────────────────────────────────────────────
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ── Static & media ────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
# Override with MEDIA_ROOT to keep uploads outside the deploy folder
# (e.g. /home/data/media on Azure App Service) so they survive redeploys.
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Serve user media through Django in production when not using object storage
# (e.g. local filesystem or an Azure Files mount). Login-required, see urls.py.
USE_S3 = env("USE_S3")
SERVE_MEDIA = env.bool("SERVE_MEDIA", default=True)

if env("USE_S3"):
    # MinIO / S3-compatible. PRD §13.2: AES-256, pre-signed URLs.
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_S3_ENDPOINT_URL = env("S3_ENDPOINT_URL")
    AWS_ACCESS_KEY_ID = env("S3_ACCESS_KEY")
    AWS_SECRET_ACCESS_KEY = env("S3_SECRET_KEY")
    AWS_STORAGE_BUCKET_NAME = env("S3_BUCKET", default="grainvision")
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_EXPIRE = 900  # 15-min pre-signed URLs
    AWS_S3_OBJECT_PARAMETERS = {"ServerSideEncryption": "AES256"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── DRF ───────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ── SAM2 segmentation (PRD §6) ────────────────────────────────────
# SAM2 is the segmentation engine. With a GPU (SAM2_DEVICE=cuda) the default
# dense point grid runs in seconds; on CPU lower SAM2_POINTS_PER_SIDE.
SAM2_ENABLED = env.bool("SAM2_ENABLED", default=True)
# When True (default) and SAM2 is enabled, a load failure raises a clear
# configuration error instead of silently degrading. Set False to allow the
# classical OpenCV watershed engine to stand in.
SAM2_REQUIRED = env.bool("SAM2_REQUIRED", default=True)
SAM2_CHECKPOINT = env("SAM2_CHECKPOINT", default=str(BASE_DIR / "ml_models" / "sam2.1_hiera_small.pt"))
SAM2_MODEL_CFG = env("SAM2_MODEL_CFG", default="configs/sam2.1/sam2.1_hiera_s.yaml")
SAM2_DEVICE = env("SAM2_DEVICE", default="cuda")  # "cpu" if no GPU is present

# AutomaticMaskGenerator tuning. The defaults suit a GPU; on CPU set
# SAM2_POINTS_PER_SIDE=16–24 to keep latency reasonable.
SAM2_POINTS_PER_SIDE = env.int("SAM2_POINTS_PER_SIDE", default=64)
SAM2_POINTS_PER_BATCH = env.int("SAM2_POINTS_PER_BATCH", default=128)
SAM2_PRED_IOU_THRESH = env.float("SAM2_PRED_IOU_THRESH", default=0.80)
SAM2_STABILITY_SCORE_THRESH = env.float("SAM2_STABILITY_SCORE_THRESH", default=0.90)
SAM2_BOX_NMS_THRESH = env.float("SAM2_BOX_NMS_THRESH", default=0.40)
SAM2_MIN_MASK_REGION_AREA = env.int("SAM2_MIN_MASK_REGION_AREA", default=50)
# Crop layers run SAM2 again on zoomed-in sub-crops — the key lever for catching
# many small, dense objects (e.g. a full plate of grains). 0=off (fast),
# 1=one extra zoom pass (much higher recall, slower). GPU strongly recommended >0.
SAM2_CROP_N_LAYERS = env.int("SAM2_CROP_N_LAYERS", default=0)
SAM2_CROP_DOWNSCALE = env.int("SAM2_CROP_DOWNSCALE", default=2)

# Tiled segmentation: run SAM2 over overlapping tiles for dense per-grain recall
# (the right setting for a full plate of grains). Needs a GPU to be fast.
SAM2_TILES = env.bool("SAM2_TILES", default=False)
SAM2_TILE_SIZE = env.int("SAM2_TILE_SIZE", default=0)   # 0 = auto (~3-4 tiles/side)

# Offload ONLY segmentation to a remote GPU worker (kept scale-to-zero on Azure
# so you pay GPU rates only while segmenting). When set, the web app needs no
# torch/SAM2 itself. The worker runs tiled SAM2 and returns polygons.
SAM2_REMOTE_URL = env("SAM2_REMOTE_URL", default="")
SAM2_REMOTE_TIMEOUT = env.int("SAM2_REMOTE_TIMEOUT", default=120)
# Shared secret sent as X-Worker-Token so the public GPU worker rejects
# unauthenticated callers. Must match the worker's WORKER_TOKEN.
SAM2_REMOTE_TOKEN = env("SAM2_REMOTE_TOKEN", default="")

# ── Security hardening (PRD §13) ──────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ── Logging ───────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "verbose"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024  # 15 MB (12MP JPEG ~4MB + headroom)
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024


# ── Physical scale calibration ────────────────────────────────────
# Diameter (mm) of the standard 30 cm sampling plate, measured at the INNER
# edge of the blue rim — where the white surface starts, since that is the
# circle the scale is computed from. IMPORTANT: measure the real plate's inner
# white circle with a tape (typically slightly less than the 300 mm outer
# edge) and set PLATE_INNER_DIAMETER_MM in .env. Every capture uses the same
# plate, so the detected rim gives px-per-mm for each image — the key to
# camera-independent pixel→weight training data.
PLATE_INNER_DIAMETER_MM = env("PLATE_INNER_DIAMETER_MM")
# Segmentation runs on a downscaled working copy (speed); annotations are
# scaled back and stored at the ORIGINAL upload resolution.
SEG_WORKING_MAX_SIDE = env("SEG_WORKING_MAX_SIDE")
