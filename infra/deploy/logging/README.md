# Central log shipping (Q64, NFR-12)

**Status:** pipeline built and unit-tested locally. Not connected to any cluster or log store; the destination and
how long logs are kept are DEC-08 and DEC-09.

## What the services write

The API, worker and CLI write **one JSON object per line to stdout** (`hela/core/logs.py`; set
`HELA_LOG_FORMAT=text` for readable local output, which is the default in dev and test):

```json
{"ts": "2026-11-08T00:17:16.768495Z", "level": "info", "logger": "hela.access", "service": "api",
 "correlation_id": "c_70555c2ad9ad4ed3", "message": "request", "http_method": "POST",
 "route": "/v1/auth/otp/request", "status": 202, "duration_ms": 14.2}
```

* `correlation_id` matches the one returned in every API error envelope and `X-Correlation-ID` header, so a
  support case can be traced without anyone's phone number.
* The access log (`hela.access`) records the **route template**, not the raw path, and never the client IP, query
  string, headers or body. uvicorn's own access log is switched off for that reason.
* Personal data is never logged in the first place. As a second line of defence, every record is masked before
  it is written: phone numbers, e-mail addresses, bearer tokens, JSON Web Tokens and one-time codes.
* Database errors are reduced to their type and SQLSTATE (`DataError (sqlstate 22P02)`) in logs and in
  `jobs.last_error`. Bound parameters are hidden (`hide_parameters`), and the server's own message is dropped
  too, because PostgreSQL repeats submitted values in it.

## Collector

`vector.yaml` runs as a node agent (`kubectl apply -k infra/deploy/logging`, in the `hela-platform` namespace,
because it needs read access to node log files). It:

1. keeps only pods in the `hela` namespace;
2. parses each JSON line; anything that is not JSON is kept as `unstructured: true`;
3. drops any `body`, `headers`, `cookie` or `authorization` field;
4. applies the same masks again, to `message` and `exception`;
5. ships over HTTPS to the log store, with an on-disk buffer so a log-store outage does not lose lines.

The sink is a placeholder `http` endpoint. Swapping it for Loki, OpenSearch or a cloud logging service changes only
the `sinks` block.

## Checks

```bash
docker run --rm -v "$PWD":/w:ro -e HELA_LOG_SINK_TOKEN=x timberio/vector:0.46.1-alpine test /w/vector.yaml /w/vector-test.yaml
```

`vector-test.yaml` covers parsing, re-masking and dropped fields. `services/api/tests/test_logging.py` runs real
sign-in flows with JSON logging and fails if a phone number, code or token appears in any line. Both run in CI.

## Not decided

* Log store and region (DEC-08). Retention (DEC-09); security-relevant logs are usually kept longer than access logs.
* Who can search logs, and whether searches are themselves audited (DEC-04).
