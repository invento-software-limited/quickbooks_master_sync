import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import frappe
from frappe.utils import get_site_path

from .utils import make_quickbooks_log

_TRUE_STRINGS = {"1", "true", "yes", "on"}
Payload = dict[str, Any] | None


def _coerce_bool(value):
	if isinstance(value, str):
		return value.strip().lower() in _TRUE_STRINGS
	if value is None:
		return None
	return bool(value)


def is_debug_enabled(default: bool = False) -> bool:
	"""
	Check if debug logging is enabled from Quickbooks Settings.

	Args:
	    default (bool): Default value if settings cannot be read (default: False)

	Returns:
	    bool: True if debug logging is enabled, False otherwise
	"""
	try:
		# Try to get from Quickbooks Settings first
		try:
			settings = frappe.get_single("Quickbooks Settings")
			if hasattr(settings, "enable_debug"):
				return bool(settings.enable_debug)
		except Exception:
			# Settings might not exist or field might not be available yet
			pass

		# Fallback to site_config for backward compatibility
		try:
			cfg = getattr(frappe, "conf", {}) or {}
			raw = cfg.get("quickbooks_debug")
			coerced = _coerce_bool(raw)
			if coerced is not None:
				return coerced
		except Exception:
			pass

		# Return default if nothing found
		return default
	except Exception:
		# If anything fails, return default
		return default


def qb_debug(
	module: str | None,
	event: str,
	payload: Payload = None,
) -> None:
	if not is_debug_enabled():
		return
	data = payload or {}
	module_name = module or "quickbooks"

	try:
		print(
			"[quickbooks DBG]",
			module_name,
			event,
			data,
		)
	except Exception:
		pass
	try:
		# Use the default logger instead of named logger for better compatibility
		frappe.logger().info(f"[QB_DEBUG] {module_name} - {event}: {data}")
	except Exception:
		pass

	# Save debug logs to JSON file for viewing in debug viewer
	try:
		# Extract company from payload if available
		company = data.get("company") if isinstance(data, dict) else None
		_save_debug_log_to_file(module_name, event, data, company=company)
	except Exception:
		# Don't fail if file save fails - logging is optional
		pass


def _get_company_abbr(company=None):
	"""
	Get company abbreviation from current company context.

	Args:
	    company (str, optional): Company name to get abbreviation for

	Returns:
	    str: Company abbreviation, or empty string if not found
	"""
	try:
		# Use passed company or try to get from user defaults
		target_company = (
			company
			or frappe.defaults.get_user_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
		)

		if target_company:
			company_abbr = frappe.db.get_value("Company", target_company, "abbr")
			if company_abbr:
				return company_abbr
	except Exception:
		pass

	return ""


def _save_debug_log_to_file(module: str, event: str, payload: dict[str, Any], company: str | None = None):
	"""
	Save debug logs to JSON file, separated by module and company abbreviation.
	Files are saved as: qb_debug_logs_{company_abbr}_{module}_full_response.json
	Timestamps are in system local time.
	Similar to other debug files like qb_si_full_response.json, qb_account_full_response.json
	"""
	try:
		site_path = get_site_path()
		private_files_path = os.path.join(site_path, "private", "files")
		os.makedirs(private_files_path, exist_ok=True)

		# Get company abbreviation for filename
		company_abbr = _get_company_abbr(company)
		company_suffix = f"_{company_abbr}" if company_abbr else ""

		# Normalize module name for filename (remove special characters)
		module_name = module or "quickbooks"
		# Replace any path separators or invalid filename characters
		safe_module_name = module_name.replace("/", "_").replace("\\", "_").replace(".", "_")

		# Create debug logs file name per module with company abbreviation
		# Format: qb_debug_logs_{company_abbr}_{module}_full_response.json
		safe_filename = os.path.basename(
			f"qb_debug_logs{company_suffix}_{safe_module_name}_full_response.json"
		)
		debug_log_file = os.path.abspath(os.path.join(private_files_path, safe_filename))

		# Verify path is within private_files_path
		if not debug_log_file.startswith(os.path.abspath(private_files_path)):
			return  # Safety check failed, skip logging

		# Load existing logs or create new list
		debug_logs = []
		if os.path.exists(debug_log_file):
			try:
				with open(debug_log_file, encoding="utf-8") as f:  # nosemgrep
					debug_logs = json.load(f)
					if not isinstance(debug_logs, list):
						debug_logs = []
			except (OSError, json.JSONDecodeError):
				debug_logs = []

		# Get current time (system local time)
		timestamp = datetime.now().isoformat()

		# Create log entry with timestamp and company abbreviation
		log_entry = {
			"timestamp": timestamp,
			"module": module,
			"event": event,
			"company_abbr": company_abbr if company_abbr else None,
			"payload": payload,
		}

		# Add to logs (save ALL entries - don't limit to prevent data loss)
		# Previously limited to 1000 entries, but this causes loss of important debug data
		debug_logs.append(log_entry)

		# Save back to file
		with open(debug_log_file, "w", encoding="utf-8") as f:  # nosemgrep
			json.dump(debug_logs, f, indent=2, ensure_ascii=False, default=str)

	except Exception:
		# Silently fail - debug logging should not break the application
		pass


def _extract_error_message(err):
	if isinstance(err, Exception):
		if getattr(err, "args", None):
			try:
				return str(err.args[0])
			except Exception:
				return str(err)
		try:
			return str(err)
		except Exception:
			return repr(err)
	if err is None:
		return ""
	try:
		return str(err)
	except Exception:
		return repr(err)


def qb_log_exception(
	method: str,
	err: Any,
	request_data: Payload = None,
	*,
	module: str | None = None,
	status: str = "Error",
	rethrow_402: bool = True,
	message: str | None = None,
	title: str | None = None,
	company: str | None = None,
) -> None:
	error_message = title or _extract_error_message(err)
	if rethrow_402 and error_message.startswith("402") and isinstance(err, Exception):
		raise err

	qb_debug(module or method, "exception", {"error": error_message})

	log_message = message
	if log_message is None:
		try:
			log_message = frappe.get_traceback()
		except Exception:
			log_message = error_message

	# Get company from parameter, request_data, or defaults
	if not company and request_data:
		company = request_data.get("company")
	if not company:
		company = frappe.defaults.get_user_default("company")

	make_quickbooks_log(
		title=error_message,
		status=status,
		method=method,
		message=log_message,
		request_data=request_data or {},
		exception=True,
		company=company,
	)


def qb_log_error(
	title: str,
	method: str,
	*,
	message: str | None = None,
	request_data: Payload = None,
	status: str = "Error",
	module: str | None = None,
	company: str | None = None,
) -> None:
	qb_debug(module or method, "error", {"title": title})
	log_message = message
	if log_message is None:
		try:
			log_message = frappe.get_traceback()
		except Exception:
			log_message = title

	# Get company from parameter, request_data, or defaults
	if not company and request_data:
		company = request_data.get("company")
	if not company:
		company = frappe.defaults.get_user_default("company")

	make_quickbooks_log(
		title=title,
		status=status,
		method=method,
		message=log_message,
		request_data=request_data or {},
		exception=True,
		company=company,
	)


def qb_log_status(
	title: str,
	*,
	method: str,
	status: str = "Queued",
	message: str | None = None,
	request_data: Payload = None,
	module: str | None = None,
	exception: bool = False,
	company: str | None = None,
) -> None:
	qb_debug(module or method, "status", {"title": title, "status": status})

	# Get company from parameter, request_data, or defaults
	if not company and request_data:
		company = request_data.get("company")
	if not company:
		company = frappe.defaults.get_user_default("company")

	# make_quickbooks_log now handles creating new entries for non-Queued statuses
	make_quickbooks_log(
		title=title,
		status=status,
		method=method,
		message=message,
		request_data=request_data or {},
		exception=exception,
		company=company,
	)
