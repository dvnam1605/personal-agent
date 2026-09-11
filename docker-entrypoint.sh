#!/bin/sh
set -e

# Run database schema migrations if DATABASE__URL is configured
if [ -n "$DATABASE__URL" ]; then
  echo "Applying database migrations with Alembic..."
  alembic upgrade head || {
    echo "Warning: Alembic migrations encountered an issue, proceeding to server startup..."
  }
fi

# Execute main process
echo "Starting Personal AI Assistant backend on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
