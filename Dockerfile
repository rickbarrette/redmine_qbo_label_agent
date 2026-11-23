FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
# Ready-made wheels where there are some (PCs, 64-bit Pis). 32-bit Pis (arm/v7) have no Pillow wheel: it's
# compiled there, with build tools that are removed afterwards. libjpeg and FreeType stay: Pillow needs them
# to run, and FreeType draws the labels' text
RUN if ! pip install --no-cache-dir --only-binary=pillow -r requirements.txt; then \
      apt-get update && \
      apt-get install -y --no-install-recommends build-essential libjpeg62-turbo-dev zlib1g-dev libfreetype-dev \
        libjpeg62-turbo libfreetype6 && \
      pip install --no-cache-dir -r requirements.txt && \
      apt-get purge -y --auto-remove build-essential libjpeg62-turbo-dev zlib1g-dev libfreetype-dev && \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY fonts fonts
COPY label_agent label_agent

RUN useradd --system --no-create-home agent
USER agent

CMD ["python", "-m", "label_agent"]
