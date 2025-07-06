#!/bin/bash

set -e

echo "🔧 Setting up secure Elasticsearch for QueryLens MVP..."

# Make sure we're in the project root
cd "$(dirname "$0")/.."

# Step 1: Generate certificates
echo "📜 Generating certificates..."
chmod +x generate-certs.sh
./generate-certs.sh

# Step 2: Create config directory
echo "📁 Creating config directory..."
mkdir -p config

# Step 3: Load environment variables
echo "🔑 Loading environment variables..."
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Step 4: Fix local certificate permissions for Docker mounting
echo "🔧 Fixing certificate permissions for Docker..."

# Ensure certs directory exists locally
mkdir -p certs/elasticsearch

# Fix permissions on local certificate files
chmod -R 755 certs/
chmod 644 certs/ca/ca.crt certs/ca/ca.key 2>/dev/null || true
chmod 644 certs/elastic-certificates.p12 2>/dev/null || true

# Clean up any existing elasticsearch directory with wrong permissions
if [ -d "certs/elasticsearch" ]; then
    chmod -R 755 certs/elasticsearch/ 2>/dev/null || sudo rm -rf certs/elasticsearch/
fi

# Step 5: Start Elasticsearch
echo "🚀 Starting Elasticsearch..."
docker-compose up -d elasticsearch

# Step 6: Wait for Elasticsearch to be ready
echo "⏳ Waiting for Elasticsearch to be ready..."
sleep 30

# Step 7: Test connection
echo "🧪 Testing connection..."
python -c "
import sys
sys.path.append('src')
from config.elasticsearch import test_elasticsearch_connection
if not test_elasticsearch_connection():
    sys.exit(1)
"

echo "✅ Elasticsearch setup complete!"
echo ""
echo "🔐 Security Information:"
echo "- Elasticsearch is running with TLS enabled"
echo "- Default credentials: elastic / changeme123!"
echo "- Access via: https://localhost:9200"
echo ""
echo "🧪 Test manually:"
echo "curl --cacert certs/ca/ca.crt -u elastic:changeme123! https://localhost:9200/_cluster/health"
echo ""
echo "⚠️  IMPORTANT: These are MVP credentials. Migrate to proper secrets management for production!"