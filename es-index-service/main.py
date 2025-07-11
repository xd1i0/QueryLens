import os
import asyncio
import json
import time
import logging
from aiokafka import AIOKafkaConsumer
from elasticsearch import AsyncElasticsearch, helpers, exceptions
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from elastic_transport import ConnectionError as ESConnectionError
from elasticsearch import BadRequestError
from aiokafka.errors import GroupCoordinatorNotAvailableError

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
RAW_TOPIC       = "raw-chunks"

ES_HOST         = os.getenv("ES_HOST", "http://elasticsearch:9200")
ES_INDEX        = os.getenv("ES_INDEX", "docs")
BULK_BATCH_SIZE = int(os.getenv("BULK_BATCH_SIZE", "200"))

# ── Index Mapping ──────────────────────────────────────────────────────────────
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "id":            {"type": "keyword"},
            "title":         {"type": "text",    "analyzer": "standard"},
            "content":       {"type": "text",    "analyzer": "standard"},
            "tags":          {"type": "keyword"},
            "author":        {"type": "keyword"},
            "source_system": {"type": "keyword"},
            "timestamp":     {"type": "date"}
        }
    }
}

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    retry=retry_if_exception_type(ESConnectionError),
    reraise=True
)
async def ensure_index(es: AsyncElasticsearch, index_name: str = ES_INDEX):
    """
    Check for existence of `index_name` and create it with INDEX_MAPPING if missing.
    Retries on connection errors.
    """
    try:
        # Elasticsearch index name rules:
        # - must be lowercase
        # - cannot start with '_', '-', '+'
        # - valid chars: a-z, 0-9, -, _, +
        # - cannot be '.' or '..'
        # - length 1-255
        if (
            not index_name
            or not (1 <= len(index_name) <= 255)
            or index_name in {".", ".."}
            or index_name[0] in "_-+"
            or not all(c.islower() or c.isdigit() or c in "-_+" for c in index_name)
        ):
            logger.error(f"Invalid Elasticsearch index name: '{index_name}'. Must be lowercase, digits, '-', '_', '+', not start with '_', '-', '+', and not '.' or '..'.")
            raise ValueError(f"Invalid Elasticsearch index name: '{index_name}'")

        logger.info(f"ES_HOST: {ES_HOST}, Checking existence of index: '{index_name}'")
        try:
            exists = await es.indices.exists(index=index_name)
        except BadRequestError as bre:
            logger.error(f"BadRequestError when checking index '{index_name}': {bre.info if hasattr(bre, 'info') else bre}")
            # Optionally, try to delete and recreate the index (dev only)
            try:
                await es.indices.delete(index=index_name, ignore_unavailable=True)
                logger.warning(f"Deleted possibly corrupt index '{index_name}', will attempt to recreate.")
            except Exception as delete_exc:
                logger.error(f"Failed to delete index '{index_name}': {delete_exc}")
            exists = False

        if not exists:
            try:
                logger.info(f"Creating index '{index_name}' with mapping: {json.dumps(INDEX_MAPPING)}")
                await es.indices.create(index=index_name, mappings=INDEX_MAPPING["mappings"])
                logger.info(f"Created Elasticsearch index '{index_name}' with initial mapping.")
            except BadRequestError as ce:
                logger.error(f"BadRequestError creating index '{index_name}': {ce.info if hasattr(ce, 'info') else ce}")
                raise
            except Exception as ce:
                logger.error(f"Failed to create index '{index_name}' with mapping {json.dumps(INDEX_MAPPING)}: {ce}")
                raise
        else:
            logger.info(f"Elasticsearch index '{index_name}' already exists; skipping mapping creation.")
    except ESConnectionError as e:
        logger.warning(f"Connection error ensuring index '{index_name}': {e}. Retrying...")
        raise
    except Exception as e:
        logger.error(f"Error ensuring index '{index_name}': {e}")
        raise

# ── ES Client ──────────────────────────────────────────────────────────────────
es = AsyncElasticsearch([ES_HOST])

consumer: AIOKafkaConsumer = None
worker_task: asyncio.Task = None

async def es_kafka_worker():
    global consumer
    batch = []
    offsets = []
    message_count = 0
    try:
        await ensure_index(es, ES_INDEX)
        logging.info(f"[Kafka] Worker started for topic '{RAW_TOPIC}' on '{KAFKA_BOOTSTRAP}'")
        while True:
            try:
                async for msg in consumer:
                    logging.info(f"[Kafka] Consumed message at offset {msg.offset} from partition {msg.partition}")
                    message_count += 1
                    data = json.loads(msg.value)
                    meta = data.get("metadata", {})

                    doc = {
                        "id":            data.get("doc_id"),
                        "title":         meta.get("title"),
                        "content":       data.get("text"),
                        "tags":          meta.get("tags", []),
                        "author":        meta.get("author"),
                        "source_system": meta.get("source_system"),
                        "timestamp":     meta.get("timestamp"),
                    }

                    action = {
                        "_op_type": "index",
                        "_index":   ES_INDEX,
                        "_id":      data.get("chunk_id") or data.get("doc_id"),
                        "_source":  doc
                    }
                    batch.append(action)
                    offsets.append(msg)

                    if len(batch) >= BULK_BATCH_SIZE:
                        await bulk_index(batch)
                        await consumer.commit()
                        batch.clear()
                        offsets.clear()
            except Exception as e:
                logging.error(f"[Kafka] Worker error: {e}")
                await asyncio.sleep(5)
            # Process remaining batch
            if batch:
                await bulk_index(batch)
                await consumer.commit()
                batch.clear()
                offsets.clear()
    finally:
        logging.info(f"[Kafka] Worker stopped. Total messages consumed: {message_count}")

async def startup():
    global consumer, worker_task
    consumer = AIOKafkaConsumer(
        RAW_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="es-indexer",
        enable_auto_commit=False,
        auto_offset_reset="earliest"
    )
    await consumer.start()
    worker_task = asyncio.create_task(es_kafka_worker())

async def shutdown():
    global consumer, worker_task
    logging.info("Shutting down Kafka worker and consumer...")
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    if consumer:
        await consumer.stop()
    await es.close()

# ── Bulk helper with retry ────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=10))
async def bulk_index(actions):
    start = time.time()
    # async_bulk returns (success_count, errors)
    success, errors = await helpers.async_bulk(
        client=es,
        actions=actions,
        raise_on_error=False,
    )
    elapsed = time.time() - start
    print(f"[ES] Indexed {success} docs in {elapsed:.2f}s")
    if errors:
        # Log non-fatal errors but don't fail the entire batch
        print(f"[ES] Bulk errors: {errors}")
    return success

async def main():
    try:
        await startup()
        # Run forever until interrupted
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await shutdown()

# ── Entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Fatal error: {e}")
