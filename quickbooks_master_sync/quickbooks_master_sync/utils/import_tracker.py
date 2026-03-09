import os
import time

import frappe
from frappe.utils import get_site_path, now_datetime


class ImportTracker:
    def __init__(self, total_count, log_filename="qb_customer_import.log", module_name="CUSTOMER", company=None, overwrite=True):
        self.total_count = total_count
        self.current_index = 0
        self.created = 0
        self.updated = 0
        self.skipped = 0
        self.errors = 0
        
        # Determine company abbreviation if company is provided
        self.company = company
        self.company_abbr = ""
        if self.company:
            self.company_abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
            
        # Run ID format: QB-{ABBR}-{MODULE}-YYYYMMDD-HHMMSS (e.g., QB-CS-CUSTOMER-20260131-095230)
        now = now_datetime()
        abbr_part = f"{self.company_abbr}-" if self.company_abbr else ""
        self.run_id = f"QB-{abbr_part}{module_name.upper()}-{now.strftime('%Y%m%d-%H%M%S')}"
        
        self.start_time = time.time()
        self.item_test_start_time = {} # Track start time for individual items by ID
        
        # Prepend abbreviation to log filename if available
        if self.company_abbr:
            if log_filename.startswith("qb_"):
                # Insert company abbr after "qb_" prefix (e.g., "qb_ACEA_account_import.log")
                log_filename = f"qb_{self.company_abbr}_{log_filename[3:]}"
            else:
                # Prepend company abbr (e.g., "ACEA_other_log.log")
                log_filename = f"{self.company_abbr}_{log_filename}"
            
        self.log_file_path = self._get_log_file_path(log_filename)
        
        # Delete existing log file if it exists to start fresh (only if overwrite is True)
        if overwrite and os.path.exists(self.log_file_path):
            try:
                os.remove(self.log_file_path)
            except Exception:
                pass  # Ignore errors if file can't be deleted
        
        # Log start of run
        self._write_log("INFO", {
            "run_id": self.run_id,
            "event": "IMPORT_START",
            "total": self.total_count,
            "source": "QB",
            "company": self.company or "Unknown"
        })

    def _get_log_file_path(self, filename):
        site_path = get_site_path()
        return os.path.join(site_path, "private", "files", filename)

    def _write_log(self, level, fields):
        """
        Write a log entry in logfmt style (key=value).
        [YYYY-MM-DD HH:MM:SS] LEVEL key=value key=value ...
        """
        timestamp = now_datetime().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build key=value string
        log_parts = []
        for key, value in fields.items():
            # Handle quoting if value contains spaces
            val_str = str(value)
            if " " in val_str or "=" in val_str:
                val_str = f'"{val_str}"'
            log_parts.append(f"{key}={val_str}")
            
        params_str = " ".join(log_parts)
        
        # Align level for readability (INFO, OK   , WARN , ERROR)
        level_map = {
            "INFO": "INFO ",
            "OK": "OK   ", 
            "WARN": "WARN ",
            "ERROR": "ERROR"
        }
        aligned_level = level_map.get(level, level.ljust(5))
        
        log_line = f"[{timestamp}] {aligned_level} {params_str}\n"
        
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def _get_progress_str(self):
        if self.total_count > 0:
            percentage = int((self.current_index / self.total_count) * 100)
            return f"{percentage}%"
        return "0%"

    def log_processing(self, source, identifier):
        """Log that processing has started for an item."""
        self.item_test_start_time[identifier] = time.time()
        
        self._write_log("INFO", {
            "run_id": self.run_id,
            "event": "RECORD_START",
            "id": identifier,
            "source": source,
            "index": self.current_index + 1
        })

    def _get_duration_ms(self, identifier):
        if hasattr(self, "last_phase_time"):
            self.last_phase_time.pop(identifier, None)
            
        start = self.item_test_start_time.pop(identifier, None)
        if start:
            return int((time.time() - start) * 1000)
        return 0

    def log_phase(self, identifier, phase_name):
        """Log the duration of a specific phase since the last phase (or since start)."""
        if not hasattr(self, "last_phase_time"):
            self.last_phase_time = {}
            
        start = self.item_test_start_time.get(identifier)
        if not start:
            return
            
        last_phase = self.last_phase_time.get(identifier, start)
        
        now = time.time()
        duration = int((now - last_phase) * 1000)
        self._write_log("INFO", {
            "run_id": self.run_id,
            "event": "TIMING",
            "id": identifier,
            "action": phase_name,
            "duration_ms": duration
        })
        
        self.last_phase_time[identifier] = now

    def log_success(self, source, identifier, action=None):
        self.current_index += 1
        
        # Ensure action is a string even if None is passed
        action_val = str(action or "Processed")
        action_lower = action_val.lower()
        if action_lower == "created":
            self.created += 1
        elif action_lower == "updated":
            self.updated += 1
            
        duration = self._get_duration_ms(identifier)
        
        self._write_log("OK", {
            "run_id": self.run_id,
            "event": "RECORD_SUCCESS",
            "id": identifier,
            "source": source,
            "action": action.upper(),
            "duration_ms": duration,
            "progress": self._get_progress_str()
        })

    def log_skip(self, source, identifier, reason):
        self.current_index += 1
        self.skipped += 1
        
        self._write_log("WARN", {
            "run_id": self.run_id,
            "event": "RECORD_SKIPPED",
            "id": identifier,
            "source": source,
            "reason": reason.upper(),
            "progress": self._get_progress_str()
        })

    def log_error(self, source, identifier, error_message):
        self.current_index += 1
        self.errors += 1
        
        self._write_log("ERROR", {
            "run_id": self.run_id,
            "event": "RECORD_FAILED",
            "id": identifier,
            "source": source,
            "error": error_message,
            "progress": self._get_progress_str()
        })

    def log_summary(self):
        """Log the final summary of the import run."""
        elapsed_total = time.time() - self.start_time
        
        self._write_log("INFO", {
            "run_id": self.run_id,
            "event": "IMPORT_END",
            "processed": self.current_index,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.errors,
            "duration_sec": round(elapsed_total, 1)
        })

    def cleanup(self):
        self.log_summary()
