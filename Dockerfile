FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 MPLBACKEND=Agg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/qq-bot
COPY requirements.txt requirements-local.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/requirements-local.txt && python -m playwright install --with-deps chromium
# Mount source and private configuration at runtime; never bake credentials into images.
CMD ["python", "qq_bot_core.py"]
