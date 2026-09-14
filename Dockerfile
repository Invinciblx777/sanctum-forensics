# Sanctum Forensics runtime + dev image.
#
# Built to be offline-repeatable once built: pinned base tags and pinned Python
# deps via constraints.txt, an `npm ci` against the committed lockfile, and no
# network access needed at run time.
#
# Two things this image used to get wrong, both of which made the shipped
# container not the product:
#
#   1. It did not build the UI. `ui/dist/` is gitignored, so a clean clone has
#      no bundle, no stage produced one, and `api/main.py` mounted nothing -
#      the "container deliverable" in README.md was a headless API. Stage 1
#      below builds it and stage 2 copies it to the path `UI_DIST` names.
#   2. It installed the stock `libewf-python`, which reads E01 and **cannot
#      write one** - the failure constraints.txt warns about in writing, silent
#      until the first acquisition to E01. `scripts/build-libewf-python.sh`
#      exists to fix exactly that and was never run here; it is run below, and
#      its own verification step round-trips a 1 MiB E01 during the build, so
#      an image that cannot write E01 fails to build rather than shipping.

# --------------------------------------------------------------------------
# Stage 1 - the UI bundle
# --------------------------------------------------------------------------
FROM node:22-slim AS ui

WORKDIR /ui

# The lockfile alone first, so a source-only change does not reinstall
# node_modules. `npm ci` and not `npm install`: it installs the lockfile
# exactly, and a build that resolved versions afresh at image-build time is not
# the bundle anybody tested.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

# The bundle must carry no external origin: the tool is used on an evidence
# network, and a font or script fetched from a CDN at load time is both an
# offline failure and a disclosure. Checked here so the *image* cannot ship one
# even if a source change introduces it.
RUN ! grep -rIlE 'https?://(?!127\.0\.0\.1|localhost)' dist/ --include='*.js' \
        --include='*.css' --include='*.html' -P \
    || (echo "the UI bundle references an external origin" >&2; exit 1)

# --------------------------------------------------------------------------
# Stage 2 - the application
# --------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Apt gives us the forensic userland (sleuthkit, libewf, hdparm, nvme-cli) and
# the filesystem tools the erase/carve layers shell out to. zlib1g-dev and
# libbz2-dev are for the libewf-python rebuild below: without real zlib headers
# libewf compiles its local deflate and refuses write access.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libtsk-dev \
        libewf-dev \
        zlib1g-dev \
        libbz2-dev \
        sleuthkit \
        hdparm \
        nvme-cli \
        ntfs-3g \
        exfatprogs \
        dosfstools \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first for cache reuse.
COPY pyproject.toml constraints.txt README.md ./
COPY core/ ./core/
COPY helper/ ./helper/
COPY api/ ./api/
COPY testkit/ ./testkit/

RUN pip install --constraint constraints.txt -e ".[dev]"

# Replace the pip-installed libewf-python with one that can write E01. The
# script takes the interpreter prefix from $VENV, so pointing it at
# /usr/local uses this image's own python rather than a virtualenv.
COPY scripts/build-libewf-python.sh ./scripts/
RUN VENV=/usr/local ./scripts/build-libewf-python.sh

# Prove the two hard-to-build native deps actually import, and that E01 write
# survived into this layer rather than only into the build script's.
RUN python -c "import pytsk3, pyewf; print('pytsk3', pytsk3.get_version()); print('pyewf', pyewf.get_version())" \
 && python -c "from core.carve.acquire import e01_write_supported; assert e01_write_supported(), 'E01 write not available'; print('e01_write_supported True')"

COPY . .

# The bundle from stage 1, at the path api/main.py:49 looks for. Copied after
# `COPY . .` so a stale host-built ui/dist cannot win.
COPY --from=ui /ui/dist ./ui/dist

# The API serves on 127.0.0.1 only, by design (api/main.py:45). Reach it with
# `podman run --network host` / `docker run --network host`, or exercise it from
# inside the container; publishing a port would require binding 0.0.0.0, which
# is the one thing this application refuses to do.
EXPOSE 8787
CMD ["python", "-m", "api.main"]
