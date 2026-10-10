# One container serves the API and the static site in web/ from the same origin.
# Build from the repository root: docker build -t debate-arena .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /repo
COPY server/requirements.txt server/requirements.txt
COPY softskills-grader/requirements.txt softskills-grader/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt -r softskills-grader/requirements.txt
COPY . .
# Grader cache and temporary recordings. Mount a persistent volume here in production.
ENV DATA_DIR=/data
EXPOSE 8000
# One worker: grading runs as in-process background tasks that share the local cache.
CMD ["python", "-m", "uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
