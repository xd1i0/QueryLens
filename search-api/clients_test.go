package main

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/elastic/go-elasticsearch/v8"
	"github.com/stretchr/testify/assert"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestQueryElasticsearch_Success(t *testing.T) {
	// Mock ES server
	esSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{
			"hits": {
				"hits": [
					{"_id": "doc1", "_score": 1.23},
					{"_id": "doc2", "_score": 0.99}
				]
			}
		}`)
	}))
	defer esSrv.Close()
	os.Setenv("ES_URL", esSrv.URL)
	initES()
	results, err := queryElasticsearch(context.Background(), "foo", 2)
	assert.NoError(t, err)
	assert.Len(t, results, 2)
	assert.Equal(t, "doc1", results[0].ID)
}

func TestQueryElasticsearch_Error(t *testing.T) {
	// Unreachable ES
	os.Setenv("ES_URL", "http://127.0.0.1:9999")
	initES()
	_, err := queryElasticsearch(context.Background(), "foo", 2)
	assert.Error(t, err)
}

func TestQueryVectorService_Success(t *testing.T) {
	// Mock embedding service
	embedSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"embeddings": [[0.1,0.2,0.3]]}`)
	}))
	defer embedSrv.Close()
	// Mock FAISS service
	faissSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"ids": ["docA", "docB"], "scores": [0.9, 0.8]}`)
	}))
	defer faissSrv.Close()
	embedURL = embedSrv.URL
	faissURL = faissSrv.URL
	results, err := queryVectorService(context.Background(), "foo", 2)
	assert.NoError(t, err)
	assert.Len(t, results, 2)
	assert.Equal(t, "docA", results[0].ID)
}

func TestQueryVectorService_EmbedError(t *testing.T) {
	embedURL = "http://127.0.0.1:9999"
	faissURL = "http://127.0.0.1:9999"
	_, err := queryVectorService(context.Background(), "foo", 2)
	assert.Error(t, err)
}
