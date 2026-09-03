# Sanctum Forensics runtime + dev image.
# Built to be offline-repeatable: pinned base digest-free tag + pinned Python deps
# via constraints.txt. Apt gives us the forensic userland (sleuthkit, libewf,
# hdparm, nvme-cli) and the filesystem tools the erase/carve layers shell out to.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libtsk-dev \
        libewf-dev \
        sleuthkit \
        hdparm \
        nvme-cli \
        ntfs-3g \
        exfatprogs \
        dosfstools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first for cache reuse.
COPY pyproject.toml constraints.txt README.md ./
COPY core/ ./core/
COPY helper/ ./helper/
COPY api/ ./api/
COPY testkit/ ./testkit/

RUN pip install --constraint constraints.txt -e ".[dev]"

# Prove the two hard-to-build native deps actually import.
RUN python -c "import pytsk3, pyewf; print('pytsk3', pytsk3.get_version()); print('pyewf', pyewf.get_version())"

COPY . .

CMD ["python", "-c", "import core.models; print('sanctum image ok')"]
