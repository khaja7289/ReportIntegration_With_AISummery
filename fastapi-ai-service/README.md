# Performance AI Reporting Service (FastAPI)

A centralized microservice that accepts structured performance test metrics (`summary.json`) from GitLab CI pipelines, analyzes them using Anthropic Claude, and returns an executive management summary, key findings, critical issues, and prioritized recommendations.

---

## Features

- **Decoupled Architecture**: Keeps AI API keys safe on the server — no secrets exposed in GitLab CI runners.
- **Pydantic Validation**: Validates the 4 structured performance datasets (`01_all_transactions`, `02_tph_not_achieved`, `03_sla_90pct_deviation`, `04_error_transactions`).
- **Claude Sonnet Analysis**: Professional performance engineering prompts yielding high-value executive insights.
- **Built-in Fallback**: Deterministic rule-based evaluation if the LLM is unreachable or if no API key is provided.
- **RESTful Endpoints**:
  - `GET /health`: Liveness probe.
  - `POST /api/v1/analyze`: Accepts JSON payload (`summary.json`).
  - `POST /api/v1/analyze-file`: Accepts multipart file upload for manual testing and Swagger UI.

---

## Quickstart

### 1. Run Locally

```bash
cd fastapi-ai-service
pip install -r requirements.txt
export ANTHROPIC_API_KEY="your-anthropic-key"  # Optional: falls back to rule engine if omitted
uvicorn app.main:app --reload --port 8000
```

Access Swagger UI documentation at: `http://localhost:8000/docs`

### 2. Run with Docker

```bash
docker build -t perf-ai-service .
docker run -d -p 8000:8000 -e ANTHROPIC_API_KEY="your-anthropic-key" --name perf-ai perf-ai-service
```

---

## GitLab CI Integration

In `.gitlab-ci.yml`, set the service URL variable:
```yaml
variables:
  AI_SERVICE_URL: "http://your-fastapi-service:8000"
```
The framework client in `perf-framework/claude_insights.py` will automatically send `summary.json` to this endpoint.
