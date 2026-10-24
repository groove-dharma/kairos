FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# Context de build: falsify/ (el padre de esta carpeta).
COPY black-boxer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# En el stack local el código va montado por volumen (ver override); estos
# COPY hacen la imagen autosuficiente y juntan en /app el par que
# black_boxer importa (querier.py) — igual que el montaje suelto del compose.
COPY black-boxer/black_boxer.py .
COPY querier/querier.py .
CMD ["python", "black_boxer.py"]
