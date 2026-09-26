"""
schemas.py
Pydantic data models for the Performance AI Analysis API.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ── Incoming Request Schemas ──────────────────────────────────────────────────

class TransactionMetric(BaseModel):
    name: str
    p90: float
    p80: float
    avg: float
    min: float
    max: float
    hitcount: int
    errors: int
    error_pct: float
    rt_target: float
    tph_target: float
    rt_status: str
    tph_status: str
    overall_status: str
    rt_deviation_pct: float
    tph_ach_pct: float


class TPHNotAchieved(BaseModel):
    name: str
    p90: float
    hitcount: int
    tph_ach_pct: float
    target_tph: float
    status: str


class SLADeviation(BaseModel):
    name: str
    p90: float
    p80: float
    avg: float
    deviation_pct: float
    target_resp: float
    status: str


class ErrorTransaction(BaseModel):
    timeStamp: Optional[Any] = None
    Transaction: Optional[str] = None
    ResponseCode: Optional[str] = None
    fail_count: Optional[int] = 0
    fail_pct: Optional[float] = Field(default=0.0, alias="fail%")

    class Config:
        populate_by_name = True


class PerformanceReportRequest(BaseModel):
    test_name: str = "Performance Test"
    generated_at: Optional[str] = None
    grafana_url: Optional[str] = ""
    total: int
    passed: int
    partial: int
    failed: int
    avg_rt: float
    total_hits: int
    avg_error_pct: float
    perf_score: float
    perf_grade: str
    perf_status: str
    stab_score: float
    stab_status: str
    overall_result: str
    all_transactions: List[TransactionMetric] = []
    tph_not_achieved: List[TPHNotAchieved] = []
    sla_90pct_deviation: List[SLADeviation] = []
    error_transactions: List[Dict[str, Any]] = []


# ── Outgoing Response Schemas ─────────────────────────────────────────────────

class Recommendations(BaseModel):
    critical: List[str] = []
    medium: List[str] = []
    low: List[str] = []


class AIAnalysisResponse(BaseModel):
    status: str = "success"
    model_used: str = "claude-sonnet-4-6"
    test_name: str
    overall_result: str
    perf_score: float
    perf_grade: str
    management_summary: str
    key_findings: List[str] = []
    critical_issues: List[str] = []
    performance_risks: List[str] = []
    positive_improvements: List[str] = []
    areas_of_concern: List[str] = []
    recommendations: Recommendations = Field(default_factory=Recommendations)
