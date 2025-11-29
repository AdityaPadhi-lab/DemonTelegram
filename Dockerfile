FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg gcc libc-dev tzdata git && \
    rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Kolkata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "RestrictedContentDL/main2.py"]