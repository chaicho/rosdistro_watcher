FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV PYTHONPATH=/workspace \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    MPLBACKEND=Agg \
    HF_HOME=/opt/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/opt/huggingface/hub

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY rosdep_auditor/requirements.txt /tmp/requirements.txt
COPY docker/artifact-requirements.txt /tmp/artifact-requirements.txt
COPY docker/constraints-cpu.txt /tmp/constraints-cpu.txt
COPY docker/constraints-versions.txt /tmp/constraints-versions.txt
RUN uv pip install \
    --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --constraint /tmp/constraints-cpu.txt \
    --constraint /tmp/constraints-versions.txt \
    -r /tmp/requirements.txt \
    -r /tmp/artifact-requirements.txt

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /home/appuser/cache/rosdistro_watcher /opt/huggingface /workspace \
    && chown -R appuser:appuser /home/appuser/cache /opt/huggingface /workspace

USER appuser
ENV TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 TOOL_PATH=/workspace

# External caches come in via --build-context (see docker/publish-artifact-image.sh).
COPY --from=rosdistro-cache --chown=appuser:appuser . /home/appuser/cache/rosdistro_watcher/
COPY --from=hf-cache        --chown=appuser:appuser . /opt/huggingface/
COPY --chown=appuser:appuser . /workspace

CMD ["/bin/bash"]
