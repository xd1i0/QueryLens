#!/bin/bash
set -e

echo "Running embedding-service unit tests..."
pytest --maxfail=1 --disable-warnings -v /Users/daniel/PycharmProjects/QueryLens/embedding-service
