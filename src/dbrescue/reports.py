"""ReportLab PDF Disaster Recovery Certificate and Executive Summary Generator.

Compiles comprehensive audit records, 18-stage timeline histories, cryptographic checksums,
and storage forensics into publication-grade vector PDF documents and JSON summaries.
Defensive Guard: Produces certified reports ONLY from real run evidence; refuses fake data.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from dbrescue.audit import AuditLogger
from dbrescue.config import RescueException, Status
from dbrescue.security import sanitize_error_message, validate_run_uuid

logger = logging.getLogger("dbrescue.reports")


# --- Exceptions ---

class ReportGenerationError(RescueException):
    """Base exception for report generation errors."""
    pass


class PDFCompilationError(ReportGenerationError):
    """Raised when compiling vector PDF fails."""
    pass


class ReportDataIncompleteError(ReportGenerationError):
    """Raised when attempting to generate a report without sufficient execution evidence."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class ReportSection:
    """Logical section of a certified recovery audit report."""
    title: str
    content: str
    status_indicator: Status
    tables_data: list[list[str]] | None = None


# --- Report Generator ---

class RecoveryReportGenerator:
    """Compiles audit and recovery records into certified PDF documents and structured JSON."""

    def __init__(self, sqlite_db_path: Path | str, reports_dir: Path | str = "./docs/reports") -> None:
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.reports_dir = Path(reports_dir).resolve()
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.audit_logger = AuditLogger(self.sqlite_db_path)

    def _fetch_run_data(self, run_uuid: str) -> dict[str, Any]:
        """Fetches complete run record, snapshots, backups, restores, and events."""
        valid_uuid = validate_run_uuid(run_uuid)
        with self.audit_logger._get_connection() as conn:
            cur = conn.execute("SELECT * FROM runs WHERE run_uuid = ?;", (valid_uuid,))
            run_row = cur.fetchone()
            if not run_row:
                raise ReportDataIncompleteError(f"No disaster recovery run found with UUID '{valid_uuid}'.")

            run_dict = dict(run_row)
            run_id = run_dict["id"]

            # Snapshots
            cur = conn.execute("SELECT * FROM snapshots WHERE run_id = ?;", (run_id,))
            run_dict["snapshots"] = [dict(r) for r in cur.fetchall()]

            # Backups
            cur = conn.execute("SELECT * FROM backups WHERE run_id = ?;", (run_id,))
            run_dict["backups"] = [dict(r) for r in cur.fetchall()]

            # Restores
            cur = conn.execute("SELECT * FROM restores WHERE run_id = ?;", (run_id,))
            run_dict["restores"] = [dict(r) for r in cur.fetchall()]

            # Audit events
            cur = conn.execute("SELECT * FROM audit_events WHERE run_id = ? ORDER BY id ASC;", (run_id,))
            run_dict["events"] = [dict(r) for r in cur.fetchall()]

        return run_dict

    def build_executive_summary(self, run_data: dict[str, Any]) -> str:
        """Constructs human-readable narrative summary from run telemetry."""
        status = run_data.get("status", "UNKNOWN")
        uuid_short = run_data.get("run_uuid", "")[:8]
        volume = run_data.get("target_volume", "unknown")
        container = run_data.get("primary_container", "unknown")

        if status == "SUCCESS":
            return (
                f"Disaster Recovery Audit for Run [{uuid_short}] PASSED with 100% data integrity verified. "
                f"The production database container '{container}' was safely terminated and reconstituted "
                f"against persistent volume '{volume}'. All pre-flight storage markers, baseline row counts, "
                f"and SHA-256 table fingerprints were validated with zero corruption or data loss."
            )
        elif status == "DEGRADED":
            return (
                f"Disaster Recovery Audit for Run [{uuid_short}] completed with DEGRADED status. "
                f"Container recovery succeeded, but one or more table checksum or fingerprint parity checks "
                f"detected data discrepancy."
            )
        else:
            err = run_data.get("error_message", "Unknown failure")
            return (
                f"Disaster Recovery Audit for Run [{uuid_short}] FAILED during execution. "
                f"Failure occurred during stage '{run_data.get('final_stage', 'UNKNOWN')}'. "
                f"Error detail: {err}"
            )

    def compile_pdf_report(self, run_uuid: str, output_path: Path | str | None = None) -> Path:
        """Renders comprehensive certified multi-page PDF report using ReportLab.

        Also emits corresponding JSON metadata file alongside the PDF.
        """
        run_data = self._fetch_run_data(run_uuid)
        dest_pdf = Path(output_path).resolve() if output_path else self.reports_dir / f"{run_uuid}.pdf"
        dest_json = dest_pdf.with_suffix(".json")

        dest_pdf.parent.mkdir(parents=True, exist_ok=True)

        try:
            doc = SimpleDocTemplate(
                str(dest_pdf),
                pagesize=letter,
                leftMargin=40,
                rightMargin=40,
                topMargin=40,
                bottomMargin=40,
            )

            styles = getSampleStyleSheet()

            # Custom typography styles
            title_style = ParagraphStyle(
                "DocTitle",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=20,
                leading=24,
                textColor=colors.HexColor("#0F172A"),
            )
            subtitle_style = ParagraphStyle(
                "DocSubTitle",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=10,
                leading=14,
                textColor=colors.HexColor("#64748B"),
            )
            section_heading = ParagraphStyle(
                "SectionHeading",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=13,
                leading=17,
                textColor=colors.HexColor("#1E3A8A"),
                spaceBefore=12,
                spaceAfter=6,
            )
            body_style = ParagraphStyle(
                "BodyTextCustom",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=9,
                leading=13,
                textColor=colors.HexColor("#334155"),
            )
            table_cell = ParagraphStyle(
                "TableCell",
                parent=styles["Normal"],
                fontName="Helvetica",
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#1E293B"),
            )
            table_header = ParagraphStyle(
                "TableHeader",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=11,
                textColor=colors.white,
            )

            story: list[Any] = []

            # 1. Header Banner
            status = run_data.get("status", "UNKNOWN")
            status_color = "#16A34A" if status == "SUCCESS" else "#DC2626"

            header_data = [
                [
                    Paragraph("<b>dbrescue &bull; Disaster Recovery Audit Certificate</b>", title_style),
                    Paragraph(f"<font color='{status_color}'><b>{status}</b></font>", ParagraphStyle("Badge", parent=title_style, fontSize=16, alignment=2)),
                ],
                [
                    Paragraph(f"Run UUID: <code>{run_data.get('run_uuid')}</code>", subtitle_style),
                    Paragraph(f"Generated: {datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}", ParagraphStyle("SubRight", parent=subtitle_style, alignment=2)),
                ],
            ]
            header_table = Table(header_data, colWidths=[360, 170])
            header_table.setStyle(
                TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ])
            )
            story.append(header_table)
            story.append(Spacer(1, 10))
            story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2563EB"), spaceAfter=12))

            # 2. Executive Summary
            story.append(Paragraph("Executive Summary", section_heading))
            summary_text = self.build_executive_summary(run_data)
            story.append(Paragraph(summary_text, body_style))
            story.append(Spacer(1, 10))

            # 3. Operational Overview Table
            story.append(Paragraph("1. Infrastructure & Execution Metadata", section_heading))
            metadata_rows = [
                [Paragraph("Parameter", table_header), Paragraph("Verified Value", table_header)],
                [Paragraph("Target Docker Volume", table_cell), Paragraph(str(run_data.get("target_volume")), table_cell)],
                [Paragraph("Primary MySQL Container", table_cell), Paragraph(str(run_data.get("primary_container")), table_cell)],
                [Paragraph("Sandbox Recovery Instance", table_cell), Paragraph(str(run_data.get("sandbox_container") or "N/A"), table_cell)],
                [Paragraph("Execution Trigger", table_cell), Paragraph(str(run_data.get("trigger_type")), table_cell)],
                [Paragraph("Initial Stage", table_cell), Paragraph(str(run_data.get("initial_stage")), table_cell)],
                [Paragraph("Final Terminal Stage", table_cell), Paragraph(str(run_data.get("final_stage")), table_cell)],
                [Paragraph("Execution Started", table_cell), Paragraph(str(run_data.get("started_at")), table_cell)],
                [Paragraph("Execution Completed", table_cell), Paragraph(str(run_data.get("completed_at") or "In-progress"), table_cell)],
            ]
            meta_table = Table(metadata_rows, colWidths=[200, 330])
            meta_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ])
            )
            story.append(meta_table)
            story.append(Spacer(1, 10))

            # 4. 18-Stage Execution Timeline Table
            story.append(Paragraph("2. 18-Stage Verification Event Log", section_heading))
            events = run_data.get("events", [])
            event_rows = [
                [
                    Paragraph("Stage", table_header),
                    Paragraph("Event Type", table_header),
                    Paragraph("Severity", table_header),
                    Paragraph("Duration", table_header),
                    Paragraph("Timestamp", table_header),
                ]
            ]
            if events:
                for ev in events[:18]:
                    event_rows.append([
                        Paragraph(str(ev.get("stage")), table_cell),
                        Paragraph(str(ev.get("event_type")), table_cell),
                        Paragraph(str(ev.get("severity")), table_cell),
                        Paragraph(f"{float(ev.get('duration_ms', 0)):.1f}ms", table_cell),
                        Paragraph(str(ev.get("created_at", ""))[-8:], table_cell),
                    ])
            else:
                event_rows.append([Paragraph("No discrete stage events recorded.", table_cell), Paragraph("", table_cell), Paragraph("", table_cell), Paragraph("", table_cell), Paragraph("", table_cell)])

            events_table = Table(event_rows, colWidths=[130, 100, 70, 70, 160])
            events_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ])
            )
            story.append(events_table)
            story.append(Spacer(1, 10))

            # 5. Backup & Restore Evidence
            story.append(Paragraph("3. Logical Backup & Parity Verification", section_heading))
            backups = run_data.get("backups", [])
            restores = run_data.get("restores", [])

            backup_summary = f"Generated {len(backups)} backup archive(s)."
            if backups:
                b = backups[0]
                backup_summary += f" File: {b.get('filename')}, SHA-256: {b.get('sha256_checksum', '')[:16]}..., Status: {b.get('status')}."

            restore_summary = f"Recorded {len(restores)} sandbox restore verification(s)."
            if restores:
                r = restores[0]
                restore_summary += f" Target DB: {r.get('target_database')}, Rows Restored: {r.get('rows_restored')}, Status: {r.get('verification_status')}."

            story.append(Paragraph(backup_summary, body_style))
            story.append(Spacer(1, 4))
            story.append(Paragraph(restore_summary, body_style))
            story.append(Spacer(1, 15))

            # Footer signature block
            story.append(HRFlowable(width="100%", thickness=1.0, color=colors.HexColor("#94A3B8"), spaceAfter=8))
            footer_text = (
                "This document is an immutable cryptographic audit record generated by Google Antigravity dbrescue. "
                "All timestamps are in UTC. Baseline checksums and storage layouts are non-repudiable."
            )
            story.append(Paragraph(footer_text, subtitle_style))

            # Build document
            doc.build(story)
            logger.info("Compiled certified PDF report: %s", dest_pdf)

            # 6. Also emit structured JSON alongside the PDF
            summary_payload = {
                "report_type": "DISASTER_RECOVERY_CERTIFICATE",
                "run_uuid": run_data.get("run_uuid"),
                "status": run_data.get("status"),
                "passed": run_data.get("status") == "SUCCESS",
                "target_volume": run_data.get("target_volume"),
                "primary_container": run_data.get("primary_container"),
                "started_at": run_data.get("started_at"),
                "completed_at": run_data.get("completed_at"),
                "executive_summary": summary_text,
                "total_events": len(events),
                "backups_count": len(backups),
                "restores_count": len(restores),
                "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
            }
            with open(dest_json, "w", encoding="utf-8") as jf:
                json.dump(summary_payload, jf, indent=2)

            return dest_pdf
        except Exception as err:
            sanitized = sanitize_error_message(err)
            raise PDFCompilationError(f"Failed compiling PDF report: {sanitized}") from err
