FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

# 用 shell 形式让 $PORT 生效
CMD gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 app:app
