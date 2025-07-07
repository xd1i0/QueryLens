class Config:
    default_model = "all-minilm-l6-v2"
    supported_models: dict[str, dict[str, int]] = {
        "all-minilm-l6-v2": {
            "dim": 384
        },
        "all-mpnet-base-v2": {
            "dim": 768
        },
        "all-distilroberta-v1": {
            "dim": 768
        },
        # add more models here as needed
    }
    default_batch_size = 32
    max_batch_size = 128
    max_texts_per_request = 1000

    redis_host = "localhost"
    redis_port = 6379
    redis_db = 0

    kafka_bootstrap = "kafka:9092"

    api_host = "0.0.0.0"
    api_port = 8000

config = Config()
