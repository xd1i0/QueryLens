#!/bin/bash

# Create certs directory if it doesn't exist
mkdir -p certs

echo "🔧 Generating Elasticsearch certificates..."

# Clean up any existing certificates
rm -rf certs/*

# Generate CA and node certificates using Elasticsearch Docker image
docker run --rm -v $PWD/certs:/certs docker.elastic.co/elasticsearch/elasticsearch:8.8.2 \
  sh -c "
    # Generate CA certificate
    bin/elasticsearch-certutil ca --silent --out /certs/ca.p12 --pass '' && \

    # Generate node certificate
    bin/elasticsearch-certutil cert --silent --ca /certs/ca.p12 --ca-pass '' --out /certs/elastic-certificates.p12 --pass '' && \

    # Generate PEM format certificates for easier use
    bin/elasticsearch-certutil ca --silent --pem --out /certs/ca.zip --pass '' && \
    cd /certs && unzip -o ca.zip && \

    # Generate node certificates in PEM format
    bin/elasticsearch-certutil cert --silent --ca /certs/ca.p12 --ca-pass '' --pem --out /certs/certs.zip --pass '' && \
    cd /certs && unzip -o certs.zip
  "

# Set appropriate permissions - make readable by all users
chmod 755 certs/
chmod 644 certs/*.p12 2>/dev/null || true
chmod 644 certs/*.crt 2>/dev/null || true
chmod 644 certs/*.key 2>/dev/null || true
chmod 755 certs/ca/ 2>/dev/null || true
chmod 644 certs/ca/* 2>/dev/null || true

echo "✅ Certificates generated successfully in ./certs/"
echo "Files created:"
ls -la certs/
if [ -d "certs/ca" ]; then
    echo "CA directory contents:"
    ls -la certs/ca/
fi
