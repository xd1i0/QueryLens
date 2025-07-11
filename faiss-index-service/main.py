import os
import json
import time
import threading
import hashlib
import numpy as np
import faiss
import redis
import asyncio
from aiokafka import AIOKafkaConsumer
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from wsgiref.simple_server import make_server

# --- Config ---
KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "embeddings")
BULK_BATCH_SIZE = int(os.getenv("BULK_BATCH_SIZE", "200"))  # Added for batching
FAISS_INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "faiss.index")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REBUILD_INTERVAL = int(os.getenv("REBUILD_INTERVAL", "10000"))  # messages
INDEX_DIM = int(os.getenv("INDEX_DIM", "384"))
N_LIST = int(os.getenv("N_LIST", "100"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))

# --- Prometheus Metrics ---
INGEST_COUNT = Counter('faiss_ingest_count', 'Number of vectors ingested')
INGEST_LATENCY = Histogram('faiss_ingest_latency_seconds', 'Latency for ingesting vectors')
INDEX_SIZE = Gauge('faiss_index_size', 'Current number of vectors in index')
LAST_REBUILD = Gauge('faiss_last_rebuild_timestamp', 'Last index rebuild time')

# --- Redis ---
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# --- FAISS Index ---
def cosine_faiss_index(dim):
    # Flat index with inner product (cosine similarity after normalization)
    return faiss.IndexFlatIP(dim)

def load_or_create_index():
    global INDEX_DIM
    if os.path.exists(FAISS_INDEX_PATH):
        index = faiss.read_index(FAISS_INDEX_PATH)
        INDEX_DIM = index.d  # Ensure global matches loaded index
    else:
        index = cosine_faiss_index(INDEX_DIM)
    return index

faiss_index = load_or_create_index()
index_lock = threading.RLock()

# --- Helper Functions ---
def id_hash(doc_id, chunk_id):
    return int(hashlib.sha256(f"{doc_id}:{chunk_id}".encode()).hexdigest(), 16) % (2**63)

def normalize(vec):
    norm = np.linalg.norm(vec)
    return (vec / norm).astype('float32') if norm > 0 else vec.astype('float32')

def store_metadata(vec_id, doc_id, chunk_id, metadata):
    meta = dict(metadata)
    meta["doc_id"] = doc_id
    meta["chunk_id"] = chunk_id
    meta["timestamp"] = int(time.time())
    redis_client.hset(f"vec:{vec_id}", mapping={
        "metadata": json.dumps(meta)
    })

def get_metadata(vec_id):
    data = redis_client.hgetall(f"vec:{vec_id}")
    if not data:
        return None
    return json.loads(data["metadata"])

def store_vector_for_rebuild(vec_id, vector):
    redis_client.hset(f"vec:{vec_id}", "vector", json.dumps(vector))

def get_vector_for_rebuild(vec_id):
    v = redis_client.hget(f"vec:{vec_id}", "vector")
    if v:
        return json.loads(v)
    return None

def recreate_index(new_dim):
    global faiss_index, INDEX_DIM
    try:
        print(f"[DEBUG] Entering recreate_index with new_dim={new_dim}")
        with index_lock:
            faiss_index = cosine_faiss_index(new_dim)
            INDEX_DIM = new_dim
            faiss.write_index(faiss_index, FAISS_INDEX_PATH)
            INDEX_SIZE.set(0)
            for key in redis_client.scan_iter("faiss_row:*"):
                redis_client.delete(key)
        print(f"[INFO] Successfully recreated FAISS index with dim {new_dim}")
    except Exception as e:
        print(f"[ERROR] Exception in recreate_index: {e}")

def upsert_vector(doc_id, chunk_id, vector, metadata):
    global faiss_index, INDEX_DIM
    try:
        print(f"[DEBUG] upsert_vector called for doc_id={doc_id}, chunk_id={chunk_id}")
        vec_id = id_hash(doc_id, chunk_id)
        norm_vec = normalize(np.array(vector, dtype='float32'))
        print(f"[DEBUG] norm_vec.shape={norm_vec.shape}, faiss_index.d={faiss_index.d}, faiss_index.ntotal={faiss_index.ntotal}")
        with index_lock:
            if norm_vec.shape[0] != faiss_index.d:
                print(f"[INFO] Recreating FAISS index with dim {norm_vec.shape[0]} (was {faiss_index.d})")
                recreate_index(norm_vec.shape[0])
                print(f"[DEBUG] Retrying insertion after index recreation: norm_vec.shape={norm_vec.shape}, faiss_index.d={faiss_index.d}")
                if norm_vec.shape[0] != faiss_index.d:
                    print(f"[ERROR] After recreation, vector dim {norm_vec.shape[0]} still does not match index dim {faiss_index.d}")
                    return None

            # Deduplication logic (unchanged, but no rebuild trigger here)
            existing_row = None
            for key in redis_client.scan_iter("faiss_row:*"):
                if redis_client.get(key) == str(vec_id):
                    existing_row = int(key.split(":")[1])
                    break

            if existing_row is not None:
                print(f"[INFO] Updating existing vector for doc_id={doc_id} chunk_id={chunk_id} at row_idx={existing_row}")
                store_vector_for_rebuild(vec_id, vector)
                store_metadata(vec_id, doc_id, chunk_id, metadata)
                # No rebuild here
                return vec_id
            else:
                with index_lock:
                    faiss_index.add(np.array([norm_vec]))
                    row_idx = faiss_index.ntotal - 1
        redis_client.set(f"faiss_row:{row_idx}", vec_id)
        faiss.write_index(faiss_index, FAISS_INDEX_PATH)
        INDEX_SIZE.set(faiss_index.ntotal)
        print(f"[INFO] Inserted vector for doc_id={doc_id} chunk_id={chunk_id} at row_idx={row_idx}")
        store_metadata(vec_id, doc_id, chunk_id, metadata)
        store_vector_for_rebuild(vec_id, vector)
        print(f"[DEBUG] upsert_vector completed (inserted) for doc_id={doc_id}, chunk_id={chunk_id}")
        return vec_id
    except Exception as e:
        print(f"[ERROR] Exception in upsert_vector: {e}")
        return None

def rebuild_index():
    global faiss_index, INDEX_DIM
    print("[INFO] Rebuilding FAISS index to remove duplicates or update vectors...")
    with index_lock:
        # Gather all vec_ids and vectors
        vecs = []
        ids = []
        for key in redis_client.scan_iter("faiss_row:*"):
            vec_id = redis_client.get(key)
            vector = get_vector_for_rebuild(vec_id)
            if vector is not None:
                vecs.append(normalize(np.array(vector, dtype='float32')))
                ids.append(vec_id)
        if vecs:
            dim = len(vecs[0])
            faiss_index = cosine_faiss_index(dim)
            faiss_index.add(np.stack(vecs))
            faiss.write_index(faiss_index, FAISS_INDEX_PATH)
            INDEX_SIZE.set(faiss_index.ntotal)
            # Rebuild row mapping
            for i, vec_id in enumerate(ids):
                redis_client.set(f"faiss_row:{i}", vec_id)
        print(f"[INFO] Rebuilt FAISS index with {len(vecs)} vectors.")

@INGEST_LATENCY.time()
async def process_faiss_batch(batch):
    print(f"[DEBUG] Entering process_faiss_batch with batch size {len(batch)}")
    # Deduplicate batch by (doc_id, chunk_id)
    deduped = {}
    for data in batch:
        key = (data["doc_id"], data["chunk_id"])
        deduped[key] = data  # Last occurrence wins
    rebuild_needed = False
    for data in deduped.values():
        try:
            doc_id = data["doc_id"]
            chunk_id = data["chunk_id"]
            vector = data["vector"]
            metadata = data["metadata"]
            result = upsert_vector(doc_id, chunk_id, vector, metadata)
            if result is not None:
                INGEST_COUNT.inc()
                rebuild_needed = True  # At least one upsert in this batch
        except Exception as e:
            print(f"[ERROR] Exception processing batch item: {e}")
    # Only rebuild once per batch if any upsert happened
    if rebuild_needed and INGEST_COUNT._value.get() % REBUILD_INTERVAL == 0:
        print(f"[DEBUG] Triggering background index rebuild")
        threading.Thread(target=rebuild_index, daemon=True).start()

async def startup():
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BROKERS.split(","),
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        enable_auto_commit=False,
        group_id="faiss-indexer"
    )
    await consumer.start()
    print(f"[Kafka] Consumer started for topic '{KAFKA_TOPIC}' with group_id 'faiss-indexer'")  # <-- Add this log
    worker_task = asyncio.create_task(faiss_kafka_worker(consumer))
    return consumer, worker_task

async def shutdown(consumer, worker_task):
    print("Shutting down Kafka worker and consumer...")
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    if consumer:
        await consumer.stop()

async def main():
    consumer, worker_task = await startup()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await shutdown(consumer, worker_task)

async def faiss_kafka_worker(consumer):
    batch = []
    offsets = []
    message_count = 0
    print(f"[Kafka] Worker started for topic '{KAFKA_TOPIC}' on '{KAFKA_BROKERS}'")
    try:
        while True:
            try:
                async for msg in consumer:
                    print(f"[Kafka] Consumed message at offset {msg.offset} from partition {msg.partition}")
                    message_count += 1
                    data = msg.value
                    # Validate schema
                    if not all(k in data for k in ("doc_id", "chunk_id", "vector", "metadata")):
                        print(f"[Kafka] Skipping message with missing keys: {data}")
                        continue
                    if not isinstance(data["vector"], list):
                        print(f"[Kafka] Skipping message with non-list vector: {data}")
                        continue
                    batch.append(data)
                    offsets.append(msg)
                    if len(batch) >= BULK_BATCH_SIZE:
                        print(f"[Kafka] Processing batch of {len(batch)} messages")
                        await process_faiss_batch(batch)
                        await consumer.commit()
                        batch.clear()
                        offsets.clear()
            except Exception as e:
                print(f"[Kafka] Worker error: {e}")
                await asyncio.sleep(5)
            # Process remaining batch
            if batch:
                print(f"[Kafka] Processing final batch of {len(batch)} messages")
                await process_faiss_batch(batch)
                await consumer.commit()
                batch.clear()
                offsets.clear()
    finally:
        print(f"[Kafka] Worker stopped. Total messages consumed: {message_count}")

# --- Metrics HTTP Server ---
def metrics_app(environ, start_response):
    if environ['PATH_INFO'] == '/metrics':
        start_response('200 OK', [('Content-Type', CONTENT_TYPE_LATEST)])
        return [generate_latest()]
    start_response('404 Not Found', [('Content-Type', 'text/plain')])
    return [b'Not found']

def run_metrics_server():
    with make_server('', METRICS_PORT, metrics_app) as httpd:
        httpd.serve_forever()

# --- Main ---
if __name__ == "__main__":
    # Start metrics server in a thread
    threading.Thread(target=run_metrics_server, daemon=True).start()
    # Run faiss kafka worker in main thread event loop
    asyncio.run(main())
