#!/bin/sh
set -e

# Configure host.docker.internal if WINDOWS_HOST_IP is supplied (fixes WSL2 docker bridge mismatch)
if [ -n "$WINDOWS_HOST_IP" ]; then
  echo "Configuring host.docker.internal to point to Windows host ($WINDOWS_HOST_IP)..."
  grep -v "host.docker.internal" /etc/hosts > /tmp/hosts 2>/dev/null || true
  cat /tmp/hosts > /etc/hosts 2>/dev/null || true
  echo "$WINDOWS_HOST_IP host.docker.internal" >> /etc/hosts 2>/dev/null || true
fi

# Wait for Docker DNS + Postgres before Alembic (avoids startup race).
wait_for_database() {
  db_url="$1"
  host="$(printf '%s' "$db_url" | sed -n 's|.*@\([^:/]*\).*|\1|p')"
  if [ -z "$host" ]; then
    echo "Warning: could not parse database host from DATABASE__URL"
    return 0
  fi

  echo "Waiting for database host '$host' to resolve..."
  i=0
  while [ "$i" -lt 30 ]; do
    if getent hosts "$host" >/dev/null 2>&1; then
      echo "Database host '$host' resolved."
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "Warning: database host '$host' did not resolve within 30s"
}

# Run database schema migrations if DATABASE__URL is configured
if [ -n "$DATABASE__URL" ]; then
  wait_for_database "$DATABASE__URL"
  echo "Applying database migrations with Alembic..."
  alembic upgrade head || {
    echo "Warning: Alembic migrations encountered an issue, proceeding to server startup..."
  }
fi

# Execute main process
echo "Starting Personal AI Assistant backend on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload "$@"
