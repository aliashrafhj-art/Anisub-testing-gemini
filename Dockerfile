FROM python:3.10-slim

# সিস্টেম প্যাকেজ ইনস্টল (FFmpeg + Bengali Font Support)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libass-dev \
    fontconfig \
    fonts-noto-bengali \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

CMD ["python", "app.py"]
