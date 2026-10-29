# One process: the send_issue_email MCP. arko-chat-agent calls POST / on this port.
# Secrets stay out of the image; Kubernetes injects them at runtime.
FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765 \
    MCP_PATH=/

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mcp \
    && mkdir -p /app/log \
    && chown mcp:mcp /app/log
USER mcp

EXPOSE 8765
CMD ["python", "server.py"]
