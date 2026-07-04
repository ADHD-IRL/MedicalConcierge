FROM python:3.11-slim

WORKDIR /app

RUN groupadd -r medconcierge && useradd -r -g medconcierge medconcierge

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN mkdir -p /data && chown -R medconcierge:medconcierge /data /app
VOLUME ["/data"]

ENV DB_PATH=/data/medconcierge.sqlite3
EXPOSE 8000

USER medconcierge

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
