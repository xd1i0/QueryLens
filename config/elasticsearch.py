import requests
import logging
from urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings for development
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = logging.getLogger(__name__)

def test_elasticsearch_connection(
    host: str = "localhost",
    port: int = 9200,
    username: str = "elastic",
    password: str = "changeme123!"
) -> bool:
    """Test connection to Elasticsearch with HTTPS and authentication"""
    try:
        # Use HTTPS as configured in docker-compose.yml
        url = f"https://{host}:{port}"

        # Use basic auth and disable SSL verification for development
        response = requests.get(
            url,
            auth=(username, password),
            verify=False,  # Disable SSL verification for self-signed certs
            timeout=30
        )

        if response.status_code == 200:
            logger.info("Elasticsearch connection successful")
            return True
        else:
            logger.error(f"Elasticsearch connection failed: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Elasticsearch connection error: {e}")
        return False

def get_elasticsearch_config() -> dict:
    """Get Elasticsearch configuration matching docker-compose.yml"""
    return {
        "host": "localhost",
        "port": 9200,
        "index_prefix": "querylens",
        "timeout": 30,
        "username": "elastic",
        "password": "changeme123!",
        "use_ssl": True,
        "verify_certs": False  # For development with self-signed certificates
    }