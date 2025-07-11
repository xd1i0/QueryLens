#!/bin/bash
set -e

echo "Running QueryLens Ingestion Service Tests"
echo "========================================"

# Set test environment variables for security configuration
export ELASTICSEARCH_HOST="${ELASTICSEARCH_HOST:-http://localhost:9200}"
export ELASTICSEARCH_USERNAME="${ELASTICSEARCH_USERNAME:-elastic}"
export ELASTICSEARCH_PASSWORD="${ELASTICSEARCH_PASSWORD:-changeme123!}"
export ELASTICSEARCH_VERIFY_CERTS="${ELASTICSEARCH_VERIFY_CERTS:-false}"
export ELASTICSEARCH_DISABLE_SSL_VERIFICATION="${ELASTICSEARCH_DISABLE_SSL_VERIFICATION:-true}"
export ELASTICSEARCH_INDEX="test_docs"

echo "Test Environment Configuration:"
echo "- ELASTICSEARCH_HOST: $ELASTICSEARCH_HOST"
echo "- ELASTICSEARCH_USERNAME: $ELASTICSEARCH_USERNAME"
echo "- ELASTICSEARCH_VERIFY_CERTS: $ELASTICSEARCH_VERIFY_CERTS"
echo "- ELASTICSEARCH_DISABLE_SSL_VERIFICATION: $ELASTICSEARCH_DISABLE_SSL_VERIFICATION"
echo "- ELASTICSEARCH_INDEX: $ELASTICSEARCH_INDEX"
echo ""

cd "$(dirname "$0")/src"

echo "Running unit tests..."
python -m pytest test_unit.py -v --tb=short

echo ""
echo "Running integration tests..."
python -m pytest test_integration.py -v --tb=short

echo ""
echo "Running end-to-end tests..."
python -m pytest test_e2e.py -v --tb=short

echo ""
echo "All tests completed successfully!"
