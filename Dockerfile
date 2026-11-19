FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-noto-core fonts-noto-extra fonts-noto-color-emoji \
    curl ca-certificates \
    && fc-cache -fv && rm -rf /var/lib/apt/lists/*
RUN test -f /usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf \
    || (echo "Devanagari font missing" && exit 1)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --upgrade yt-dlp
COPY . .
RUN mkdir -p /app/tmp /app/cache /app/data
EXPOSE 8080
CMD ["python", "bot.py"]