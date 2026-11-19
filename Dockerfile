# Node binary is needed by yt-dlp to solve YouTube's JavaScript challenges (Debian's apt node is too old).
FROM node:22-slim AS node

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DEBIAN_FRONTEND=noninteractive
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fontconfig fonts-noto-core fonts-noto-extra fonts-noto-color-emoji \
    curl ca-certificates \
    && fc-cache -f && rm -rf /var/lib/apt/lists/*
# Fail the build early if no Devanagari-capable font is installed (Hindi subtitles).
RUN fc-list :lang=hi | grep -qi devanagari || (echo "Devanagari font missing" && exit 1)
RUN echo "--js-runtimes node" > /etc/yt-dlp.conf
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/tmp /app/cache /app/data
EXPOSE 8080
CMD ["python", "bot.py"]
