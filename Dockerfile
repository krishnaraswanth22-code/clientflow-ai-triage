FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run defaults to port 8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn onboard_server:app --host 0.0.0.0 --port ${PORT:-8080}"]