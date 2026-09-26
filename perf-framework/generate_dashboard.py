"""
generate_dashboard.py — Performance Testing Framework
Reads: JMeter Simple Data Writer (sdw.csv / raw results) + SLA JSON
Outputs:
  - 01_all_transactions.csv
  - 02_tph_not_achieved.csv (Amber/Red only)
  - 03_sla_90pct_deviation.csv (Amber/Red only)
  - 04_error_transactions.csv
  - summary.json
  - dashboard.html
"""

import os
import json
import re
import pandas as pd
from datetime import datetime
from pathlib import Path

# ── Environment & Paths ───────────────────────────────────────────────────────
RESULTS_DIR      = os.environ.get("RESULTS_DIR",      "artifacts/results")
OUTPUT_DIR       = os.environ.get("OUTPUT_DIR",       "artifacts/reports")
SDW_REPORT       = os.environ.get("SDW_REPORT",       "")
AGGREGATE_REPORT = os.environ.get("AGGREGATE_REPORT", "")
SLA_FILE         = os.environ.get("SLA_FILE",         "perf-tests-petstore/config/sla.json")
TEST_NAME        = os.environ.get("TEST_NAME",        "Performance Test")
GRAFANA_URL      = os.environ.get("GRAFANA_URL",      "")

Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# ── Locate Input Data ─────────────────────────────────────────────────────────
def find_input_file():
    candidates = [
        SDW_REPORT,
        AGGREGATE_REPORT,
        f"{RESULTS_DIR}/sdw.csv",
        f"{RESULTS_DIR}/aggregate_report.csv",
        f"{RESULTS_DIR}/raw.jtl",
        "artifacts/results/sdw.csv",
        "artifacts/results/aggregate_report.csv",
        "sdw.csv",
        "aggregate_report.csv"
    ]
    for c in candidates:
        if c and Path(c).exists() and Path(c).is_file():
            return str(c)
    return candidates[0] if candidates[0] else f"{RESULTS_DIR}/sdw.csv"


def percentile(series, p):
    return series.quantile(p / 100)


# ── Load SLA ──────────────────────────────────────────────────────────────────
def load_sla(path):
    if not Path(path).exists():
        # Fallback check
        alt = Path("config/sla.json")
        if alt.exists():
            path = str(alt)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        t["name"]: {
            "resp": t["response_time_target"],
            "tph": t["tph_target"]
        }
        for t in data.get("transactions", [])
    }


# ── Process Raw Data & Generate 4 Structured CSVs ──────────────────────────────
def process_data(input_csv, sla_map, out_dir):
    print(f"[generate_dashboard] Loading raw results from: {input_csv}")
    agg = pd.read_csv(input_csv)
    agg.columns = agg.columns.str.strip()

    # Normalize column names if needed
    rename_cols = {
        "Label": "label",
        "Elapsed": "elapsed",
        "Success": "success",
        "Timestamp": "timeStamp",
        "ResponseCode": "responseCode",
        "response_code": "responseCode"
    }
    agg.rename(columns={k: v for k, v in rename_cols.items() if k in agg.columns}, inplace=True)

    # Ensure required columns
    if "elapsed" not in agg.columns:
        # If input came from an aggregate report rather than raw samples
        if "Average" in agg.columns:
            agg["elapsed"] = pd.to_numeric(agg["Average"], errors="coerce")
        else:
            raise ValueError("Input CSV missing 'elapsed' column required for percentile analysis.")
    else:
        agg["elapsed"] = pd.to_numeric(agg["elapsed"], errors="coerce")

    if "success" not in agg.columns:
        agg["success"] = True
    else:
        agg["success"] = agg["success"].astype(str).str.strip().str.lower().isin(["true", "1"])

    if "label" not in agg.columns and "transaction" in agg.columns:
        agg["label"] = agg["transaction"]
    agg["label"] = agg["label"].astype(str).str.strip()

    if "timeStamp" not in agg.columns:
        agg["timeStamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if "responseCode" not in agg.columns:
        agg["responseCode"] = "200"

    # Base summary grouped by label
    summary = agg.groupby("label").agg(
        hitcount=("elapsed", "count"),
        avg=("elapsed", "mean"),
        min=("elapsed", "min"),
        max=("elapsed", "max"),
        p80=("elapsed", lambda x: percentile(x, 80)),
        p90=("elapsed", lambda x: percentile(x, 90)),
        errors=("success", lambda x: (x == False).sum())
    ).reset_index()

    summary["error_pct"] = (summary["errors"] / summary["hitcount"]) * 100
    summary = summary.rename(columns={"label": "Transaction", "p80": "80%", "p90": "90%"})
    summary = summary[summary["Transaction"].isin(sla_map.keys())].copy()

    # Round numerical metrics
    summary["90%"] = summary["90%"].round(1)
    summary["80%"] = summary["80%"].round(1)
    summary["avg"] = summary["avg"].round(1)
    summary["min"] = summary["min"].round(1)
    summary["max"] = summary["max"].round(1)
    summary["hitcount"] = summary["hitcount"].astype(int)
    summary["errors"] = summary["errors"].astype(int)
    summary["error_pct"] = summary["error_pct"].round(2)

    # 1. All Transactions
    all_tx = summary[["Transaction", "90%", "80%", "avg", "min", "max", "hitcount", "errors", "error_pct"]].copy()
    all_tx_path = f"{out_dir}/01_all_transactions.csv"
    all_tx.to_csv(all_tx_path, index=False)
    print(f"[generate_dashboard] Wrote: {all_tx_path}")

    # 2. TPH not achieved -> only amber/red
    tph = []
    for _, row in summary.iterrows():
        target_tph = sla_map[row["Transaction"]]["tph"]
        if target_tph:
            achievement = (row["hitcount"] / target_tph) * 100
            if 80 <= achievement <= 89:   # amber
                tph.append([row["Transaction"], row["90%"], row["hitcount"], round(achievement, 2), target_tph, "AMBER"])
            elif achievement < 80:        # red
                tph.append([row["Transaction"], row["90%"], row["hitcount"], round(achievement, 2), target_tph, "RED"])

    tph_df = pd.DataFrame(
        [[r[0], r[1], r[2], r[3]] for r in tph],
        columns=["Transaction", "90%", "hitcount", "TPH%_achieved"]
    )
    tph_path = f"{out_dir}/02_tph_not_achieved.csv"
    tph_df.to_csv(tph_path, index=False)
    print(f"[generate_dashboard] Wrote: {tph_path} ({len(tph)} exceptions)")

    # 3. SLA 90% deviation -> percentage deviation from SLA (amber/red only)
    sla_dev = []
    for _, row in summary.iterrows():
        target_resp = sla_map[row["Transaction"]]["resp"]
        if target_resp:
            ratio = (row["90%"] / target_resp) * 100
            deviation_pct = ratio - 100   # deviation in %
            if 111 <= ratio <= 120:   # amber
                sla_dev.append([row["Transaction"], row["90%"], row["80%"], row["avg"], round(deviation_pct, 2), target_resp, "AMBER"])
            elif ratio > 120:         # red
                sla_dev.append([row["Transaction"], row["90%"], row["80%"], row["avg"], round(deviation_pct, 2), target_resp, "RED"])

    sla_dev_df = pd.DataFrame(
        [[r[0], r[1], r[2], r[3], r[4]] for r in sla_dev],
        columns=["Transaction", "90%", "80%", "avg", "Deviation_from_SLA"]
    )
    sla_dev_path = f"{out_dir}/03_sla_90pct_deviation.csv"
    sla_dev_df.to_csv(sla_dev_path, index=False)
    print(f"[generate_dashboard] Wrote: {sla_dev_path} ({len(sla_dev)} exceptions)")

    # 4. Error transactions -> simplified format
    failures = agg[agg["success"] == False].copy()
    if not failures.empty:
        failures["fail_count"] = 1
        failures_summary = failures.groupby(
            ["timeStamp", "label", "responseCode"]
        ).agg(fail_count=("fail_count", "sum")).reset_index()

        txn_counts = agg.groupby("label")["success"].count()
        failures_summary["fail%"] = failures_summary.apply(
            lambda r: round((r["fail_count"] / txn_counts.get(r["label"], 1)) * 100, 2), axis=1
        )
        failures_summary = failures_summary.rename(columns={"label": "Transaction", "responseCode": "ResponseCode"})
    else:
        failures_summary = pd.DataFrame(columns=["timeStamp", "Transaction", "ResponseCode", "fail_count", "fail%"])

    err_path = f"{out_dir}/04_error_transactions.csv"
    failures_summary.to_csv(err_path, index=False)
    print(f"[generate_dashboard] Wrote: {err_path} ({len(failures_summary)} error groups)")

    return summary, tph, sla_dev, failures_summary


# ── Status and Scoring Engine ─────────────────────────────────────────────────
def evaluate_performance(summary, tph, sla_dev, sla_map):
    tx_detail = []
    red_count = 0
    amber_count = 0
    green_count = 0

    tph_exc_map = {r[0]: r[5] for r in tph}
    sla_dev_map = {r[0]: (r[4], r[6]) for r in sla_dev}

    for _, row in summary.iterrows():
        name = row["Transaction"]
        target_resp = sla_map.get(name, {}).get("resp", 0)
        target_tph = sla_map.get(name, {}).get("tph", 0)
        p90 = row["90%"]
        p80 = row["80%"]
        avg = row["avg"]
        hitcount = row["hitcount"]
        errors = row["errors"]
        err_pct = row["error_pct"]

        # RT status
        if name in sla_dev_map:
            rt_dev, rt_st = sla_dev_map[name]
        else:
            rt_st = "GREEN"
            rt_dev = round(((p90 / target_resp) * 100 - 100), 2) if target_resp > 0 else 0.0

        # TPH status
        if name in tph_exc_map:
            tph_st = tph_exc_map[name]
            ach = round((hitcount / target_tph) * 100, 2) if target_tph > 0 else 100.0
        else:
            tph_st = "GREEN"
            ach = round((hitcount / target_tph) * 100, 2) if target_tph > 0 else 100.0

        # Overall transaction status
        if "RED" in (rt_st, tph_st):
            overall_st = "RED"
            red_count += 1
        elif "AMBER" in (rt_st, tph_st):
            overall_st = "AMBER"
            amber_count += 1
        else:
            overall_st = "GREEN"
            green_count += 1

        tx_detail.append({
            "name": name,
            "p90": p90,
            "p80": p80,
            "avg": avg,
            "min": row["min"],
            "max": row["max"],
            "hitcount": hitcount,
            "errors": errors,
            "error_pct": err_pct,
            "rt_target": target_resp,
            "tph_target": target_tph,
            "rt_status": rt_st,
            "tph_status": tph_st,
            "overall_status": overall_st,
            "rt_deviation_pct": rt_dev,
            "tph_ach_pct": ach
        })

    # Overall test result
    if red_count > 0:
        res = "FAIL"
    elif amber_count > 0:
        res = "PARTIAL PASS"
    else:
        res = "PASS"

    # Performance Score
    n = len(tx_detail)
    if n > 0:
        wt = {"GREEN": 1.0, "AMBER": 0.5, "RED": 0.0}
        rt_pts = sum(wt.get(t["rt_status"], 0) for t in tx_detail) / n * 50
        tp_pts = sum(wt.get(t["tph_status"], 0) for t in tx_detail) / n * 30
        er_pts = max(0, (1 - sum(t["error_pct"] for t in tx_detail) / n / 100)) * 20
        ps = round(max(0, min(100, rt_pts + tp_pts + er_pts)), 1)
    else:
        ps = 0.0

    if ps >= 90:
        pg, pst = "A+", "Excellent"
    elif ps >= 80:
        pg, pst = "A", "Good"
    elif ps >= 70:
        pg, pst = "B", "Acceptable"
    elif ps >= 60:
        pg, pst = "C", "Needs Attention"
    else:
        pg, pst = "D", "Failed"

    # Stability Score
    if n > 0:
        e, sp, g = [], [], []
        for t in tx_detail:
            e.append(max(0, 100 - t["error_pct"] * 5))
            sp.append(max(0, 100 - ((t["p90"] - t["p80"]) / t["p80"] * 100 * 2)) if t["p80"] > 0 else 100)
            g.append(max(0, 100 - ((t["max"] - t["avg"]) / t["avg"] * 100)) if t["avg"] > 0 else 100)
        ss = round(max(0, min(100, (sum(e) / len(e) + sum(sp) / len(sp) + sum(g) / len(g)) / 3)), 1)
    else:
        ss = 0.0

    if ss >= 90:
        sst = "Highly Stable"
    elif ss >= 80:
        sst = "Stable"
    elif ss >= 70:
        sst = "Moderately Stable"
    else:
        sst = "Unstable"

    return tx_detail, res, ps, pg, pst, ss, sst, green_count, amber_count, red_count


# ── HTML Dashboard Builder ────────────────────────────────────────────────────
STATUS_COLORS = {
    "GREEN": "#22c55e",
    "AMBER": "#f59e0b",
    "RED": "#ef4444",
    "PASS": "#22c55e",
    "PARTIAL PASS": "#f59e0b",
    "FAIL": "#ef4444"
}

def badge(label):
    color = STATUS_COLORS.get(label, "#64748b")
    return f'<span style="background:{color};color:#fff;padding:3px 9px;border-radius:12px;font-size:11px;font-weight:700;">{label}</span>'

def result_badge(res):
    color = STATUS_COLORS.get(res, "#64748b")
    icon = {"PASS": "✅", "PARTIAL PASS": "⚠️", "FAIL": "❌"}.get(res, "")
    return f'<span style="background:{color};color:#fff;padding:8px 20px;border-radius:20px;font-size:15px;font-weight:700;">{icon} {res}</span>'


def build_dashboard_html(summary_dict):
    txs = summary_dict["all_transactions"]
    tph_exc = summary_dict["tph_not_achieved"]
    sla_exc = summary_dict["sla_90pct_deviation"]
    errors = summary_dict["error_transactions"]

    # Table 1: All Transactions
    rows_all = ""
    for t in txs:
        rows_all += f"""<tr>
            <td style="font-weight:600;white-space:nowrap;">{t['name']}</td>
            <td class="tc" style="font-weight:700;color:#60a5fa;">{t['p90']} ms</td>
            <td class="tc">{t['p80']} ms</td>
            <td class="tc">{t['avg']} ms</td>
            <td class="tc">{t['min']} ms</td>
            <td class="tc">{t['max']} ms</td>
            <td class="tc" style="font-weight:600;">{t['hitcount']}</td>
            <td class="tc">{t['errors']}</td>
            <td class="tc" style="color:{'#ef4444' if t['error_pct']>0 else '#94a3b8'};">{t['error_pct']}%</td>
            <td class="tc">{badge(t['overall_status'])}</td>
        </tr>"""

    # Table 2: TPH Not Achieved
    rows_tph = ""
    if tph_exc:
        for r in tph_exc:
            rows_tph += f"""<tr>
                <td style="font-weight:600;white-space:nowrap;">{r['name']}</td>
                <td class="tc">{r['p90']} ms</td>
                <td class="tc" style="font-weight:600;">{r['hitcount']}</td>
                <td class="tc">{r['target_tph']}</td>
                <td class="tc" style="font-weight:700;color:{'#ef4444' if r['status']=='RED' else '#f59e0b'};">{r['tph_ach_pct']}%</td>
                <td class="tc">{badge(r['status'])}</td>
            </tr>"""
    else:
        rows_tph = '<tr><td colspan="6" class="tc" style="color:#22c55e;padding:18px;">✅ All transactions met or exceeded throughput targets</td></tr>'

    # Table 3: SLA 90% Deviation
    rows_sla = ""
    if sla_exc:
        for r in sla_exc:
            rows_sla += f"""<tr>
                <td style="font-weight:600;white-space:nowrap;">{r['name']}</td>
                <td class="tc" style="font-weight:700;color:{'#ef4444' if r['status']=='RED' else '#f59e0b'};">{r['p90']} ms</td>
                <td class="tc">{r['p80']} ms</td>
                <td class="tc">{r['avg']} ms</td>
                <td class="tc">{r['target_resp']} ms</td>
                <td class="tc" style="font-weight:700;color:{'#ef4444' if r['status']=='RED' else '#f59e0b'};">+{r['deviation_pct']}%</td>
                <td class="tc">{badge(r['status'])}</td>
            </tr>"""
    else:
        rows_sla = '<tr><td colspan="7" class="tc" style="color:#22c55e;padding:18px;">✅ All transactions met response time SLA thresholds (P90 within limits)</td></tr>'

    # Table 4: Error Transactions
    rows_err = ""
    if errors:
        for r in errors:
            rows_err += f"""<tr>
                <td class="tc" style="color:#94a3b8;font-size:11px;">{r.get('timeStamp','')}</td>
                <td style="font-weight:600;white-space:nowrap;">{r.get('Transaction','')}</td>
                <td class="tc"><code style="background:#0f172a;padding:2px 6px;border-radius:4px;color:#f87171;">{r.get('ResponseCode','')}</code></td>
                <td class="tc" style="font-weight:700;color:#f87171;">{r.get('fail_count',0)}</td>
                <td class="tc" style="font-weight:600;color:#f87171;">{r.get('fail%',0)}%</td>
            </tr>"""
    else:
        rows_err = '<tr><td colspan="5" class="tc" style="color:#22c55e;padding:18px;">✅ Zero errors recorded across all test transactions</td></tr>'

    # Chart data
    chart_tx_labels = json.dumps([t["name"] for t in txs])
    chart_p90_vals = json.dumps([t["p90"] for t in txs])
    chart_tph_vals = json.dumps([t["tph_ach_pct"] for t in txs])
    chart_err_vals = json.dumps([t["error_pct"] for t in txs])

    grafana_block = ""
    if GRAFANA_URL:
        grafana_block = f"""
        <div class="card" style="text-align:center;padding:18px;">
          <a href="{GRAFANA_URL}" target="_blank" class="btn-link">📈 View Live Metrics in Grafana</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{TEST_NAME} — Performance Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0b0f19;color:#e2e8f0;line-height:1.5;}}
.hdr{{background:linear-gradient(135deg,#1e293b,#0f172a);padding:22px 36px;border-bottom:1px solid #1e293b;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;}}
.hdr h1{{font-size:22px;font-weight:700;color:#f8fafc;display:flex;align-items:center;gap:10px;}}
.hdr .meta{{font-size:12px;color:#94a3b8;margin-top:4px;}}
.wrap{{max-width:1440px;margin:0 auto;padding:24px 28px;}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:24px;}}
.kpi-c{{background:#131c2e;border:1px solid #1e293b;border-radius:10px;padding:16px;text-align:center;box-shadow:0 4px 6px -1px rgba(0,0,0,0.2);}}
.kpi-c .lbl{{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;}}
.kpi-c .val{{font-size:24px;font-weight:700;color:#f8fafc;}}
.kpi-c .sub{{font-size:11px;color:#64748b;margin-top:2px;}}
.card{{background:#131c2e;border:1px solid #1e293b;border-radius:10px;padding:20px;margin-bottom:24px;box-shadow:0 4px 6px -1px rgba(0,0,0,0.2);}}
.sec-ttl{{font-size:15px;font-weight:700;color:#f8fafc;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;padding-bottom:8px;border-bottom:1px solid #1e293b;}}
.tw{{overflow-x:auto;border-radius:8px;border:1px solid #1e293b;}}
table{{width:100%;border-collapse:collapse;font-size:12px;}}
th{{background:#0b0f19;color:#94a3b8;padding:10px 12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid #1e293b;position:sticky;top:0;}}
td{{padding:10px 12px;border-bottom:1px solid #1e293b66;color:#cbd5e1;}}
tr:hover td{{background:#1e293b44;}}
.tc{{text-align:center;}}
.sum-box{{background:#0b0f19;border:1px solid #1e293b;border-radius:8px;padding:18px;font-size:13px;line-height:1.8;color:#cbd5e1;}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px;margin-bottom:24px;}}
.ch-card{{background:#131c2e;border:1px solid #1e293b;border-radius:10px;padding:18px;}}
.ch-card h3{{font-size:13px;color:#94a3b8;margin-bottom:12px;}}
.ins{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;}}
.ins-c{{background:#0b0f19;border:1px solid #1e293b;border-radius:8px;padding:14px;}}
.ins-c .ttl{{font-size:13px;font-weight:700;margin-bottom:8px;}}
.ins-c ul{{list-style:none;}}
.ins-c ul li{{font-size:12px;color:#94a3b8;padding:4px 0;border-bottom:1px solid #1e293b33;}}
.ins-c ul li::before{{content:"→ ";color:#3b82f6;}}
.rec{{display:flex;align-items:flex-start;gap:10px;padding:12px;background:#0b0f19;border-radius:7px;margin-bottom:8px;border-left:3px solid;}}
.btn-link{{display:inline-block;background:#3b82f6;color:#fff;padding:9px 20px;border-radius:7px;text-decoration:none;font-weight:600;font-size:13px;}}
code{{background:#0b0f19;padding:2px 6px;border-radius:3px;font-size:11px;}}
</style>
</head>
<body>

<div class="hdr">
  <div>
    <h1>⚡ {TEST_NAME} <span style="background:#2563eb;color:#fff;font-size:11px;padding:2px 8px;border-radius:6px;">Automated Report</span></h1>
    <div class="meta">Generated: {summary_dict['generated_at']} &nbsp;|&nbsp; {summary_dict['total']} Target Transactions &nbsp;|&nbsp; Simple Data Writer Feed</div>
  </div>
  <div>{result_badge(summary_dict['overall_result'])}</div>
</div>

<div class="wrap">

<!-- KPI CARDS -->
<div class="kpis">
  <div class="kpi-c"><div class="lbl">Transactions</div><div class="val">{summary_dict['total']}</div><div class="sub">Total Analyzed</div></div>
  <div class="kpi-c"><div class="lbl">Passed</div><div class="val" style="color:#22c55e;">{summary_dict['passed']}</div><div class="sub">Met SLA</div></div>
  <div class="kpi-c"><div class="lbl">Partial Pass</div><div class="val" style="color:#f59e0b;">{summary_dict['partial']}</div><div class="sub">Warnings</div></div>
  <div class="kpi-c"><div class="lbl">Failed</div><div class="val" style="color:#ef4444;">{summary_dict['failed']}</div><div class="sub">SLA Breached</div></div>
  <div class="kpi-c"><div class="lbl">Avg Response Time</div><div class="val">{summary_dict['avg_rt']}</div><div class="sub">ms</div></div>
  <div class="kpi-c"><div class="lbl">Total Hits</div><div class="val">{summary_dict['total_hits']}</div><div class="sub">Samples</div></div>
  <div class="kpi-c"><div class="lbl">Avg Error %</div><div class="val" style="color:{'#ef4444' if summary_dict['avg_error_pct']>1 else '#22c55e'};">{summary_dict['avg_error_pct']}%</div><div class="sub">Error Rate</div></div>
  <div class="kpi-c"><div class="lbl">Perf Score</div><div class="val" style="color:#3b82f6;">{summary_dict['perf_score']}</div><div class="sub">Grade {summary_dict['perf_grade']} ({summary_dict['perf_status']})</div></div>
  <div class="kpi-c"><div class="lbl">Stability</div><div class="val" style="color:#a855f7;">{summary_dict['stab_score']}</div><div class="sub">{summary_dict['stab_status']}</div></div>
</div>

<!-- MANAGEMENT AI SUMMARY -->
<div class="card">
  <div class="sec-ttl"><span>📋 Management Executive Summary</span><span style="font-size:11px;color:#94a3b8;font-weight:400;">AI Synthesized</span></div>
  <div class="sum-box" id="ai-summary">Generating AI insights... please wait.</div>
</div>

<!-- 01_ALL_TRANSACTIONS TABLE -->
<div class="card">
  <div class="sec-ttl"><span>📊 01 — All Transactions</span><span style="font-size:11px;color:#94a3b8;font-weight:400;">Full Latency & Error Distribution</span></div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th>Transaction</th>
          <th class="tc">90% Line</th>
          <th class="tc">80% Line</th>
          <th class="tc">Avg</th>
          <th class="tc">Min</th>
          <th class="tc">Max</th>
          <th class="tc">Hit Count</th>
          <th class="tc">Errors</th>
          <th class="tc">Error %</th>
          <th class="tc">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows_all}
      </tbody>
    </table>
  </div>
</div>

<!-- 02_TPH_NOT_ACHIEVED TABLE -->
<div class="card">
  <div class="sec-ttl"><span>🚀 02 — Throughput (TPH) Not Achieved</span><span style="font-size:11px;color:#f59e0b;font-weight:600;">AMBER & RED Only</span></div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th>Transaction</th>
          <th class="tc">90% Line</th>
          <th class="tc">Hit Count</th>
          <th class="tc">Target TPH</th>
          <th class="tc">TPH % Achieved</th>
          <th class="tc">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows_tph}
      </tbody>
    </table>
  </div>
</div>

<!-- 03_SLA_90PCT_DEVIATION TABLE -->
<div class="card">
  <div class="sec-ttl"><span>⏱️ 03 — SLA 90th Percentile Deviation</span><span style="font-size:11px;color:#ef4444;font-weight:600;">AMBER & RED Only</span></div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th>Transaction</th>
          <th class="tc">90% Actual</th>
          <th class="tc">80% Actual</th>
          <th class="tc">Avg Actual</th>
          <th class="tc">Target SLA</th>
          <th class="tc">Deviation %</th>
          <th class="tc">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows_sla}
      </tbody>
    </table>
  </div>
</div>

<!-- 04_ERROR_TRANSACTIONS TABLE -->
<div class="card">
  <div class="sec-ttl"><span>🔴 04 — Error Transactions</span><span style="font-size:11px;color:#f87171;font-weight:400;">Grouped by Time & Code</span></div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th class="tc">Timestamp</th>
          <th>Transaction</th>
          <th class="tc">Response Code</th>
          <th class="tc">Fail Count</th>
          <th class="tc">Fail %</th>
        </tr>
      </thead>
      <tbody>
        {rows_err}
      </tbody>
    </table>
  </div>
</div>

<!-- CHARTS GRID -->
<div class="charts">
  <div class="ch-card">
    <h3>Pass / Partial / Fail Distribution</h3>
    <canvas id="cDonut" height="220"></canvas>
  </div>
  <div class="ch-card">
    <h3>P90 Response Time (ms) by Transaction</h3>
    <canvas id="cRT" height="220"></canvas>
  </div>
  <div class="ch-card">
    <h3>TPH Achievement % by Transaction</h3>
    <canvas id="cTPH" height="220"></canvas>
  </div>
  <div class="ch-card">
    <h3>Error % by Transaction</h3>
    <canvas id="cErr" height="220"></canvas>
  </div>
</div>

<!-- AI INSIGHTS -->
<div class="card">
  <div class="sec-ttl"><span>🤖 AI In-Depth Analysis</span><span style="font-size:11px;color:#94a3b8;font-weight:400;">Anthropic Claude</span></div>
  <!-- AI-INSIGHTS-START -->
  <div class="ins" id="ai-insights">
    <div class="ins-c"><div class="ttl" style="color:#3b82f6;">🔍 Key Findings</div><ul><li>Analysis pending...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#ef4444;">🚨 Critical Issues</div><ul><li>Analysis pending...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#f59e0b;">⚠️ Performance Risks</div><ul><li>Analysis pending...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#22c55e;">✅ Positive Improvements</div><ul><li>Analysis pending...</li></ul></div>
    <div class="ins-c"><div class="ttl" style="color:#a78bfa;">🔎 Areas of Concern</div><ul><li>Analysis pending...</li></ul></div>
  </div>
  <!-- AI-INSIGHTS-END -->
</div>

<!-- RECOMMENDATIONS -->
<div class="card">
  <div class="sec-ttl"><span>💡 Prioritized Recommendations</span><span style="font-size:11px;color:#94a3b8;font-weight:400;">Actionable Next Steps</span></div>
  <!-- AI-RECS-START -->
  <div id="ai-recs">
    <div class="rec" style="border-color:#ef4444;"><span>🔴</span><div><strong style="color:#ef4444;">Critical Priority</strong><br><span style="color:#94a3b8;font-size:12px;">Recommendations pending...</span></div></div>
  </div>
  <!-- AI-RECS-END -->
</div>

{grafana_block}

<div style="text-align:center;padding:24px 0;color:#64748b;font-size:11px;border-top:1px solid #1e293b;margin-top:20px;">
  Automated Performance Reporting Pipeline &nbsp;|&nbsp; {TEST_NAME} &nbsp;|&nbsp; {summary_dict['generated_at']}
</div>

</div>

<script>
const commonCfg = {{
  plugins: {{
    legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 10 }} }} }}
  }},
  scales: {{
    x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
    y: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }}
  }}
}};

new Chart(document.getElementById('cDonut'), {{
  type: 'doughnut',
  data: {{
    labels: ['Passed', 'Partial', 'Failed'],
    datasets: [{{
      data: [{summary_dict['passed']}, {summary_dict['partial']}, {summary_dict['failed']}],
      backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'],
      borderWidth: 0
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }}
  }}
}});

new Chart(document.getElementById('cRT'), {{
  type: 'bar',
  data: {{
    labels: {chart_tx_labels},
    datasets: [{{
      label: 'P90 ms',
      data: {chart_p90_vals},
      backgroundColor: '#3b82f6',
      borderRadius: 4
    }}]
  }},
  options: {{ indexAxis: 'y', ...commonCfg }}
}});

new Chart(document.getElementById('cTPH'), {{
  type: 'bar',
  data: {{
    labels: {chart_tx_labels},
    datasets: [{{
      label: 'TPH Achieved %',
      data: {chart_tph_vals},
      backgroundColor: '#a855f7',
      borderRadius: 4
    }}]
  }},
  options: {{ indexAxis: 'y', ...commonCfg }}
}});

new Chart(document.getElementById('cErr'), {{
  type: 'bar',
  data: {{
    labels: {chart_tx_labels},
    datasets: [{{
      label: 'Error %',
      data: {chart_err_vals},
      backgroundColor: '#ef4444',
      borderRadius: 4
    }}]
  }},
  options: {{ indexAxis: 'y', ...commonCfg }}
}});
</script>
</body>
</html>"""


# ── Main Entrypoint ───────────────────────────────────────────────────────────
def main():
    input_file = find_input_file()
    print(f"[generate_dashboard] Input File : {input_file}")
    print(f"[generate_dashboard] SLA File   : {SLA_FILE}")
    print(f"[generate_dashboard] Output Dir : {OUTPUT_DIR}")

    sla_map = load_sla(SLA_FILE)
    summary_df, tph, sla_dev, failures_summary = process_data(input_file, sla_map, OUTPUT_DIR)

    tx_detail, res, ps, pg, pst, ss, sst, passed, partial, failed = evaluate_performance(
        summary_df, tph, sla_dev, sla_map
    )

    avg_rt = round(summary_df["avg"].mean(), 1) if not summary_df.empty else 0.0
    tot_hits = int(summary_df["hitcount"].sum()) if not summary_df.empty else 0
    avg_err = round(summary_df["error_pct"].mean(), 2) if not summary_df.empty else 0.0

    summary_dict = {
        "test_name": TEST_NAME,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "grafana_url": GRAFANA_URL,
        "total": len(tx_detail),
        "passed": passed,
        "partial": partial,
        "failed": failed,
        "avg_rt": avg_rt,
        "total_hits": tot_hits,
        "avg_error_pct": avg_err,
        "perf_score": ps,
        "perf_grade": pg,
        "perf_status": pst,
        "stab_score": ss,
        "stab_status": sst,
        "overall_result": res,
        "all_transactions": tx_detail,
        "tph_not_achieved": [
            {"name": r[0], "p90": r[1], "hitcount": r[2], "tph_ach_pct": r[3], "target_tph": r[4], "status": r[5]}
            for r in tph
        ],
        "sla_90pct_deviation": [
            {"name": r[0], "p90": r[1], "p80": r[2], "avg": r[3], "deviation_pct": r[4], "target_resp": r[5], "status": r[6]}
            for r in sla_dev
        ],
        "error_transactions": failures_summary.to_dict(orient="records")
    }

    # Save summary.json
    summary_json_path = f"{OUTPUT_DIR}/summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_dict, f, indent=2)
    print(f"[generate_dashboard] Saved: {summary_json_path}")

    # Build & save dashboard.html
    html = build_dashboard_html(summary_dict)
    dash_path = f"{OUTPUT_DIR}/dashboard.html"
    with open(dash_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[generate_dashboard] Saved: {dash_path}")
    print(f"[generate_dashboard] Result: {res} | Score: {ps} ({pg}) | Stability: {ss}")


if __name__ == "__main__":
    main()
