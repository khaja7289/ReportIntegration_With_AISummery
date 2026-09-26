"""
main.py
FastAPI application exposing performance AI analysis endpoints.
"""

import json
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from .schemas import PerformanceReportRequest, AIAnalysisResponse
from .ai_engine import analyze_report

app = FastAPI(
    title="Performance AI Reporting Service",
    description="Automated AI Insights & Reporting Engine for JMeter and GitLab CI Performance Pipelines",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint for liveness/readiness probes."""
    return {
        "status": "healthy",
        "service": "performance-ai-reporter",
        "version": "1.0.0"
    }


@app.post("/api/v1/analyze", response_model=AIAnalysisResponse, tags=["AI Analysis"])
def analyze_performance(request: PerformanceReportRequest):
    """
    Main analysis endpoint. Receives structured test metrics (summary.json payload)
    and returns synthesized AI insights including executive summary, key findings,
    critical issues, risks, and prioritized recommendations.
    """
    try:
        response = analyze_report(request)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {str(e)}")


@app.post("/api/v1/analyze-file", response_model=AIAnalysisResponse, tags=["AI Analysis"])
async def analyze_performance_file(file: UploadFile = File(...)):
    """
    Convenience endpoint accepting a uploaded summary.json file.
    Useful for manual testing, Swagger UI, or curl file uploads.
    """
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
        report_request = PerformanceReportRequest(**data)
        return analyze_report(report_request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid file or analysis error: {str(e)}")
