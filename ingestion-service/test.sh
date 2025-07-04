#!/bin/bash
set -e

echo "Running QueryLens Ingestion Service Tests"
echo "========================================"

cd "$(dirname "$0")/src"

echo "Running unit tests..."
python -m pytest test_unit.py -v

echo "Running integration tests..."
python -m pytest test_integration.py -v

echo "Running end-to-end tests..."
python -m pytest test_e2e.py -v

echo "All tests completed successfully!"