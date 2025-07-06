#!/bin/bash

# Create certs directory structure
mkdir -p certs/ca

# Generate CA private key
openssl genrsa -out certs/ca/ca.key 4096

# Generate CA certificate
openssl req -new -x509 -days 365 -key certs/ca/ca.key -out certs/ca/ca.crt -subj "/C=US/ST=State/L=City/O=Organization/CN=ElasticCA"

# Generate instance private key
openssl genrsa -out certs/instance.key 4096

# Generate certificate signing request
openssl req -new -key certs/instance.key -out certs/instance.csr -subj "/C=US/ST=State/L=City/O=Organization/CN=elasticsearch"

# Generate instance certificate signed by CA
openssl x509 -req -in certs/instance.csr -CA certs/ca/ca.crt -CAkey certs/ca/ca.key -CAcreateserial -out certs/instance.crt -days 365

# Clean up CSR file
rm certs/instance.csr

# Set proper permissions
chmod 644 certs/ca/ca.crt certs/instance.crt
chmod 600 certs/ca/ca.key certs/instance.key

echo "SSL certificates generated successfully!"