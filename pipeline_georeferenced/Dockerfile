FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE $PORT

# Shell form (not exec/JSON form) so $PORT is actually expanded at container
# start -- cloud platforms (Render, Cloud Run, etc.) inject PORT at runtime,
# and exec-form CMD would pass the literal string "$PORT" to uvicorn instead.
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
