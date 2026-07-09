FROM python:3.12-slim as builder

WORKDIR /code

# Install build dependencies for libraries requiring compilation (e.g. greenlet, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim as runner

WORKDIR /code

# Copy installed site-packages from builder stage
COPY --from=builder /root/.local /root/.local
COPY . /code

# Make sure scripts in .local/bin are executable and on PATH
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Run FastAPI with Uvicorn in production mode
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
