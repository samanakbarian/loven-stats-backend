FROM mcr.microsoft.com/playwright/python:v1.54.0-jammy

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY scrape_official_rendered.py /app/scrape_official_rendered.py

CMD ["python", "/app/scrape_official_rendered.py"]
