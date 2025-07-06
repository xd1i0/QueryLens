#!/bin/bash
set -e

echo "🚀 Setting up production-ready Elasticsearch..."

# Generate secure passwords
echo "📝 Generating secure passwords..."
ELASTICSEARCH_PASSWORD=$(openssl rand -base64 32)
JWT_SECRET=$(openssl rand -base64 48)

# Create root .env file for Docker Compose
echo "🔧 Creating root .env file..."
cat > .env << EOF
# Root environment file for Docker Compose
ELASTICSEARCH_PASSWORD=${ELASTICSEARCH_PASSWORD}
EOF

# Update ingestion service environment file
echo "🔧 Updating ingestion service environment..."

# Always recreate the .env file to avoid sed issues with special characters
cat > ingestion-service/.env << EOF
# Ingestion Service Environment Configuration
ELASTICSEARCH_HOST=https://elasticsearch:9200
ELASTICSEARCH_INDEX=docs
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=${ELASTICSEARCH_PASSWORD}
ELASTICSEARCH_VERIFY_CERTS=true
ELASTICSEARCH_CA_PATH=/app/certs/ca/ca.crt
ELASTICSEARCH_DISABLE_SSL_VERIFICATION=false
APP_HOST=0.0.0.0
APP_PORT=8000
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
CHUNK_MAX_TOKENS=250
CHUNK_OVERLAP=50
CHUNK_THRESHOLD_WORDS=500
APP_TITLE="QueryLens Ingestion & Search"
EOF

# Generate certificates
echo "🔐 Generating SSL certificates..."
./generate-certs.sh

# Save passwords securely
echo "💾 Saving passwords to secure file..."
cat > .env.secrets << EOF
# Secure password storage - keep this file safe!
ELASTICSEARCH_PASSWORD=${ELASTICSEARCH_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET}
EOF

chmod 600 .env.secrets

echo "✅ Production setup complete!"
echo "🔒 Passwords saved to .env.secrets (keep this file secure!)"
echo "🔑 Elasticsearch Password: ${ELASTICSEARCH_PASSWORD}"
echo "🔑 JWT Secret: ${JWT_SECRET}"
echo "🚀 You can now start the services with: docker-compose up -d"