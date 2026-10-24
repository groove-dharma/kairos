FROM python:3.13-slim
WORKDIR /app

# AG2 (runtime del debate) + cliente MCP (lectura read-only de Fortemi)
# + httpx (tools custom) + psycopg2 (escritura SQL directa sin cascada:
# e2_parser y fusionar_hipotesis escriben en note_original sin encolar jobs)
RUN pip install --no-cache-dir 'ag2[openai]' mcp httpx psycopg2-binary

# GTFlow (etapas 1-5, importadas por piloto.py) + requests (cliente REST)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir requests git+https://github.com/zw-zhtlab/GTFlow.git
