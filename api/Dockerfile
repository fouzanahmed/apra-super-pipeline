# Build from the repo root so requirements-api.txt is inside the build context:
#   docker build -f api/Dockerfile -t super-pipeline-api .
FROM python:3.12-slim
WORKDIR /app
COPY requirements-api.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
