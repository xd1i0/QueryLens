import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from elasticsearch import BadRequestError
from elastic_transport import ConnectionError as ESConnectionError

import main

@pytest.mark.asyncio
async def test_ensure_index_creates_index_when_missing():
    es_mock = AsyncMock()
    es_mock.indices.exists.return_value = False
    es_mock.indices.create.return_value = None

    await main.ensure_index(es_mock, "testindex")
    es_mock.indices.create.assert_awaited_once()
    es_mock.indices.exists.assert_awaited_once_with(index="testindex")

@pytest.mark.asyncio
async def test_ensure_index_skips_creation_if_exists():
    es_mock = AsyncMock()
    es_mock.indices.exists.return_value = True

    await main.ensure_index(es_mock, "testindex")
    es_mock.indices.create.assert_not_awaited()

@pytest.mark.asyncio
async def test_ensure_index_invalid_name_raises():
    es_mock = AsyncMock()
    with pytest.raises(ValueError):
        await main.ensure_index(es_mock, "INVALID_INDEX")

@pytest.mark.asyncio
async def test_ensure_index_badrequest_deletes_and_recreates():
    es_mock = AsyncMock()
    es_mock.indices.exists.side_effect = BadRequestError(message="bad", meta=None, body={})
    es_mock.indices.delete.return_value = None
    es_mock.indices.create.return_value = None

    await main.ensure_index(es_mock, "testindex")
    es_mock.indices.delete.assert_awaited_once_with(index="testindex", ignore_unavailable=True)
    es_mock.indices.create.assert_awaited_once()

@pytest.mark.asyncio
async def test_ensure_index_connection_error_retries():
    es_mock = AsyncMock()
    es_mock.indices.exists.side_effect = [ESConnectionError("fail"), False]
    es_mock.indices.create.return_value = None

    await main.ensure_index(es_mock, "testindex")
    assert es_mock.indices.exists.await_count == 2

@pytest.mark.asyncio
async def test_bulk_index_success(monkeypatch):
    actions = [{"_id": 1}]
    async def fake_async_bulk(client, actions, raise_on_error):
        return (1, [])
    monkeypatch.setattr(main.helpers, "async_bulk", fake_async_bulk)
    result = await main.bulk_index(actions)
    assert result == 1

@pytest.mark.asyncio
async def test_bulk_index_with_errors(monkeypatch):
    actions = [{"_id": 1}]
    async def fake_async_bulk(client, actions, raise_on_error):
        return (1, [{"error": "fail"}])
    monkeypatch.setattr(main.helpers, "async_bulk", fake_async_bulk)
    result = await main.bulk_index(actions)
    assert result == 1

