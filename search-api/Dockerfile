FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod .
COPY go.sum .
COPY main.go .
RUN go mod download
RUN go build -o queryservice .

FROM alpine:latest
WORKDIR /root/
COPY --from=builder /app/queryservice .
EXPOSE 8080
CMD ["./queryservice"]