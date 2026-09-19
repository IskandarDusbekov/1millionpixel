FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY frontend/ /frontend/

# Statik fayllarni yig'amiz. collectstatic sozlamalarni o'qiydi, lekin
# bu bosqichda Postgres/Redis yo'q — shuning uchun vaqtincha SQLite.
RUN USE_SQLITE=1 SECRET_KEY=build DEBUG=0 \
    python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
