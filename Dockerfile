FROM python:3.12-slim

WORKDIR /app

# System deps for numpy/pandas/sklearn
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create dirs for state files and logs
RUN mkdir -p logs /app/.streamlit

# Make start script executable
RUN chmod +x /app/start.sh

# Paper trading is default — no secrets needed for basic operation
# Secrets (API keys, Telegram, ETH wallet) are injected via Railway env vars

EXPOSE 8501

# Railway sets $PORT dynamically; Streamlit must listen on it
# We override in the CMD via env var
ENV STREAMLIT_SERVER_PORT=8501

CMD ["./start.sh"]
