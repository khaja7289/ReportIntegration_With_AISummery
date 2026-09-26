"""
send_report.py
Reads summary.json + dashboard.html -> sends email with report attachments.
All mail config read from environment variables (set in .gitlab-ci.yml / CI variables).
Fails gracefully — never breaks the pipeline on mail errors.
"""

import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email               import encoders
from datetime            import datetime
from pathlib             import Path

OUTPUT_DIR     = os.environ.get("OUTPUT_DIR",     "artifacts/reports")
SUMMARY_PATH   = f"{OUTPUT_DIR}/summary.json"
DASHBOARD_PATH = f"{OUTPUT_DIR}/dashboard.html"

# Mail config — from .gitlab-ci.yml variables
MAIL_TO             = os.environ.get("MAIL_TO",             "")
MAIL_CC             = os.environ.get("MAIL_CC",             "")
MAIL_SUBJECT_PREFIX = os.environ.get("MAIL_SUBJECT_PREFIX", "Perf Report")
SEND_MAIL_ON        = os.environ.get("SEND_MAIL_ON",        "always")   # always | fail_only | pass_only
TEST_NAME           = os.environ.get("TEST_NAME",           "Performance Test")

# SMTP config — from GitLab protected variables
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")
SMTP_SSL  = os.environ.get("SMTP_SSL", "false").lower() == "true"


# ── Should We Send? ───────────────────────────────────────────────────────────
def should_send(overall_result):
    if SEND_MAIL_ON == "always":
        return True
    elif SEND_MAIL_ON == "fail_only" and overall_result == "FAIL":
        return True
    elif SEND_MAIL_ON == "pass_only" and overall_result == "PASS":
        return True
    return False


# ── Build Subject ─────────────────────────────────────────────────────────────
def build_subject(summary):
    result = summary.get("overall_result", "UNKNOWN")
    date   = datetime.now().strftime("%Y-%m-%d")
    icon   = {"PASS": "✅", "PARTIAL PASS": "⚠️", "FAIL": "❌"}.get(result, "")
    return f"[{result}] {icon} {MAIL_SUBJECT_PREFIX} — {TEST_NAME} | {date}"


# ── Build Body ────────────────────────────────────────────────────────────────
def build_body(summary):
    result    = summary.get("overall_result", "N/A")
    score     = summary.get("perf_score", 0)
    grade     = summary.get("perf_grade", "N/A")
    stab      = summary.get("stab_score", 0)
    stab_st   = summary.get("stab_status", "N/A")
    total     = summary.get("total", 0)
    passed    = summary.get("passed", 0)
    partial   = summary.get("partial", 0)
    failed    = summary.get("failed", 0)
    avg_rt    = summary.get("avg_rt", 0)
    tot_hits  = summary.get("total_hits", 0)
    avg_err   = summary.get("avg_error_pct", 0)
    generated = summary.get("generated_at", "")
    grafana   = summary.get("grafana_url", "")

    # SLA Deviations
    sla_dev = summary.get("sla_90pct_deviation", [])
    if sla_dev:
        sla_lines = "".join(
            f"\n    • {d['name']} | P90: {d['p90']}ms (Target: {d['target_resp']}ms) | Dev: +{d['deviation_pct']}% [{d['status']}]"
            for d in sla_dev[:5]
        )
    else:
        sla_lines = "\n    None — all transactions met 90% SLA target"

    # TPH Underachievers
    tph_exc = summary.get("tph_not_achieved", [])
    if tph_exc:
        tph_lines = "".join(
            f"\n    • {t['name']} | Hits: {t['hitcount']} (Target: {t['target_tph']}) | Achieved: {t['tph_ach_pct']}% [{t['status']}]"
            for t in tph_exc[:5]
        )
    else:
        tph_lines = "\n    None — all transactions met throughput target"

    # Errors
    err_list = summary.get("error_transactions", [])
    if err_list:
        err_lines = "".join(
            f"\n    • {e.get('Transaction')} [Code {e.get('ResponseCode')}]: {e.get('fail_count')} errors ({e.get('fail%')}%)"
            for e in err_list[:5]
        )
    else:
        err_lines = "\n    Zero errors recorded"

    grafana_line = f"\n  Grafana Dashboard : {grafana}" if grafana else ""

    ai_summary_section = ""
    ai_report_path = f"{OUTPUT_DIR}/ai_report.json"
    if Path(ai_report_path).exists():
        try:
            with open(ai_report_path, encoding="utf-8") as f:
                ai_data = json.load(f)
                mgmt = ai_data.get("management_summary", "").strip()
                if mgmt:
                    ai_summary_section = f"\nEXECUTIVE AI SUMMARY:\n  {mgmt}\n"
        except Exception:
            pass

    body = f"""
Performance Test Report — {TEST_NAME}
Generated: {generated}
{'='*65}

OVERALL RESULT   : {result}
Performance Score: {score}/100  (Grade: {grade})
Stability Score  : {stab}/100   ({stab_st})
{ai_summary_section}
TRANSACTION METRICS SUMMARY:
  Total Analyzed   : {total}
  ✅ Passed        : {passed}
  ⚠️  Partial Pass  : {partial}
  ❌ Failed        : {failed}
  Avg Response Time: {avg_rt} ms
  Total Hit Count  : {tot_hits}
  Avg Error Rate   : {avg_err}%

SLA 90th PERCENTILE DEVIATIONS (AMBER/RED):{sla_lines}

THROUGHPUT UNDERACHIEVERS (AMBER/RED):{tph_lines}

RECORDED ERROR GROUPS:{err_lines}
{grafana_line}
{'='*65}
Attachments included with this notification:
  1. Performance Dashboard (dashboard.html) — interactive charts & AI analysis
  2. 01_all_transactions.csv — comprehensive transaction metrics
  3. 02_tph_not_achieved.csv — throughput amber/red exceptions
  4. 03_sla_90pct_deviation.csv — latency SLA deviation exceptions
  5. 04_error_transactions.csv — granular failure distribution
  6. ai_report.json — structured AI analysis and recommendations
{'='*65}
Auto-generated by Performance Framework Pipeline.
"""
    return body.strip()


# ── Send Email ────────────────────────────────────────────────────────────────
def attach_file(msg, file_path, display_name):
    if Path(file_path).exists():
        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{display_name}"')
        msg.attach(part)
        print(f"[send_report] Attached: {display_name}")


def send_email(summary):
    if not MAIL_TO:
        print("[send_report] MAIL_TO not configured — skipping email delivery.")
        return

    if not SMTP_HOST:
        print("[send_report] SMTP_HOST not configured — skipping email delivery.")
        return

    to_list  = [addr.strip() for addr in MAIL_TO.split(",") if addr.strip()]
    cc_list  = [addr.strip() for addr in MAIL_CC.split(",") if addr.strip()] if MAIL_CC else []
    all_rcpt = to_list + cc_list

    msg = MIMEMultipart()
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(to_list)
    msg["CC"]      = ", ".join(cc_list)
    msg["Subject"] = build_subject(summary)

    msg.attach(MIMEText(build_body(summary), "plain"))

    # Attach Dashboard HTML
    date_stamp = datetime.now().strftime("%Y%m%d_%H%M")
    attach_file(msg, DASHBOARD_PATH, f"performance_dashboard_{date_stamp}.html")

    # Attach 4 CSV tables if generated
    attach_file(msg, f"{OUTPUT_DIR}/01_all_transactions.csv", "01_all_transactions.csv")
    attach_file(msg, f"{OUTPUT_DIR}/02_tph_not_achieved.csv", "02_tph_not_achieved.csv")
    attach_file(msg, f"{OUTPUT_DIR}/03_sla_90pct_deviation.csv", "03_sla_90pct_deviation.csv")
    attach_file(msg, f"{OUTPUT_DIR}/04_error_transactions.csv", "04_error_transactions.csv")
    attach_file(msg, f"{OUTPUT_DIR}/ai_report.json", "ai_report.json")

    try:
        if SMTP_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()

        if SMTP_USER and SMTP_PASS:
            server.login(SMTP_USER, SMTP_PASS)

        server.sendmail(SMTP_USER, all_rcpt, msg.as_string())
        server.quit()
        print(f"[send_report] Email successfully sent to: {', '.join(all_rcpt)}")

    except Exception as e:
        # Graceful failure — never fail pipeline because of email delivery
        print(f"[send_report] Email delivery failed (pipeline continues): {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not Path(SUMMARY_PATH).exists():
        print(f"[send_report] Error: {SUMMARY_PATH} not found.")
        return

    print(f"[send_report] Reading summary: {SUMMARY_PATH}")
    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)

    overall_result = summary.get("overall_result", "UNKNOWN")
    print(f"[send_report] Overall Result : {overall_result}")
    print(f"[send_report] SEND_MAIL_ON   : {SEND_MAIL_ON}")

    if should_send(overall_result):
        send_email(summary)
    else:
        print(f"[send_report] Mail skipped — condition '{SEND_MAIL_ON}' not satisfied for result '{overall_result}'")


if __name__ == "__main__":
    main()
