"""
claude_insights.py
Reads summary.json → calls Claude API → injects AI narrative into dashboard.html
Single API call, ~700 tokens. Fails gracefully — pipeline always continues.
"""
import os, json, re
import urllib.request, urllib.error
from pathlib import Path

OUTPUT_DIR    = os.environ.get("OUTPUT_DIR",         "reports")
API_KEY       = os.environ.get("ANTHROPIC_API_KEY",  "")
TEST_NAME     = os.environ.get("TEST_NAME",          "Performance Test")
SUMMARY_PATH  = f"{OUTPUT_DIR}/summary.json"
DASH_PATH     = f"{OUTPUT_DIR}/dashboard.html"

# ── Build compact prompt ──────────────────────────────────────────────────────
def build_prompt(s):
    trx_lines = []
    for t in s["transactions"]:
        trx_lines.append(
            f"  {t['name']}: P90={t['p90_rt']}ms(tgt={t['rt_target']}ms) "
            f"TPH={t['tph']}(tgt={t['tph_target']}) Err%={t['error_pct']} "
            f"RT={t['rt_status']} TPH={t['tph_status']}"
        )
    return f"""You are a senior performance engineer. Analyze these JMeter results for {TEST_NAME}.

SUMMARY: Result={s['overall_result']} Score={s['perf_score']}/100(Grade {s['perf_grade']}) \
Stability={s['stab_score']}/100({s['stab_status']}) \
Transactions: {s['total']} total|{s['passed']} pass|{s['partial']} partial|{s['failed']} fail \
AvgRT={s['avg_rt']}ms AvgTPH={s['avg_tph']} AvgErr={s['avg_error_pct']}%

TRANSACTIONS:
{chr(10).join(trx_lines)}

Return ONLY valid JSON (no markdown, no backticks):
{{"management_summary":"3-4 sentence executive paragraph. Direct and specific.",
"key_findings":["finding1","finding2","finding3"],
"critical_issues":["issue1","issue2"],
"performance_risks":["risk1","risk2","risk3"],
"positive_improvements":["positive1","positive2"],
"areas_of_concern":["concern1","concern2"],
"recommendations":{{"critical":["action1","action2"],"medium":["action1","action2"],"low":["action1"]}}}}"""

# ── Call Claude API ───────────────────────────────────────────────────────────
def call_claude(prompt):
    if not API_KEY:
        print("[claude_insights] ANTHROPIC_API_KEY not set — using fallback")
        return None
    payload = json.dumps({
        "model":"claude-sonnet-4-6","max_tokens":1000,
        "messages":[{"role":"user","content":prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"Content-Type":"application/json","x-api-key":API_KEY,"anthropic-version":"2023-06-01"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())["content"][0]["text"].strip()
            raw = re.sub(r'^```json\s*|^```\s*|```$','',raw,flags=re.MULTILINE).strip()
            return json.loads(raw)
    except Exception as e:
        print(f"[claude_insights] API error: {e} — using fallback")
        return None

# ── Fallback insights ─────────────────────────────────────────────────────────
def fallback(s):
    red_names = [t["name"] for t in s["transactions"] if t["overall_status"]=="RED"][:3]
    red_str   = ", ".join(red_names) if red_names else "none"
    return {
        "management_summary": (
            f"The {TEST_NAME} resulted in {s['overall_result']} with a Performance Score of "
            f"{s['perf_score']}/100 (Grade {s['perf_grade']}). {s['failed']} transaction(s) "
            f"breached SLA thresholds: {red_str}. {s['partial']} transaction(s) are in warning "
            f"state. Average error rate of {s['avg_error_pct']}% requires immediate attention."
        ),
        "key_findings": [
            f"Overall result: {s['overall_result']} — {s['failed']} RED, {s['partial']} AMBER transactions",
            f"Performance Score {s['perf_score']}/100 (Grade {s['perf_grade']}) — {s['perf_status']}",
            f"Stability Score {s['stab_score']}/100 — {s['stab_status']}",
        ],
        "critical_issues": [
            f"SLA breaches on: {red_str}" if red_names else "No critical SLA breaches",
            f"Error rate at {s['avg_error_pct']}% — root cause investigation required",
        ],
        "performance_risks": [
            "RED transactions indicate potential production stability risk",
            "High response time variance signals infrastructure bottlenecks",
            "Throughput shortfalls may impact SLA commitments under peak load",
        ],
        "positive_improvements": [
            f"{s['passed']} of {s['total']} transactions meeting all SLA targets",
            "Stable transactions show consistent and predictable response times",
        ],
        "areas_of_concern": [
            "Payment and checkout flows show highest error concentration",
            "Response time spikes under concurrent load need investigation",
        ],
        "recommendations": {
            "critical": [
                "Resolve RED transaction failures immediately — block release until fixed",
                "Review connection pool sizing and DB query plans for slow transactions",
            ],
            "medium": [
                "Profile AMBER transactions — identify and fix bottlenecks before next sprint",
                "Add retry logic and circuit breakers for external service dependencies",
            ],
            "low": [
                "Consider caching for frequently accessed read-only endpoints",
            ],
        },
    }

# ── Inject into HTML ──────────────────────────────────────────────────────────
def ul(items):
    return "".join(f"<li>{i}</li>" for i in items)

def insights_html(ins):
    return f"""
    <div class="ins-c"><div class="ttl" style="color:#3b82f6;">🔍 Key Findings</div><ul>{ul(ins['key_findings'])}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#ef4444;">🚨 Critical Issues</div><ul>{ul(ins['critical_issues'])}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#f59e0b;">⚠️ Performance Risks</div><ul>{ul(ins['performance_risks'])}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#22c55e;">✅ Positive Improvements</div><ul>{ul(ins['positive_improvements'])}</ul></div>
    <div class="ins-c"><div class="ttl" style="color:#a78bfa;">🔎 Areas of Concern</div><ul>{ul(ins['areas_of_concern'])}</ul></div>"""

def recs_html(recs):
    def blk(items, color, icon, label):
        h = f'<div style="margin-bottom:14px;"><div style="font-size:12px;font-weight:700;color:{color};margin-bottom:6px;">{icon} {label}</div>'
        for item in items:
            h += f'<div class="rec" style="border-color:{color};"><span>{icon}</span><div style="font-size:12px;color:#cbd5e1;">{item}</div></div>'
        return h + "</div>"
    return (blk(recs.get("critical",[]),"#ef4444","🔴","Critical Priority") +
            blk(recs.get("medium",  []),"#f59e0b","🟡","Medium Priority")   +
            blk(recs.get("low",     []),"#22c55e","🟢","Low Priority"))

def inject(ins):
    with open(DASH_PATH) as f:
        html = f.read()

    # 1. Management summary
    html = html.replace(
        "Generating AI insights... please wait.",
        ins["management_summary"]
    )

    # 2. Insights grid — replace entire div content
    start_tag = '<div class="ins" id="ai-insights">'
    end_tag   = '</div>\n\n<!-- RECOMMENDATIONS -->'
    i1 = html.find(start_tag)
    i2 = html.find(end_tag, i1)
    if i1 != -1 and i2 != -1:
        html = html[:i1] + start_tag + insights_html(ins) + "\n  </div>\n\n<!-- RECOMMENDATIONS -->" + html[i2+len(end_tag):]

    # 3. Recommendations — replace content inside ai-recs div
    rec_start = '<div id="ai-recs">'
    rec_end   = '</div>\n</div>\n\n'
    r1 = html.find(rec_start)
    r2 = html.find(rec_end, r1)
    if r1 != -1 and r2 != -1:
        html = html[:r1] + rec_start + "\n" + recs_html(ins["recommendations"]) + "\n</div>\n</div>\n\n" + html[r2+len(rec_end):]

    with open(DASH_PATH, "w") as f:
        f.write(html)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[claude_insights] Reading: {SUMMARY_PATH}")
    with open(SUMMARY_PATH) as f:
        summary = json.load(f)
    prompt  = build_prompt(summary)
    ins     = call_claude(prompt) or fallback(summary)
    source  = "Claude API" if API_KEY else "fallback rules"
    print(f"[claude_insights] Insights source: {source}")
    inject(ins)
    print(f"[claude_insights] Injected into: {DASH_PATH}")

if __name__ == "__main__":
    main()
