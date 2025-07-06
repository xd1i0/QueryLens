# QueryLens
A hybrid search service that ingests documents (PDFs, Markdown, tickets, specs), indexes both keyword and semantic vectors, and serves ultra‐fast, high-precision results via a unified API.

## Security Setup (MVP)

QueryLens uses secure-by-default Elasticsearch with TLS and authentication enabled.

### Quick Setup

```bash
# Generate certificates and start secure Elasticsearch
./scripts/setup-elasticsearch.sh
```

### Manual Setup

1. **Generate certificates:**
   ```bash
   ./generate-certs.sh
   ```

2. **Start services:**
   ```bash
   docker-compose up -d
   ```

3. **Test connection:**
   ```bash
   curl -k -u elastic:changeme123! https://localhost:9200/_cluster/health
   ```

### Security Configuration

- **TLS**: All communication encrypted with self-signed certificates
- **Authentication**: Basic auth with `elastic` user
- **Credentials**: Stored in `.env` file (MVP only)

### ⚠️ Production Migration

Current setup is MVP-friendly but NOT production-ready:

- [ ] Migrate to proper PKI or managed CA (AWS ACM)
- [ ] Use HashiCorp Vault or AWS Secrets Manager
- [ ] Implement least-privilege roles
- [ ] Enable audit logging
- [ ] Network security (VPC, firewall rules)
