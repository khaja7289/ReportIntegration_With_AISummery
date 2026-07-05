"""
generate_dashboard.py — PetStore Performance Framework
Reads: JMeter aggregate CSV + errors CSV + SLA JSON
Output: dashboard.html + summary.json
"""

import os, json, csv
import pandas as pd
from datetime import datetime
from pathlib import Path

AGGREGATE_REPORT = os.environ.get("AGGREGATE_REPORT", "results/aggregate_report.csv")
ERROR_LOG        = os.environ.get("ERROR_LOG",        "results/errors.csv")
SLA_FILE         = os.environ.get("SLA_FILE",         "config/sla.json")
TEST_NAME        = os.environ.get("TEST_NAME",        "Performance Test")
REPORT_MODE      = os.environ.get("REPORT_MODE",      "sla")
GRAFANA_URL      = os.environ.get("GRAFANA_URL",      "")
OUTPUT_DIR       = os.environ.get("OUTPUT_DIR",       "reports")
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# ── Thresholds ────────────────────────────────────────────────────────────────
RT_AMBER = 110; RT_RED = 120
TPH_OVER = 110; TPH_GREEN_MIN = 90; TPH_AMBER_MIN = 80

# ══════════════════════════════════════════════════════════════════════════════
# PARSE
# ══════════════════════════════════════════════════════════════════════════════
def parse_aggregate(path):
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    rename = {
        "Label":"transaction","# Samples":"samples","Average":"avg_rt",
        "Median":"p50_rt","90% Line":"p90_rt","95% Line":"p95_rt",
        "99% Line":"p99_rt","Min":"min_rt","Max":"max_rt",
        "Error%":"error_pct","Throughput":"tps","Received KB/sec":"rcv_kb","Sent KB/sec":"snt_kb"
    }
    df.rename(columns={k:v for k,v in rename.items() if k in df.columns}, inplace=True)
    df["error_pct"] = df["error_pct"].astype(str).str.replace("%","").astype(float)
    df["tph"]       = df["tps"] * 3600
    df["p80_rt"]    = ((df["p50_rt"] + df["p90_rt"]) / 2).round(1)
    df["error_count"] = (df["samples"] * df["error_pct"] / 100).round(0).astype(int)
    df = df[~df["transaction"].str.upper().str.contains("TOTAL", na=False)]
    return df.reset_index(drop=True)

def parse_errors(path):
    if not Path(path).exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    rename = {"label":"transaction","responseCode":"response_code",
              "responseMessage":"response_message","failureMessage":"failure_message","elapsed":"elapsed"}
    df.rename(columns={k:v for k,v in rename.items() if k in df.columns}, inplace=True)
    for c in ["transaction","response_code","response_message","failure_message"]:
        if c not in df.columns: df[c] = ""
    grp = (df.groupby(["transaction","response_code","response_message","failure_message"])
             .size().reset_index(name="count").sort_values("count", ascending=False))
    return grp

def parse_sla(path):
    with open(path) as f: data = json.load(f)
    return {t["name"]:{"rt_target":t["response_time_target"],"tph_target":t["tph_target"]}
            for t in data.get("transactions",[])}

# ══════════════════════════════════════════════════════════════════════════════
# STATUS LOGIC
# ══════════════════════════════════════════════════════════════════════════════
def rt_status(p90, target):
    if target <= 0: return "N/A", 100.0
    pct = (p90 / target) * 100
    ach = round((target / p90) * 100, 1) if p90 > 0 else 100.0
    if pct <= RT_AMBER:   return "GREEN", ach
    elif pct <= RT_RED:   return "AMBER", ach
    else:                 return "RED",   ach

def tph_status(tph, target):
    if target <= 0: return "N/A", 100.0, "N/A"
    pct = round((tph / target) * 100, 1)
    if pct > TPH_OVER:        return "GREEN", pct, "Over Achieved"
    elif pct >= TPH_GREEN_MIN: return "GREEN", pct, "Achieved"
    elif pct >= TPH_AMBER_MIN: return "AMBER", pct, "Partially Achieved"
    else:                      return "RED",   pct, "Not Achieved"

def overall_status(rt_s, tph_s):
    s = {rt_s, tph_s}
    if "RED" in s:   return "RED"
    if "AMBER" in s: return "AMBER"
    return "GREEN"

def error_impact(err_pct):
    if err_pct >= 5: return "HIGH"
    if err_pct >= 1: return "MEDIUM"
    return "LOW"

# ══════════════════════════════════════════════════════════════════════════════
# BUILD TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════
def build_transactions(agg_df, sla):
    rows = []
    for _, r in agg_df.iterrows():
        name = r["transaction"]
        tgt  = sla.get(name, {"rt_target":0,"tph_target":0})
        p90  = round(float(r.get("p90_rt",0)),1)
        tph  = round(float(r.get("tph",0)),1)
        err  = round(float(r.get("error_pct",0)),2)
        rt_s, rt_ach   = rt_status(p90, tgt["rt_target"])
        tph_s, tph_ach, tph_lbl = tph_status(tph, tgt["tph_target"])
        rows.append({
            "name": name,
            "samples":     int(r.get("samples",0)),
            "error_count": int(r.get("error_count",0)),
            "error_pct":   err,
            "avg_rt":      round(float(r.get("avg_rt",0)),1),
            "p80_rt":      round(float(r.get("p80_rt",0)),1),
            "p90_rt":      p90,
            "p95_rt":      round(float(r.get("p95_rt",0)),1),
            "max_rt":      round(float(r.get("max_rt",0)),1),
            "tph":         round(tph,1),
            "rt_target":   tgt["rt_target"],
            "tph_target":  tgt["tph_target"],
            "rt_status":   rt_s, "tph_status": tph_s,
            "tph_label":   tph_lbl, "overall_status": overall_status(rt_s, tph_s),
            "rt_ach_pct":  rt_ach, "tph_ach_pct": tph_ach,
            "impact":      error_impact(err),
        })
    order = {"RED":0,"AMBER":1,"GREEN":2,"N/A":3}
    rows.sort(key=lambda x: order.get(x["overall_status"],4))
    return rows

# ══════════════════════════════════════════════════════════════════════════════
# SCORES
# ══════════════════════════════════════════════════════════════════════════════
def perf_score(trx):
    if not trx: return 0,"D","Failed"
    n = len(trx)
    wt = {"GREEN":1.0,"AMBER":0.5,"RED":0.0,"N/A":1.0}
    rt  = sum(wt.get(t["rt_status"],0) for t in trx)/n*50
    tp  = sum(wt.get(t["tph_status"],0) for t in trx)/n*30
    er  = max(0,(1-sum(t["error_pct"] for t in trx)/n/100))*20
    s   = round(max(0,min(100, rt+tp+er)),1)
    if s>=90: return s,"A+","Excellent"
    if s>=80: return s,"A","Good"
    if s>=70: return s,"B","Acceptable"
    if s>=60: return s,"C","Needs Attention"
    return s,"D","Failed"

def stab_score(trx):
    if not trx: return 0,"Unstable"
    e,sp,g = [],[],[]
    for t in trx:
        e.append(max(0,100-t["error_pct"]*5))
        sp.append(max(0,100-((t["p90_rt"]-t["p80_rt"])/t["p80_rt"]*100*2)) if t["p80_rt"]>0 else 100)
        g.append(max(0,100-((t["max_rt"]-t["avg_rt"])/t["avg_rt"]*100)) if t["avg_rt"]>0 else 100)
    s = round(max(0,min(100,(sum(e)/len(e)+sum(sp)/len(sp)+sum(g)/len(g))/3)),1)
    if s>=90: return s,"Highly Stable"
    if s>=80: return s,"Stable"
    if s>=70: return s,"Moderately Stable"
    return s,"Unstable"

def overall_result(trx):
    st = [t["overall_status"] for t in trx]
    if "RED" in st:   return "FAIL"
    if "AMBER" in st: return "PARTIAL PASS"
    return "PASS"

# ══════════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ══════════════════════════════════════════════════════════════════════════════
STATUS_COLORS = {
    "GREEN":"#22c55e","AMBER":"#f59e0b","RED":"#ef4444","N/A":"#64748b",
    "PASS":"#22c55e","PARTIAL PASS":"#f59e0b","FAIL":"#ef4444",
    "HIGH":"#ef4444","MEDIUM":"#f59e0b","LOW":"#22c55e",
    "Over Achieved":"#16a34a","Achieved":"#22c55e",
    "Partially Achieved":"#f59e0b","Not Achieved":"#ef4444",
}
def pill(label, bold=False):
    bg = STATUS_COLORS.get(label,"#64748b")
    fw = "700" if bold else "600"
    return f'<span style="background:{bg};color:#fff;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:{fw};">{label}</span>'

def result_badge(r):
    icons={"PASS":"✅","PARTIAL PASS":"⚠️","FAIL":"❌"}
    bg=STATUS_COLORS.get(r,"#64748b")
    return f'<span style="background:{bg};color:#fff;padding:8px 22px;border-radius:20px;font-size:15px;font-weight:700;">{icons.get(r,"")} {r}</span>'

def grade_color(g):
    return {"A+":"#22c55e","A":"#4ade80","B":"#facc15","C":"#f97316","D":"#ef4444"}.get(g,"#64748b")

# ══════════════════════════════════════════════════════════════════════════════
# BUILD HTML
# ══════════════════════════════════════════════════════════════════════════════
def build_html(summary, trx, errors_df):
    # Pre-compute chart data
    passed  = summary["passed"]; partial = summary["partial"]; failed = summary["failed"]
    rt_g = sum(1 for t in trx if t["rt_status"]=="GREEN")
    rt_a = sum(1 for t in trx if t["rt_status"]=="AMBER")
    rt_r = sum(1 for t in trx if t["rt_status"]=="RED")
    tp_g = sum(1 for t in trx if t["tph_status"]=="GREEN")
    tp_a = sum(1 for t in trx if t["tph_status"]=="AMBER")
    tp_r = sum(1 for t in trx if t["tph_status"]=="RED")
    top10s  = sorted(trx, key=lambda x:x["p90_rt"], reverse=True)[:10]
    top10ta = sorted(trx, key=lambda x:x["tph_ach_pct"])[:10]
    top5s   = sorted(trx, key=lambda x:x["p90_rt"], reverse=True)[:5]
    top5t   = sorted(trx, key=lambda x:x["tph_ach_pct"])[:5]
    top5e   = sorted(trx, key=lambda x:x["error_pct"], reverse=True)[:5]
    ra_rt   = [t for t in trx if t["rt_status"]  in ("RED","AMBER")]
    ra_tph  = [t for t in trx if t["tph_status"] in ("RED","AMBER")]

    pc  = summary["perf_score"]; pg = summary["perf_grade"]; sc = summary["stab_score"]
    res = summary["overall_result"]
    pc_col = grade_color(pg)
    res_col= STATUS_COLORS.get(res,"#64748b")

    def js_labels(lst): return json.dumps([t["name"] for t in lst])
    def js_vals(lst,k):  return json.dumps([t[k] for t in lst])

    # ── SLA Table Rows ──────────────────────────────────────────────────────
    sla_rows = ""
    for t in trx:
        sla_rows += f"""<tr>
          <td style="font-weight:500;white-space:nowrap;">{t['name']}</td>
          <td class="tc">{t['rt_target']} ms</td>
          <td class="tc">{t['avg_rt']} ms</td>
          <td class="tc">{t['p80_rt']} ms</td>
          <td class="tc" style="font-weight:700;">{t['p90_rt']} ms</td>
          <td class="tc">{t['p95_rt']} ms</td>
          <td class="tc">{t['max_rt']} ms</td>
          <td class="tc">{pill(t['rt_status'])}</td>
          <td class="tc">{t['tph_target']}</td>
          <td class="tc">{t['tph']}</td>
          <td class="tc">{pill(t['tph_label'])}</td>
          <td class="tc" style="font-weight:600;">{t['rt_ach_pct']}%</td>
          <td class="tc" style="font-weight:600;">{t['tph_ach_pct']}%</td>
          <td class="tc">{pill(t['overall_status'])}</td>
        </tr>"""

    # ── Error Table Rows ────────────────────────────────────────────────────
    err_rows = ""
    if not errors_df.empty:
        for _, r in errors_df.iterrows():
            trx_data = next((t for t in trx if t["name"]==r.get("transaction","")), None)
            ep = trx_data["error_pct"] if trx_data else 0
            err_rows += f"""<tr>
              <td style="white-space:nowrap;">{r.get('transaction','')}</td>
              <td class="tc"><code style="background:#0f172a;padding:2px 6px;border-radius:4px;color:#f87171;">{r.get('response_code','')}</code></td>
              <td>{r.get('response_message','')}</td>
              <td style="font-size:12px;color:#94a3b8;max-width:280px;">{r.get('failure_message','')}</td>
              <td class="tc" style="font-weight:700;color:#f87171;">{r.get('count',0)}</td>
              <td class="tc">{ep}%</td>
              <td class="tc">{pill(error_impact(ep))}</td>
            </tr>"""
    else:
        err_rows = '<tr><td colspan="7" class="tc" style="color:#22c55e;padding:20px;">✅ No errors recorded</td></tr>'

    # ── RT Exceptions ───────────────────────────────────────────────────────
    rt_exc = ""
    for t in sorted(ra_rt, key=lambda x:x["rt_ach_pct"]):
        gap = round(t["p90_rt"]-t["rt_target"],1) if t["rt_target"]>0 else 0
        reason = "Response time exceeded SLA by >20%" if t["rt_status"]=="RED" else "Response time within warning threshold"
        action = "Investigate DB queries and backend API latency" if t["rt_status"]=="RED" else "Monitor and optimise before next run"
        rt_exc += f"""<tr>
          <td style="white-space:nowrap;">{t['name']}</td>
          <td class="tc">{t['rt_target']} ms</td>
          <td class="tc" style="font-weight:700;">{t['p90_rt']} ms</td>
          <td class="tc" style="color:#f87171;">+{gap} ms</td>
          <td class="tc">{t['rt_ach_pct']}%</td>
          <td class="tc">{pill(t['rt_status'])}</td>
          <td style="font-size:12px;">{reason}</td>
          <td style="font-size:12px;color:#94a3b8;">{action}</td>
        </tr>"""
    if not rt_exc:
        rt_exc = '<tr><td colspan="8" class="tc" style="color:#22c55e;padding:20px;">✅ No Response Time Exceptions</td></tr>'

    # ── TPH Exceptions ──────────────────────────────────────────────────────
    tph_exc = ""
    for t in sorted(ra_tph, key=lambda x:x["tph_ach_pct"]):
        gap = round(t["tph"]-t["tph_target"],1)
        reason = "Throughput not achieved — possible bottleneck" if t["tph_status"]=="RED" else "Throughput partially achieved"
        action = "Review thread pool, connection limits and server capacity"
        tph_exc += f"""<tr>
          <td style="white-space:nowrap;">{t['name']}</td>
          <td class="tc">{t['tph_target']}</td>
          <td class="tc" style="font-weight:700;">{t['tph']}</td>
          <td class="tc" style="color:#f87171;">{gap}</td>
          <td class="tc">{t['tph_ach_pct']}%</td>
          <td class="tc">{pill(t['tph_status'])}</td>
          <td style="font-size:12px;">{reason}</td>
          <td style="font-size:12px;color:#94a3b8;">{action}</td>
        </tr>"""
    if not tph_exc:
        tph_exc = '<tr><td colspan="8" class="tc" style="color:#22c55e;padding:20px;">✅ No Throughput Exceptions</td></tr>'

    # ── Top Risk Rows ───────────────────────────────────────────────────────
    def risk_rows(items, key, unit):
        rows=""
        for i,t in enumerate(items,1):
            rows+=f'<tr><td class="tc" style="color:#f59e0b;font-weight:700;">#{i}</td><td style="font-size:12px;">{t["name"]}</td><td class="tc" style="font-weight:600;">{t[key]} {unit}</td><td class="tc">{pill(t["overall_status"])}</td></tr>'
        return rows

    # ── Grafana ─────────────────────────────────────────────────────────────
    grafana_html = ""
    if GRAFANA_URL:
        grafana_html = f"""
        <div class="card">
          <h2 class="sec-title">📈 Grafana Dashboard</h2>
          <p style="color:#94a3b8;margin-bottom:16px;">Real-time metrics for this test execution window.</p>
          <a href="{GRAFANA_URL}" target="_blank" class="btn-link">🔗 Open Grafana Dashboard</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{TEST_NAME} — Performance Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;}}
.hdr{{background:linear-gradient(135deg,#1e3a5f,#1e293b);padding:20px 32px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center;}}
.hdr h1{{font-size:20px;font-weight:700;color:#f1f5f9;}}
.hdr .meta{{font-size:12px;color:#94a3b8;margin-top:4px;}}
.badge{{padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600;background:#3b82f6;color:#fff;}}
.wrap{{max-width:1500px;margin:0 auto;padding:20px 28px;}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:20px;}}
.kpi-c{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:18px;text-align:center;}}
.kpi-c .lbl{{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px;}}
.kpi-c .val{{font-size:26px;font-weight:700;color:#f1f5f9;}}
.kpi-c .sub{{font-size:11px;color:#64748b;margin-top:3px;}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px;margin-bottom:20px;}}
.sec-title{{font-size:15px;font-weight:700;color:#f1f5f9;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid #334155;}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:18px;margin-bottom:20px;}}
.ch-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:18px;}}
.ch-card h3{{font-size:13px;color:#94a3b8;margin-bottom:14px;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th{{background:#0f172a;color:#64748b;padding:9px 11px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid #334155;position:sticky;top:0;z-index:1;}}
td{{padding:9px 11px;border-bottom:1px solid #1e293b44;color:#e2e8f0;vertical-align:middle;}}
tr:hover td{{background:#1e3a5f22;}}
.tc{{text-align:center;}}
.tw{{overflow-x:auto;border-radius:8px;border:1px solid #334155;}}
.ins{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;}}
.ins-c{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;}}
.ins-c .ttl{{font-size:13px;font-weight:700;margin-bottom:8px;}}
.ins-c ul{{list-style:none;}}
.ins-c ul li{{font-size:12px;color:#94a3b8;padding:3px 0;border-bottom:1px solid #1e293b33;}}
.ins-c ul li::before{{content:"→ ";color:#3b82f6;}}
.rec{{display:flex;align-items:flex-start;gap:10px;padding:11px;background:#0f172a;border-radius:7px;margin-bottom:7px;border-left:3px solid;}}
.risks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;}}
.risk-c{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:14px;}}
.risk-c h4{{font-size:12px;color:#94a3b8;margin-bottom:10px;}}
.sum-box{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:18px;font-size:13px;line-height:1.8;color:#cbd5e1;}}
.btn-link{{display:inline-block;background:#3b82f6;color:#fff;padding:9px 20px;border-radius:7px;text-decoration:none;font-weight:600;font-size:13px;}}
code{{background:#0f172a;padding:2px 5px;border-radius:3px;font-size:11px;}}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <div style="display:flex;align-items:center;gap:10px;">
      <h1>⚡ {TEST_NAME}</h1>
      <span class="badge">{REPORT_MODE.upper()} MODE</span>
      <span class="badge" style="background:#475569;">JMeter</span>
    </div>
    <div class="meta">Generated: {summary['generated_at']} &nbsp;|&nbsp; {summary['total']} Transactions &nbsp;|&nbsp; PetStore Application</div>
  </div>
  <div>{result_badge(res)}</div>
</div>

<div class="wrap">

<!-- KPI CARDS -->
<div class="kpi">
  <div class="kpi-c"><div class="lbl">Transactions</div><div class="val">{summary['total']}</div></div>
  <div class="kpi-c"><div class="lbl">✅ Passed</div><div class="val" style="color:#22c55e;">{summary['passed']}</div></div>
  <div class="kpi-c"><div class="lbl">⚠️ Partial</div><div class="val" style="color:#f59e0b;">{summary['partial']}</div></div>
  <div class="kpi-c"><div class="lbl">❌ Failed</div><div class="val" style="color:#ef4444;">{summary['failed']}</div></div>
  <div class="kpi-c"><div class="lbl">Avg Response Time</div><div class="val">{summary['avg_rt']}</div><div class="sub">ms</div></div>
  <div class="kpi-c"><div class="lbl">Avg TPH</div><div class="val">{summary['avg_tph']}</div><div class="sub">trans/hr</div></div>
  <div class="kpi-c"><div class="lbl">Avg Error %</div><div class="val" style="color:{'#ef4444' if summary['avg_error_pct']>1 else '#22c55e'};">{summary['avg_error_pct']}%</div></div>
  <div class="kpi-c"><div class="lbl">Perf Score</div><div class="val" style="color:{pc_col};">{pc}</div><div class="sub">Grade {pg} — {summary['perf_status']}</div></div>
  <div class="kpi-c"><div class="lbl">Stability Score</div><div class="val" style="color:#3b82f6;">{sc}</div><div class="sub">{summary['stab_status']}</div></div>
</div>

<!-- MANAGEMENT SUMMARY -->
<div class="card">
  <h2 class="sec-title">📋 Management Summary</h2>
  <div class="sum-box" id="ai-summary">Generating AI insights... please wait.</div>
</div>

<!-- SLA COMPARISON TABLE -->
<div class="card">
  <h2 class="sec-title">📊 SLA Comparison Table &nbsp;<span style="font-size:11px;color:#64748b;font-weight:400;">★ P90 is primary KPI &nbsp;|&nbsp; Sorted worst-first</span></h2>
  <div class="tw">
    <table>
      <thead><tr>
        <th>Transaction</th><th>Target RT</th><th>Avg RT</th><th>P80</th>
        <th>P90 ★</th><th>P95</th><th>Max RT</th><th>RT Status</th>
        <th>Target TPH</th><th>Actual TPH</th><th>TPH Status</th>
        <th>RT Ach%</th><th>TPH Ach%</th><th>Overall</th>
      </tr></thead>
      <tbody>{sla_rows}</tbody>
    </table>
  </div>
</div>

<!-- ERROR TRANSACTION TABLE -->
<div class="card">
  <h2 class="sec-title">🔴 Error Transaction Table</h2>
  <div class="tw">
    <table>
      <thead><tr>
        <th>Transaction</th><th>Code</th><th>Response Message</th>
        <th>Failure Message</th><th>Count</th><th>Error %</th><th>Impact</th>
      </tr></thead>
      <tbody>{err_rows}</tbody>
    </table>
  </div>
</div>

<!-- RT EXCEPTIONS -->
<div class="card">
  <h2 class="sec-title">⏱️ Response Time Exceptions &nbsp;<span style="font-size:11px;color:#64748b;font-weight:400;">RED + AMBER — ranked by worst deviation</span></h2>
  <div class="tw">
    <table>
      <thead><tr>
        <th>Transaction</th><th>Target RT</th><th>P90 Actual</th><th>Gap</th>
        <th>Achievement%</th><th>Status</th><th>Reason</th><th>Action</th>
      </tr></thead>
      <tbody>{rt_exc}</tbody>
    </table>
  </div>
</div>

<!-- TPH EXCEPTIONS -->
<div class="card">
  <h2 class="sec-title">🚀 Throughput Exceptions &nbsp;<span style="font-size:11px;color:#64748b;font-weight:400;">RED + AMBER — ranked by worst deviation</span></h2>
  <div class="tw">
    <table>
      <thead><tr>
        <th>Transaction</th><th>Target TPH</th><th>Actual TPH</th><th>Gap</th>
        <th>Achievement%</th><th>Status</th><th>Reason</th><th>Action</th>
      </tr></thead>
      <tbody>{tph_exc}</tbody>
    </table>
  </div>
</div>

<!-- TOP RISKS -->
<div class="card">
  <h2 class="sec-title">⚠️ Top Performance Risks</h2>
  <div class="risks">
    <div class="risk-c">
      <h4>🐢 Top 5 Slowest (P90)</h4>
      <table><thead><tr><th>#</th><th>Transaction</th><th>P90</th><th>Status</th></tr></thead>
      <tbody>{risk_rows(top5s,'p90_rt','ms')}</tbody></table>
    </div>
    <div class="risk-c">
      <h4>📉 Top 5 TPH Underachievers</h4>
      <table><thead><tr><th>#</th><th>Transaction</th><th>Ach%</th><th>Status</th></tr></thead>
      <tbody>{risk_rows(top5t,'tph_ach_pct','%')}</tbody></table>
    </div>
    <div class="risk-c">
      <h4>💥 Top 5 Highest Error Rate</h4>
      <table><thead><tr><th>#</th><th>Transaction</th><th>Err%</th><th>Status</th></tr></thead>
      <tbody>{risk_rows(top5e,'error_pct','%')}</tbody></table>
    </div>
  </div>
</div>

<!-- CHARTS -->
<div class="charts">
  <div class="ch-card"><h3>Pass / Partial / Fail</h3><canvas id="cDonut" height="200"></canvas></div>
  <div class="ch-card"><h3>Response Time Status</h3><canvas id="cRT" height="200"></canvas></div>
  <div class="ch-card"><h3>Throughput Status</h3><canvas id="cTPH" height="200"></canvas></div>
  <div class="ch-card"><h3>Top 10 Slowest (P90 ms)</h3><canvas id="cSlow" height="200"></canvas></div>
  <div class="ch-card"><h3>Top 10 TPH Achievement %</h3><canvas id="cTPHAch" height="200"></canvas></div>
  <div class="ch-card"><h3>Error % by Transaction</h3><canvas id="cErr" height="200"></canvas></div>
</div>

<!-- AI INSIGHTS -->
<div class="card">
  <h2 class="sec-title">🤖 AI Insights</h2>
  <div class="ins" id="ai-insights">
    <div class="ins-c"><div class="ttl" style="color:#3b82f6;">🔍 Key Findings</div><ul><li>Pending AI analysis...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#ef4444;">🚨 Critical Issues</div><ul><li>Pending AI analysis...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#f59e0b;">⚠️ Performance Risks</div><ul><li>Pending AI analysis...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#22c55e;">✅ Positive Improvements</div><ul><li>Pending AI analysis...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#a78bfa;">🔎 Areas of Concern</div><ul><li>Pending AI analysis...</li></ul></div>
  </div>
</div>

<!-- RECOMMENDATIONS -->
<div class="card">
  <h2 class="sec-title">💡 Recommendations</h2>
  <div id="ai-recs">
    <div class="rec" style="border-color:#ef4444;"><span>🔴</span><div><strong style="color:#ef4444;">Critical Priority</strong><br><span style="color:#94a3b8;font-size:12px;">Pending AI analysis...</span></div></div>
  </div>
</div>

{grafana_html}

<div style="text-align:center;padding:20px;color:#475569;font-size:11px;border-top:1px solid #334155;margin-top:20px;">
  Performance Analytics Dashboard &nbsp;|&nbsp; {TEST_NAME} &nbsp;|&nbsp; {summary['generated_at']} &nbsp;|&nbsp; JMeter + Claude AI
</div>
</div>

<script>
const CD = {{plugins:{{legend:{{labels:{{color:'#94a3b8',font:{{size:10}}}}}}}},scales:{{x:{{ticks:{{color:'#64748b'}},grid:{{color:'#1e293b'}}}},y:{{ticks:{{color:'#64748b'}},grid:{{color:'#1e293b'}}}}}}}};
new Chart(document.getElementById('cDonut'),{{type:'doughnut',data:{{labels:['Passed','Partial','Failed'],datasets:[{{data:[{passed},{partial},{failed}],backgroundColor:['#22c55e','#f59e0b','#ef4444'],borderWidth:0}}]}},options:{{plugins:{{legend:{{labels:{{color:'#94a3b8'}}}}}}}}}});
new Chart(document.getElementById('cRT'),{{type:'bar',data:{{labels:['GREEN','AMBER','RED'],datasets:[{{data:[{rt_g},{rt_a},{rt_r}],backgroundColor:['#22c55e','#f59e0b','#ef4444'],borderRadius:5}}]}},options:{{...CD,plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('cTPH'),{{type:'bar',data:{{labels:['GREEN','AMBER','RED'],datasets:[{{data:[{tp_g},{tp_a},{tp_r}],backgroundColor:['#22c55e','#f59e0b','#ef4444'],borderRadius:5}}]}},options:{{...CD,plugins:{{legend:{{display:false}}}}}}}});
new Chart(document.getElementById('cSlow'),{{type:'bar',data:{{labels:{js_labels(top10s)},datasets:[{{label:'P90 ms',data:{js_vals(top10s,'p90_rt')},backgroundColor:'#3b82f6',borderRadius:4}}]}},options:{{indexAxis:'y',...CD}}}});
new Chart(document.getElementById('cTPHAch'),{{type:'bar',data:{{labels:{js_labels(top10ta)},datasets:[{{label:'Ach%',data:{js_vals(top10ta,'tph_ach_pct')},backgroundColor:'#a78bfa',borderRadius:4}}]}},options:{{indexAxis:'y',...CD}}}});
new Chart(document.getElementById('cErr'),{{type:'bar',data:{{labels:{js_labels(sorted(trx,key=lambda x:x['error_pct'],reverse=True)[:10])},datasets:[{{label:'Error%',data:{js_vals(sorted(trx,key=lambda x:x['error_pct'],reverse=True)[:10],'error_pct')},backgroundColor:'#f43f5e',borderRadius:4}}]}},options:{{...CD,plugins:{{legend:{{display:false}}}}}}}});
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print(f"[generate_dashboard] Aggregate : {AGGREGATE_REPORT}")
    print(f"[generate_dashboard] Errors    : {ERROR_LOG}")
    print(f"[generate_dashboard] SLA       : {SLA_FILE}")

    agg_df    = parse_aggregate(AGGREGATE_REPORT)
    errors_df = parse_errors(ERROR_LOG)
    sla       = parse_sla(SLA_FILE)
    trx       = build_transactions(agg_df, sla)

    ps, pg, pst = perf_score(trx)
    ss, sst     = stab_score(trx)
    res         = overall_result(trx)
    n           = len(trx)

    summary = {
        "test_name": TEST_NAME, "report_mode": REPORT_MODE,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "grafana_url": GRAFANA_URL,
        "total": n, "passed": sum(1 for t in trx if t["overall_status"]=="GREEN"),
        "partial": sum(1 for t in trx if t["overall_status"]=="AMBER"),
        "failed":  sum(1 for t in trx if t["overall_status"]=="RED"),
        "avg_rt":  round(sum(t["avg_rt"] for t in trx)/n,1) if n else 0,
        "avg_tph": round(sum(t["tph"] for t in trx)/n,1)    if n else 0,
        "avg_error_pct": round(sum(t["error_pct"] for t in trx)/n,2) if n else 0,
        "perf_score": ps, "perf_grade": pg, "perf_status": pst,
        "stab_score": ss, "stab_status": sst, "overall_result": res,
        "top_failed": [t for t in trx if t["overall_status"]=="RED"][:3],
        "transactions": trx,
    }

    with open(f"{OUTPUT_DIR}/summary.json","w") as f:
        json.dump(summary, f, indent=2)
    print(f"[generate_dashboard] summary.json saved")

    html = build_html(summary, trx, errors_df)
    with open(f"{OUTPUT_DIR}/dashboard.html","w") as f:
        f.write(html)
    print(f"[generate_dashboard] dashboard.html saved")
    print(f"[generate_dashboard] Result : {res} | Score : {ps} ({pg}) | Stability : {ss}")

if __name__ == "__main__":
    main()
