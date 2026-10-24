FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Copy dependency specifications first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project scripts and files
COPY . .

# Expose Cloud Run default port
EXPOSE 8080

# Execute Uvicorn via Python module to ensure path resolution
CMD ["sh", "-c", "python -m uvicorn onboard_server:app --host 0.0.0.0 --port ${PORT:-8080}"]