FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# El stack monta ./querier sobre /app; este COPY es solo para
# poder correr la imagen suelta sin volumen.
COPY querier.py .
ENTRYPOINT ["python", "querier.py"]
