FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py gestao.py conversa.py reposicao.py cenario.py assistente.js migration.sql index.html ./
COPY static ./static
COPY formas_assistente.py ./
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
