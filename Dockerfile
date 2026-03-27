FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Copy data files (bundled during deploy)
COPY deploy_data/ /app/data_files/

# Streamlit config
RUN mkdir -p ~/.streamlit && \
    printf '[server]\nheadless = true\nport = 8080\nenableCORS = false\nenableXsrfProtection = false\nmaxUploadSize = 200\n\n[browser]\ngatherUsageStats = false\n' > ~/.streamlit/config.toml

EXPOSE 8080

ENV DEPLOY_MODE=cloud
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.headless=true"]
