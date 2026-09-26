"""
claude_insights.py — Performance AI Client
Sends summary.json to FastAPI AI Service (or falls back to direct Claude API / rule engine).
Injects synthesized AI narrative into dashboard.html and saves ai_report.json.
Fails gracefully — pipeline always continues.
"""

import os
import json
import re
import urllib.request
import urllib.error
from pathlib import Path

OUTPUT_DIR     = os.environ.get("OUTPUT_DIR", "artifacts/reports")
AI_SERVICE_URL = os.environ.get("AI_SERVICE_URL", "").rstrip("/")
API_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
TEST_NAME      = os.environ.get("TEST_NAME", "Performance Test")
SUMMARY_PATH   = f"{OUTPUT_DIR}/summary.json"
DASH_PATH      = f"{OUTPUT_DIR}/dashboard.html"
AI_REPORT_PATH = f"{OUTPUT_DIR}/ai_report.json"


# ── 1. Call FastAPI AI Service ────────────────────────────────────────────────
def call_fastapi_service(summary):
    if not AI_SERVICE_URL:
        return None

    endpoint = f"{AI_SERVICE_URL}/api/v1/analyze"
    print(f"[ai_client] Calling FastAPI AI Service: {endpoint}")

    payload = json.dumps(summary).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[ai_client] Received response from FastAPI service (model: {data.get('model_used')})")
            return data
    except Exception as e:
        print(f"[ai_client] FastAPI service request failed ({e}) — attempting fallback.")
        return None


# ── 2. Direct Claude API Call (Fallback Option A) ─────────────────────────────
def build_prompt(s):
    trx_lines = []
    for t in s.get("all_transactions", []):
        trx_lines.append(
            f"  {t['name']}: P90={t['p90']}ms(tgt={t['rt_target']}ms) "
            f"Hits={t['hitcount']}(tgt={t['tph_target']}) Err%={t['error_pct']}% "
            f"RT={t['rt_status']} TPH={t['tph_status']} Overall={t['overall_status']}"
        )

    tph_exc = [
        f"  {t['name']}: Achieved={t['tph_ach_pct']}% (Target TPH={t['target_tph']}, Hits={t['hitcount']}) Status={t['status']}"
        for t in s.get("tph_not_achieved", [])
    ]
    sla_exc = [
        f"  {t['name']}: P90={t['p90']}ms (Target={t['target_resp']}ms, Deviation=+{t['deviation_pct']}%) Status={t['status']}"
        for t in s.get("sla_90pct_deviation", [])
    ]
    err_exc = [
        f"  {t.get('Transaction')}: Code={t.get('ResponseCode')} Count={t.get('fail_count')} ({t.get('fail%')}%)"
        for t in s.get("error_transactions", [])
    ]

    return f"""You are a senior performance engineer. Analyze these JMeter load test results for {TEST_NAME}.

SUMMARY: Result={s['overall_result']} Score={s['perf_score']}/100 (Grade {s['perf_grade']}) \
Stability={s['stab_score']}/100 ({s['stab_status']}) \
Transactions: {s['total']} total | {s['passed']} pass | {s['partial']} partial | {s['failed']} fail \
AvgRT={s['avg_rt']}ms TotalHits={s['total_hits']} AvgErr={s['avg_error_pct']}%

TRANSACTION SAMPLES:
{chr(10).join(trx_lines[:25])}

SLA DEVIATIONS (AMBER/RED):
{chr(10).join(sla_exc) if sla_exc else "None"}

TPH NOT ACHIEVED (AMBER/RED):
{chr(10).join(tph_exc) if tph_exc else "None"}

RECORDED ERRORS:
{chr(10).join(err_exc[:10]) if err_exc else "None"}

Return ONLY valid JSON (no markdown, no backticks):
{{"management_summary":"3-4 sentence executive paragraph. Direct and specific.",
"key_findings":["finding1","finding2","finding3"],
"critical_issues":["issue1","issue2"],
"performance_risks":["risk1","risk2","risk3"],
"positive_improvements":["positive1","positive2"],
"areas_of_concern":["concern1","concern2"],
"recommendations":{{"critical":["action1","action2"],"medium":["action1","action2"],"low":["action1"]}}}}"""


def call_claude(prompt):
    if not API_KEY:
        print("[ai_client] ANTHROPIC_API_KEY not configured locally.")
        return None

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))["content"][0]["text"].strip()
            raw = re.sub(r'^```json\s*|^```\s*|```$', '', raw, flags=re.MULTILINE).strip()
            return json.loads(raw)
    except Exception as e:
        print(f"[ai_client] Claude API error: {e}")
        return None


# ── 3. Fallback Rule Engine (Fallback Option B) ──────────────────────────────
def fallback(s):
    sla_red = [t["name"] for t in s.get("sla_90pct_deviation", []) if t.get("status") == "RED"]
    tph_red = [t["name"] for t in s.get("tph_not_achieved", []) if t.get("status") == "RED"]
    all_red = list(dict.fromkeys(sla_red + tph_red))
    red_str = ", ".join(all_red[:3]) if all_red else "None"

    sla_amber = [t["name"] for t in s.get("sla_90pct_deviation", []) if t.get("status") == "AMBER"]
    tph_amber = [t["name"] for t in s.get("tph_not_achieved", []) if t.get("status") == "AMBER"]
    all_amber = list(dict.fromkeys(sla_amber + tph_amber))
    amber_str = ", ".join(all_amber[:3]) if all_amber else "None"

    return {
        "management_summary": (
            f"The {TEST_NAME} run concluded with overall result '{s['overall_result']}', achieving a Performance Score of "
            f"{s['perf_score']}/100 (Grade {s['perf_grade']}) and a Stability Score of {s['stab_score']}/100 ({s['stab_status']}). "
            f"Across {s['total']} evaluated transactions, {s['passed']} met all SLA criteria, {s['partial']} registered warnings, "
            f"and {s['failed']} breached thresholds ({red_str}). Average response time was {s['avg_rt']} ms with an average error rate of {s['avg_error_pct']}%."
        ),
        "key_findings": [
            f"Overall Result: {s['overall_result']} (Score: {s['perf_score']}/100, Grade {s['perf_grade']})",
            f"Stability Index: {s['stab_score']}/100 — {s['stab_status']}",
            f"{s['passed']} of {s['total']} transactions successfully satisfied SLA targets",
        ],
        "critical_issues": [
            f"SLA breaches observed on: {red_str}" if all_red else "No critical SLA breaches detected",
            f"Error rate at {s['avg_error_pct']}% across test execution" if s['avg_error_pct'] > 0 else "Zero errors recorded across all sample transactions",
        ],
        "performance_risks": [
            f"High response time deviations detected on: {red_str}" if all_red else "All transaction response times within SLA limits",
            f"Throughput underachievement on: {amber_str}" if all_amber else "Throughput pacing aligned with configured targets",
            "Concurrency bottlenecks could impact peak-traffic performance under scaled load",
        ],
        "positive_improvements": [
            f"{s['passed']} transactions consistently maintained sub-second SLA compliance",
            f"Execution processed {s['total_hits']} total sample hits reliably",
        ],
        "areas_of_concern": [
            f"Review slow transactions: {red_str or amber_str or 'None'}",
            "Analyze database query plans and network latency for high 90th percentile endpoints",
        ],
        "recommendations": {
            "critical": [
                f"Optimize backend endpoints for breached transactions: {red_str}" if all_red else "Maintain current performance thresholds",
                "Investigate root cause of HTTP response errors logged during the run",
            ],
            "medium": [
                f"Fine-tune warning transactions: {amber_str}" if all_amber else "Profile database connection pools under concurrent load",
                "Review thread concurrency and client-side think time pacing",
            ],
            "low": [
                "Ensure caching is active for static/catalog read requests",
                "Schedule recurring automated regression runs against future builds",
            ],
        },
    }


# ── 4. HTML Injection ─────────────────────────────────────────────────────────
def ul(items):
    return "".join(f"<li>{i}</li>" for i in items)

def insights_html(ins):
    return f"""
    <div class="ins-c"><div class="ttl" style="color:#3b82f6;">🔍 Key Findings</div><ul>{ul(ins.get('key_findings', []))}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#ef4444;">🚨 Critical Issues</div><ul>{ul(ins.get('critical_issues', []))}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#f59e0b;">⚠️ Performance Risks</div><ul>{ul(ins.get('performance_risks', []))}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#22c55e;">✅ Positive Improvements</div><ul>{ul(ins.get('positive_improvements', []))}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#a78bfa;">🔎 Areas of Concern</div><ul>{ul(ins.get('areas_of_concern', []))}</ul></div>"""

def recs_html(recs):
    if hasattr(recs, "dict"):
        recs = recs.dict()
    def blk(items, color, icon, label):
        h = f'<div style="margin-bottom:14px;"><div style="font-size:12px;font-weight:700;color:{color};margin-bottom:6px;">{icon} {label}</div>'
        for item in items:
            h += f'<div class="rec" style="border-color:{color};"><span>{icon}</span><div style="font-size:12px;color:#cbd5e1;">{item}</div></div>'
        return h + "</div>"
    return (
        blk(recs.get("critical", []), "#ef4444", "🔴", "Critical Priority") +
        blk(recs.get("medium", []),   "#f59e0b", "🟡", "Medium Priority")   +
        blk(recs.get("low", []),      "#22c55e", "🟢", "Low Priority")
    )

def inject(ins):
    if not Path(DASH_PATH).exists():
        print(f"[ai_client] Warning: {DASH_PATH} not found, skipping HTML injection.")
        return

    with open(DASH_PATH, encoding="utf-8") as f:
        html = f.read()

    # 1. Management summary
    html = html.replace(
        "Generating AI insights... please wait.",
        ins.get("management_summary", "")
    )

    # 2. Insights grid — replace content inside AI-INSIGHTS markers
    start_ins = "<!-- AI-INSIGHTS-START -->"
    end_ins   = "<!-- AI-INSIGHTS-END -->"
    if start_ins in html and end_ins in html:
        p1 = html.find(start_ins) + len(start_ins)
        p2 = html.find(end_ins, p1)
        html = html[:p1] + f'\n  <div class="ins" id="ai-insights">{insights_html(ins)}\n  </div>\n  ' + html[p2:]

    # 3. Recommendations — replace content inside AI-RECS markers
    start_recs = "<!-- AI-RECS-START -->"
    end_recs   = "<!-- AI-RECS-END -->"
    if start_recs in html and end_recs in html:
        p1 = html.find(start_recs) + len(start_recs)
        p2 = html.find(end_recs, p1)
        html = html[:p1] + f'\n  <div id="ai-recs">\n{recs_html(ins.get("recommendations", {}))}\n  </div>\n  ' + html[p2:]

    with open(DASH_PATH, "w", encoding="utf-8") as f:
        f.write(html)


# ── 5. Main Entrypoint ────────────────────────────────────────────────────────
def main():
    if not Path(SUMMARY_PATH).exists():
        print(f"[ai_client] Error: {SUMMARY_PATH} not found.")
        return

    print(f"[ai_client] Reading: {SUMMARY_PATH}")
    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)

    # Try FastAPI service first
    ins = call_fastapi_service(summary)
    source = "FastAPI AI Microservice" if ins else None

    # Fallback to direct Claude API or local rule engine
    if not ins:
        prompt = build_prompt(summary)
        ins = call_claude(prompt)
        source = "Direct Claude API" if ins else "Local Fallback Engine"
        if not ins:
            ins = fallback(summary)

    print(f"[ai_client] AI Report Source: {source}")

    # Persist the full AI report JSON for downstream notification/audit
    with open(AI_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(ins, f, indent=2)
    print(f"[ai_client] Saved AI Report: {AI_REPORT_PATH}")

    # Inject into dashboard.html
    inject(ins)
    print(f"[ai_client] Successfully injected AI narrative into: {DASH_PATH}")


if __name__ == "__main__":
    main()
