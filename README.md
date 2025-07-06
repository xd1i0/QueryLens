# QueryLens
A hybrid search service that ingests documents (PDFs, Markdown, tickets, specs), indexes both keyword and semantic vectors, and serves ultra‐fast, high-precision results via a unified API.

## Project Structure

```
QueryLens/
├── ingestion-service/          # FastAPI-based document ingestion service
│   ├── src/                   # Source code
│   ├── Dockerfile             # Container configuration
│   └── requirements.txt       # Python dependencies
├── docker-compose.yml         # Multi-service orchestration
├── .env.secrets              # Secure credentials (keep safe!)
├── scripts/                   # Setup and deployment scripts
│   ├── setup-production.sh    # Production deployment script
│   └── setup-elasticsearch.sh # Development setup script
└── .github/workflows/        # CI/CD automation
```

## Quick Start

### Development Setup

1. **Clone and setup:**
   ```bash
   git clone <repository-url>
   cd QueryLens
   ```

2. **Start services:**
   ```bash
   docker-compose up -d
   ```

3. **Test the API:**
   ```bash
   curl http://localhost:8000/
   ```

### Production Setup

1. **Run production setup script:**
   ```bash
   chmod +x scripts/setup-production.sh
   ./scripts/setup-production.sh
   ```

2. **Start production services:**
   ```bash
   docker-compose up -d
   ```

3. **Test secure connection:**
   ```bash
   curl -k https://localhost:9200/_cluster/health
   ```

The production setup script automatically:
- Generates cryptographically secure passwords
- Creates proper SSL certificates
- Configures TLS encryption for Elasticsearch
- Sets up JWT authentication
- Saves credentials securely in `.env.secrets`

## Security Configuration

### Environment Variables

Sensitive credentials are stored in `.env.secrets`:
- `ELASTICSEARCH_PASSWORD`: Secure Elasticsearch authentication
- `JWT_SECRET_KEY`: API authentication token

### Authentication

The service supports JWT-based authentication:
- **Development**: Optional authentication for easier testing
- **Production**: Required JWT tokens for all endpoints

### Elasticsearch Security

- **Development**: Security disabled for CI/CD compatibility
- **Production**: TLS encryption and authentication enabled

## Development

### Running Tests

```bash
# Unit tests
cd ingestion-service/src
python -m pytest test_unit.py -v

# Integration tests  
python -m pytest test_integration.py -v

# End-to-end tests
python -m pytest test_e2e.py -v
```

### System Requirements

- Python 3.11 or 3.12
- Docker and Docker Compose
- System dependencies: libmagic, antiword, poppler-utils, tesseract-ocr

### CI/CD Pipeline

GitHub Actions automatically:
- Tests across Python 3.11 and 3.12
- Runs unit, integration, and E2E tests
- Validates Docker builds and deployments
- Tests both authenticated and fallback modes

## API Endpoints

- `GET /` - Health check
- `POST /docs/` - Index documents
- `GET /search/` - Search documents
- `GET /docs/{id}` - Retrieve specific document
- `POST /auth/token` - Get authentication token

## ⚠️ Production Considerations

Current setup provides production-ready security but consider these additional hardening steps:

- [ ] Use external secret management (HashiCorp Vault, AWS Secrets Manager)
- [ ] Add rate limiting and monitoring
- [ ] Implement proper logging and audit trails
- [ ] Network security (VPC, firewall rules)
- [ ] Database backup and disaster recovery
- [ ] Certificate management and renewal
```

The key addition is the **Production Setup** section that shows how to use the `setup-production.sh` script, which automatically handles secure password generation, SSL certificate creation, and proper environment configuration for production deployment.
