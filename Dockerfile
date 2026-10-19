FROM python:3.11-slim

WORKDIR /app

# Instalar dependências de sistema necessárias ao PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    mupdf-tools \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependências Python (wheels compatíveis com linux/arm64 e linux/amd64)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação e utilitário CLI
COPY app/ ./app/
COPY gerenciar_usuarios.py ./

# Criar diretórios persistentes e usuário sem privilégios (UID 1000 / GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -m -u 1000 -g 1000 appuser && \
    mkdir -p /app/data/uploads /app/app/uploads && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/app/static && \
    chmod -R 775 /app/data /app/app/uploads

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
