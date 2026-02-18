FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server/ /app/server/
COPY widget/ /app/widget/

EXPOSE 8000
CMD ["python", "server/main.py"]
