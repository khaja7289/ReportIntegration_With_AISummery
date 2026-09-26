"""
ai_engine.py
Handles LLM prompt synthesis, Anthropic Claude API invocations, and deterministic fallbacks.
"""

import os
import json
import re
import urllib.request
import urllib.error
from .schemas import PerformanceReportRequest, AIAnalysisResponse, Recommendations

API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
MODEL_NAME = os.environ.get("AI_MODEL_NAME", "claude-sonnet-4-6")


def build_claude_prompt(req: PerformanceReportRequest) -> str:
    trx_lines = []
    for t in req.all_transactions[:30]:
        trx_lines.append(
            f"  • {t.name}: P90={t.p90}ms (Target={t.rt_target}ms) "
            f"Hits={t.hitcount} (Target={t.tph_target}) Err%={t.error_pct}% "
            f"RT_Status={t.rt_status} TPH_Status={t.tph_status} Overall={t.overall_status}"
        )

    tph_exc = [
        f"  • {t.name}: Achieved={t.tph_ach_pct}% (Target TPH={t.target_tph}, Hits={t.hitcount}) Status={t.status}"
        for t in req.tph_not_achieved
    ]
    sla_exc = [
        f"  • {t.name}: P90={t.p90}ms (Target={t.target_resp}ms, Dev=+{t.deviation_pct}%) Status={t.status}"
        for t in req.sla_90pct_deviation
    ]
    err_exc = [
        f"  • {e.get('Transaction', 'Unknown')}: Code={e.get('ResponseCode')} Count={e.get('fail_count', 0)} ({e.get('fail%', e.get('fail_pct', 0))}%)"
        for e in req.error_transactions[:10]
    ]

    prompt = f"""You are a Principal Performance Engineer. Analyze these performance test results for '{req.test_name}'.

EXECUTIVE METRICS:
- Overall Result: {req.overall_result}
- Performance Score: {req.perf_score}/100 (Grade {req.perf_grade} - {req.perf_status})
- Stability Score: {req.stab_score}/100 ({req.stab_status})
- Total Transactions: {req.total} (Passed: {req.passed}, Partial Pass: {req.partial}, Failed: {req.failed})
- Average Response Time: {req.avg_rt} ms
- Total Hit Count: {req.total_hits} samples
- Average Error Rate: {req.avg_error_pct}%

TRANSACTION SAMPLES:
{chr(10).join(trx_lines) if trx_lines else "None provided"}

SLA 90th PERCENTILE DEVIATIONS (AMBER/RED):
{chr(10).join(sla_exc) if sla_exc else "None — all transactions within SLA"}

THROUGHPUT UNDERACHIEVERS (AMBER/RED):
{chr(10).join(tph_exc) if tph_exc else "None — all throughput targets met"}

RECORDED ERRORS:
{chr(10).join(err_exc) if err_exc else "None — zero errors observed"}

Return ONLY a valid JSON object (no markdown, no backticks, no extra text):
{{
  "management_summary": "3-4 concise, data-backed sentences providing an executive overview of test health, primary bottlenecks, and deployment risk.",
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "critical_issues": ["critical issue 1", "critical issue 2"],
  "performance_risks": ["risk 1", "risk 2", "risk 3"],
  "positive_improvements": ["positive aspect 1", "positive aspect 2"],
  "areas_of_concern": ["concern 1", "concern 2"],
  "recommendations": {{
    "critical": ["actionable critical fix 1", "actionable critical fix 2"],
    "medium": ["actionable medium fix 1", "actionable medium fix 2"],
    "low": ["actionable low fix 1"]
  }}
}}"""
    return prompt


def call_claude_api(prompt: str) -> dict:
    if not API_KEY:
        print("[ai_engine] ANTHROPIC_API_KEY not configured — using deterministic rule engine.")
        return None

    payload = json.dumps({
        "model": MODEL_NAME,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        CLAUDE_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["content"][0]["text"].strip()
            clean_json = re.sub(r'^```json\s*|^```\s*|```$', '', raw, flags=re.MULTILINE).strip()
            return json.loads(clean_json)
    except Exception as e:
        print(f"[ai_engine] Claude API invocation error: {e} — falling back to rule engine.")
        return None


def generate_fallback_analysis(req: PerformanceReportRequest) -> dict:
    sla_red = [t.name for t in req.sla_90pct_deviation if t.status == "RED"]
    tph_red = [t.name for t in req.tph_not_achieved if t.status == "RED"]
    all_red = list(dict.fromkeys(sla_red + tph_red))
    red_str = ", ".join(all_red[:3]) if all_red else "None"

    sla_amber = [t.name for t in req.sla_90pct_deviation if t.status == "AMBER"]
    tph_amber = [t.name for t in req.tph_not_achieved if t.status == "AMBER"]
    all_amber = list(dict.fromkeys(sla_amber + tph_amber))
    amber_str = ", ".join(all_amber[:3]) if all_amber else "None"

    summary_text = (
        f"The {req.test_name} test executed with overall result '{req.overall_result}', achieving a Performance Score of "
        f"{req.perf_score}/100 (Grade {req.perf_grade} — {req.perf_status}) and a Stability Score of {req.stab_score}/100 ({req.stab_status}). "
        f"Of the {req.total} monitored transactions, {req.passed} achieved SLA compliance, {req.partial} showed warnings, "
        f"and {req.failed} breached SLA boundaries ({red_str}). Average response latency was {req.avg_rt} ms across {req.total_hits} samples with an error rate of {req.avg_error_pct}%."
    )

    return {
        "management_summary": summary_text,
        "key_findings": [
            f"Overall Test Evaluation: {req.overall_result} (Score: {req.perf_score}/100, Grade {req.perf_grade})",
            f"System Stability Score: {req.stab_score}/100 ({req.stab_status})",
            f"{req.passed} of {req.total} transactions satisfied all response time and throughput SLAs",
        ],
        "critical_issues": [
            f"SLA threshold breaches observed on: {red_str}" if all_red else "No critical SLA threshold breaches recorded",
            f"Average error rate recorded at {req.avg_error_pct}%" if req.avg_error_pct > 0 else "Zero errors recorded across all sample transactions",
        ],
        "performance_risks": [
            f"Severe latency degradation on: {red_str}" if all_red else "Transaction latency maintained within SLA tolerances",
            f"Throughput pacing deficit on: {amber_str}" if all_amber else "Throughput volume aligned with production pacing targets",
            "Resource contention or thread starvation under sustained peak workload",
        ],
        "positive_improvements": [
            f"{req.passed} transactions demonstrated stable sub-second latency profiles",
            f"Workload pipeline successfully processed {req.total_hits} total requests",
        ],
        "areas_of_concern": [
            f"High response times on: {red_str or amber_str or 'None'}",
            "Database connection pool sizing and backend query latency during concurrent execution",
        ],
        "recommendations": {
            "critical": [
                f"Address SLA breaches on {red_str} prior to deployment gate approval" if all_red else "System meets release criteria — verify regression stability",
                "Investigate root cause of logged HTTP exceptions with the application engineering team",
            ],
            "medium": [
                f"Review Amber transactions ({amber_str}) to prevent SLA erosion" if all_amber else "Monitor database thread pool utilization under peak load",
                "Optimize client-side think time pacing and thread concurrency",
            ],
            "low": [
                "Verify CDN caching policies on frequently accessed static endpoints",
                "Maintain automated performance test cadence on subsequent release tags",
            ],
        },
    }


def analyze_report(req: PerformanceReportRequest) -> AIAnalysisResponse:
    prompt = build_claude_prompt(req)
    result = call_claude_api(prompt)

    model_used = MODEL_NAME if (API_KEY and result) else "deterministic-rule-engine"
    if not result:
        result = generate_fallback_analysis(req)

    recs_data = result.get("recommendations", {})
    recs = Recommendations(
        critical=recs_data.get("critical", []),
        medium=recs_data.get("medium", []),
        low=recs_data.get("low", [])
    )

    return AIAnalysisResponse(
        status="success",
        model_used=model_used,
        test_name=req.test_name,
        overall_result=req.overall_result,
        perf_score=req.perf_score,
        perf_grade=req.perf_grade,
        management_summary=result.get("management_summary", ""),
        key_findings=result.get("key_findings", []),
        critical_issues=result.get("critical_issues", []),
        performance_risks=result.get("performance_risks", []),
        positive_improvements=result.get("positive_improvements", []),
        areas_of_concern=result.get("areas_of_concern", []),
        recommendations=recs
    )
