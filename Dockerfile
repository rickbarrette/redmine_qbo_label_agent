FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fonts fonts
COPY label_agent label_agent

RUN useradd --system --no-create-home agent
USER agent

CMD ["python", "-m", "label_agent"]
