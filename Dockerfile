FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    WEB_PORT=8765 \
    VNC_PORT=5900 \
    NOVNC_PORT=7900 \
    HEADLESS=false \
    CHROME_BINARY=/usr/bin/chromium \
    CHROME_EXTRA_ARGS="--no-sandbox||--disable-dev-shm-usage||--disable-gpu||--window-size=1440,1100" \
    COMMENT_TEXT_INPUT_MODE=paste \
    COMMENT_IMAGE_ATTACH_MODE=auto \
    KEEP_BROWSER_OPEN=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        chromium-driver \
        fluxbox \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        novnc \
        procps \
        websockify \
        xclip \
        xdotool \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN mkdir -p generated output chrome-profiles \
    && chmod +x docker-entrypoint.sh

EXPOSE 8765 5900 7900
CMD ["./docker-entrypoint.sh"]
