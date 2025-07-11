package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/elastic/go-elasticsearch/v8"
	"github.com/gin-gonic/gin"
	"net/http"
	"os"
	"sort" // moved sort here, not at the end
	"strconv"
	"strings"
	"sync"
	"time"
)

type ChunkResult struct {
	ChunkID         string  `json:"chunk_id"`
	BM25Score       float64 `json:"bm25_score"`
	VectorScore     float64 `json:"vector_score"`
	FinalScore      float64 `json:"final_score"`
	Source          string  `json:"source"`
	Title           string  `json:"title,omitempty"`
	Snippet         string  `json:"snippet,omitempty"`
	URL             string  `json:"url,omitempty"`
	Timestamp       string  `json:"timestamp,omitempty"`
	MissingMetadata bool    `json:"missing_metadata,omitempty"`
}

type DocResult struct {
	DocID           string        `json:"doc_id"`
	Tags            []string      `json:"tags,omitempty"`
	Author          string        `json:"author,omitempty"`
	OriginSystem    string        `json:"origin_system,omitempty"`
	MissingMetadata bool          `json:"missing_metadata,omitempty"`
	Chunks          []ChunkResult `json:"chunks"`
}

type Meta struct {
	DocID        string   `json:"doc_id"`
	ChunkID      string   `json:"chunk_id"`
	Title        string   `json:"title"`
	Tags         []string `json:"tags"`
	Author       string   `json:"author"`
	OriginSystem string   `json:"origin_system"`
	Timestamp    string   `json:"timestamp"`
	Content      string   `json:"content"`
	URL          string   `json:"url"`
}

// Add the missing Result struct for internal merging logic
type Result struct {
	ID          string // doc_id[#chunk_id]
	BM25Score   float64
	VectorScore float64
	Source      string
	FinalScore  float64
}

type SearchRequest struct {
	Query string `json:"query"`
	TopK  int    `json:"top_k"`
}

type SearchResponse struct {
	Results []DocResult `json:"results"`
}

var (
	esClient *elasticsearch.Client
	embedURL string
	faissURL string
	alpha    float64
	beta     float64
	initOnce sync.Once
)

func initConfig() {
	initOnce.Do(func() {
		embedURL = getenv("EMBEDDING_URL", "http://embedding:8000/encode")
		faissURL = getenv("FAISS_URL", "http://faiss-index:8001/search")
		alpha = mustParseFloat(getenv("MERGE_ALPHA", "0.5"))
		beta = mustParseFloat(getenv("MERGE_BETA", "0.5"))
	})
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func mustParseFloat(s string) float64 {
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0.5
	}
	return f
}

func initES() error {
	cfg := elasticsearch.Config{
		Addresses: []string{getenv("ES_URL", "http://elasticsearch:9200")},
	}
	client, err := elasticsearch.NewClient(cfg)
	if err != nil {
		return err
	}
	esClient = client
	return nil
}

func mergeResults(esResults, vecResults []Result, topK int, alpha, beta float64) []Result {
	// Merge by ID, keep bm25 and vector scores, track sources
	type mergedInfo struct {
		bm25   float64
		vector float64
		source string
	}
	scoreMap := map[string]*mergedInfo{}

	for _, r := range esResults {
		scoreMap[r.ID] = &mergedInfo{bm25: r.BM25Score, source: "elasticsearch"}
	}
	for _, r := range vecResults {
		if info, ok := scoreMap[r.ID]; ok {
			info.vector = r.VectorScore
			info.source = "both"
		} else {
			scoreMap[r.ID] = &mergedInfo{vector: r.VectorScore, source: "faiss"}
		}
	}
	merged := make([]Result, 0, len(scoreMap))
	for id, info := range scoreMap {
		finalScore := alpha*info.bm25 + beta*info.vector
		merged = append(merged, Result{
			ID:          id,
			BM25Score:   info.bm25,
			VectorScore: info.vector,
			Source:      info.source,
			FinalScore:  finalScore,
		})
	}
	// Sort by finalScore desc
	sort.Slice(merged, func(i, j int) bool { return merged[i].FinalScore > merged[j].FinalScore })
	if len(merged) > topK {
		merged = merged[:topK]
	}
	return merged
}

func fetchMetadataFromES(ctx context.Context, ids []string) (map[string]Meta, error) {
	if len(ids) == 0 {
		return map[string]Meta{}, nil
	}
	reqBody := map[string]interface{}{
		"ids": ids,
		"_source": []string{
			"doc_id", "chunk_id", "title", "tags", "author",
			"origin_system", "timestamp", "content", "url",
		},
	}
	body, _ := json.Marshal(reqBody)
	res, err := esClient.Mget(
		bytes.NewReader(body),
		esClient.Mget.WithContext(ctx),
		esClient.Mget.WithIndex("docs"),
	)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("es mget status %d", res.StatusCode)
	}
	var parsed struct {
		Docs []struct {
			ID     string                 `json:"_id"`
			Found  bool                   `json:"found"`
			Source map[string]interface{} `json:"_source"`
		} `json:"docs"`
	}
	if err := json.NewDecoder(res.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	metas := make(map[string]Meta)
	for _, doc := range parsed.Docs {
		if !doc.Found {
			continue
		}
		var m Meta
		b, _ := json.Marshal(doc.Source)
		_ = json.Unmarshal(b, &m)
		metas[doc.ID] = m
	}
	return metas, nil
}

func parseID(id string) (docID, chunkID string) {
	// Expecting id format: doc_id[_chunk_id] or just doc_id
	parts := strings.SplitN(id, "_chunk_", 2)
	docID = parts[0]
	if len(parts) > 1 {
		chunkID = "chunk_" + parts[1]
	} else {
		chunkID = parts[0]
	}
	return
}

// Helper to extract just the chunk_id (e.g., "chunk_2") from an ES ID
func extractChunkID(id string) string {
	parts := strings.SplitN(id, "_chunk_", 2)
	if len(parts) > 1 {
		return "chunk_" + parts[1]
	}
	return id
}
func enrichResultsWithMetadata(ctx context.Context, results []Result) []DocResult {
	ids := make([]string, 0, len(results))
	for _, r := range results {
		ids = append(ids, r.ID)
	}
	metas, _ := fetchMetadataFromES(ctx, ids)

	docMap := make(map[string]*DocResult)
	for _, r := range results {
		meta, ok := metas[r.ID]
		// Always use doc_id and chunk_id from metadata if present, else parse from ID
		docID, chunkID := parseID(r.ID)
		if ok {
			if meta.DocID != "" {
				docID = meta.DocID
			}
			if meta.ChunkID != "" {
				chunkID = meta.ChunkID
			}
		}
		chunk := ChunkResult{
			ChunkID:         chunkID,
			BM25Score:       r.BM25Score,
			VectorScore:     r.VectorScore,
			FinalScore:      r.FinalScore,
			Source:          r.Source,
			MissingMetadata: !ok,
		}
		if ok {
			chunk.Title = meta.Title
			if meta.Content != "" {
				if len(meta.Content) > 200 {
					chunk.Snippet = meta.Content[:200] + "..."
				} else {
					chunk.Snippet = meta.Content
				}
			}
			// Always use normalized URL: /docs/{doc_id}#{chunk_id}
			chunk.URL = fmt.Sprintf("/docs/%s#%s", docID, chunkID)
			chunk.Timestamp = meta.Timestamp
		}
		doc, exists := docMap[docID]
		if !exists {
			doc = &DocResult{
				DocID: docID,
			}
			if ok {
				doc.Tags = meta.Tags
				doc.Author = meta.Author
				doc.OriginSystem = meta.OriginSystem
			}
			docMap[docID] = doc
		}
		doc.Chunks = append(doc.Chunks, chunk)
	}

	docResults := make([]DocResult, 0, len(docMap))
	for _, doc := range docMap {
		missing := true
		for _, c := range doc.Chunks {
			if !c.MissingMetadata {
				missing = false
				break
			}
		}
		doc.MissingMetadata = missing
		docResults = append(docResults, *doc)
	}

	sort.Slice(docResults, func(i, j int) bool {
		bestI, bestJ := 0.0, 0.0
		for _, c := range docResults[i].Chunks {
			if c.FinalScore > bestI {
				bestI = c.FinalScore
			}
		}
		for _, c := range docResults[j].Chunks {
			if c.FinalScore > bestJ {
				bestJ = c.FinalScore
			}
		}
		return bestI > bestJ
	})

	return docResults
}

func queryElasticsearch(ctx context.Context, query string, topK int) ([]Result, error) {
	reqBody := map[string]interface{}{
		"size": topK,
		"query": map[string]interface{}{
			"query_string": map[string]interface{}{
				"query": query,
			},
		},
	}
	body, _ := json.Marshal(reqBody)
	res, err := esClient.Search(
		esClient.Search.WithContext(ctx),
		esClient.Search.WithIndex("docs"),
		esClient.Search.WithBody(bytes.NewReader(body)),
	)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return nil, fmt.Errorf("es status %d", res.StatusCode)
	}
	var parsed struct {
		Hits struct {
			Hits []struct {
				ID    string  `json:"_id"`
				Score float64 `json:"_score"`
			} `json:"hits"`
		} `json:"hits"`
	}
	if err := json.NewDecoder(res.Body).Decode(&parsed); err != nil {
		return nil, err
	}
	results := make([]Result, 0, len(parsed.Hits.Hits))
	for _, hit := range parsed.Hits.Hits {
		results = append(results, Result{
			ID:          hit.ID,
			BM25Score:   hit.Score,
			VectorScore: 0,
			Source:      "elasticsearch",
		})
	}
	return results, nil
}

func queryVectorService(ctx context.Context, query string, topK int) ([]Result, error) {
	embedReq := map[string]interface{}{
		"texts": []string{query},
		"model": "default",
	}
	embedBody, _ := json.Marshal(embedReq)
	embedResp, err := http.Post(embedURL, "application/json", bytes.NewReader(embedBody))
	if err != nil {
		return nil, err
	}
	defer embedResp.Body.Close()
	if embedResp.StatusCode != 200 {
		return nil, fmt.Errorf("embed status %d", embedResp.StatusCode)
	}
	var embedParsed struct {
		Vectors [][]float32 `json:"vectors"`
	}
	if err := json.NewDecoder(embedResp.Body).Decode(&embedParsed); err != nil {
		return nil, err
	}
	if len(embedParsed.Vectors) == 0 {
		return nil, errors.New("no embedding returned")
	}
	faissReq := map[string]interface{}{
		"vector": embedParsed.Vectors[0],
		"top_k":  topK,
	}
	faissBody, _ := json.Marshal(faissReq)
	faissResp, err := http.Post(faissURL, "application/json", bytes.NewReader(faissBody))
	if err != nil {
		return nil, err
	}
	defer faissResp.Body.Close()
	if faissResp.StatusCode != 200 {
		return nil, fmt.Errorf("faiss status %d", faissResp.StatusCode)
	}
	var faissParsed struct {
		IDs    []string  `json:"ids"`
		Scores []float64 `json:"scores"`
	}
	if err := json.NewDecoder(faissResp.Body).Decode(&faissParsed); err != nil {
		return nil, err
	}
	results := make([]Result, 0, len(faissParsed.IDs))
	for i := range faissParsed.IDs {
		results = append(results, Result{
			ID:          faissParsed.IDs[i],
			BM25Score:   0,
			VectorScore: faissParsed.Scores[i],
			Source:      "faiss",
		})
	}
	return results, nil
}

func main() {
	initConfig()
	if err := initES(); err != nil {
		panic(err)
	}
	r := gin.Default()

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})

	r.POST("/search", func(c *gin.Context) {
		var req SearchRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request"})
			return
		}
		ctx, cancel := context.WithTimeout(c.Request.Context(), 5*time.Second)
		defer cancel()
		esCh := make(chan []Result, 1)
		vecCh := make(chan []Result, 1)
		errCh := make(chan error, 2)
		go func() {
			res, err := queryElasticsearch(ctx, req.Query, req.TopK)
			if err != nil {
				errCh <- err
				esCh <- nil
				return
			}
			esCh <- res
		}()
		go func() {
			res, err := queryVectorService(ctx, req.Query, req.TopK)
			if err != nil {
				errCh <- err
				vecCh <- nil
				return
			}
			vecCh <- res
		}()
		var esResults, vecResults []Result
		for i := 0; i < 2; i++ {
			select {
			case r := <-esCh:
				esResults = r
			case r := <-vecCh:
				vecResults = r
			case err := <-errCh:
				c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
				return
			}
		}
		merged := mergeResults(esResults, vecResults, req.TopK, alpha, beta)
		grouped := enrichResultsWithMetadata(ctx, merged)
		c.JSON(http.StatusOK, SearchResponse{Results: grouped})
	})

	r.Run() // listens on :8080 by default
}
