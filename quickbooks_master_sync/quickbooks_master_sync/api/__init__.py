# Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import time

import frappe
from frappe import _
from frappe.utils import cint, flt, get_url
from frappe.utils.background_jobs import enqueue

from quickbooks_master_sync.pyqb.quickbooks import QuickBooks
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_account import sync_account
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_company import sync_company
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_customers import sync_customers
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_employee import create_Employee
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_opening_balances import sync_opening_balances
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_payment_method import sync_payment_method
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_products import create_Item
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_suppliers import sync_suppliers
from quickbooks_master_sync.quickbooks_master_sync.sync.sync_utils import SyncCache, _get_quickbooks_company
from quickbooks_master_sync.quickbooks_master_sync.utils.exceptions import QuickbooksError
from quickbooks_master_sync.quickbooks_master_sync.utils.logging import (
	qb_log_error,
	qb_log_exception,
	qb_log_status,
)


@frappe.whitelist()
def sync_quickbooks():
	"""Enqueue longjob for Syncing quickbooks Online"""

	enqueue(
		"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_resources",
		queue="long",
		timeout=1500,
		event="hourly_long",
	)


@frappe.whitelist()
def sync_quickbooks_resources():
	quickbooks_settings = frappe.get_doc("Quickbooks Settings")

	qb_log_status(
		title="Sync Job Queued",
		status="Queued",
		method=frappe.local.form_dict.cmd,
		message="Sync Job Queued (Master Data Only)",
		module="api.sync_quickbooks_resources",
	)

	if quickbooks_settings.enable_quickbooks_online:
		try:
			if quickbooks_settings.quickbooks_to_erpnext:
				enqueue(
					method="quickbooks_master_sync.quickbooks_master_sync.api._run_sync_with_progress",
					queue="long",
					timeout=86400,
					job_name=f"quickbooks_sync_{frappe.session.user}",
					quickbooks_settings=quickbooks_settings,
				)

				return {
					"success": True,
					"message": "Sync started in background. Progress will be shown in real-time.",
					"job_queued": True,
				}
			else:
				return {
					"success": False,
					"message": "QuickBooks to ERPNext sync is disabled.",
				}
		except Exception as e:
			error_message = str(e)
			qb_log_error(
				title="Sync Job Queue Error",
				status="Error",
				method="sync_quickbooks_resources",
				message=_("""Error queuing sync job: {0}""").format(error_message),
				module="api.sync_quickbooks_resources",
			)
			return {
				"success": False,
				"message": f"Failed to start sync: {error_message}",
			}
	else:
		return {"success": False, "message": "QuickBooks connector is not enabled."}


def _keep_db_connection_alive():
	"""Keep database connection alive during long-running sync"""
	try:
		frappe.db.sql("SELECT 1")
		frappe.db.commit()
	except Exception:
		try:
			frappe.connect()
		except Exception:
			pass


def _publish_realtime_progress(step, status="running", message=""):
	"""Publish sync progress to frontend via Socket.io"""
	frappe.publish_realtime(
		"quickbooks_sync_progress",
		{"step": step, "status": status, "message": message},
		user=frappe.session.user,
	)


def _run_sync_with_progress(quickbooks_settings):
	"""Run sync with progress updates - called from background job"""

	start_time = time.time()

	try:
		_keep_db_connection_alive()

		if quickbooks_settings.quickbooks_to_erpnext:
			validate_quickbooks_settings(quickbooks_settings)
			sync_from_quickbooks_to_erp(quickbooks_settings)

			elapsed_time = int((time.time() - start_time) / 60)
			_publish_realtime_progress(
				"Sync Completed",
				"completed",
				f"Master Data Sync completed successfully in {elapsed_time} minutes",
			)
			qb_log_status(
				title="Sync Completed",
				status="Success",
				method="sync_quickbooks_resources",
				message=f"Master Data Sync completed successfully in {elapsed_time} minutes",
				module="api.sync_quickbooks_resources",
			)
	except Exception as e:
		error_message = str(e)
		_publish_realtime_progress("Sync Failed", "error", error_message)
		qb_log_error(
			title="QuickBooks Sync Error",
			status="Error",
			method="sync_quickbooks_resources",
			message=_("""Error: {0}\n\nTraceback:\n{1}""").format(error_message, frappe.get_traceback()),
			module="api.sync_quickbooks_resources",
		)


def sync_from_quickbooks_to_erp(quickbooks_settings):
	"""MASTER DATA ONLY SYNC"""
	quickbooks_obj = _create_quickbooks_client(quickbooks_settings)
	frappe.flags.mute_messages = True

	try:
		_publish_realtime_progress(
			"Analyzing QuickBooks...", "running", "Fetching company info and initializing cache"
		)
		qb_company = _get_quickbooks_company(quickbooks_obj=quickbooks_obj)
		cache = SyncCache(qb_company)
		cache.quickbooks_settings = quickbooks_settings

		# 1. Company
		try:
			_publish_realtime_progress(
				"Syncing Company...", "running", "Synchronizing QuickBooks company details and foundations"
			)
			sync_company(quickbooks_obj)
		except Exception as e:
			qb_log_exception(method="sync_company", err=e, module="api")

		# 2. Master Data
		try:
			_publish_realtime_progress(
				"Syncing Customers...", "running", "Synchronizing QuickBooks customers"
			)
			sync_customers(quickbooks_obj)
		except Exception as e:
			qb_log_exception(method="sync_customers", err=e, module="api")

		try:
			_publish_realtime_progress(
				"Syncing Suppliers...", "running", "Synchronizing QuickBooks suppliers"
			)
			sync_suppliers(quickbooks_obj)
		except Exception as e:
			qb_log_exception(method="sync_suppliers", err=e, module="api")

		try:
			_publish_realtime_progress(
				"Syncing Employees...", "running", "Synchronizing QuickBooks employees"
			)
			create_Employee(quickbooks_obj)
		except Exception as e:
			qb_log_exception(method="create_Employee", err=e, module="api")

		try:
			_publish_realtime_progress(
				"Syncing Products...", "running", "Synchronizing QuickBooks items and categories"
			)
			create_Item(quickbooks_obj)
		except Exception as e:
			qb_log_exception(method="create_Item", err=e, module="api")

		try:
			_publish_realtime_progress(
				"Syncing Opening Balances...", "running", "Synchronizing QuickBooks opening balances"
			)
			sync_opening_balances(quickbooks_obj, auto_submit=True)
		except Exception as e:
			qb_log_exception(method="sync_opening_balances", err=e, module="api")

	finally:
		frappe.flags.mute_messages = False

	frappe.db.set_single_value("Quickbooks Settings", "last_sync_datetime", frappe.utils.now())


def validate_quickbooks_settings(quickbooks_settings):
	if not quickbooks_settings.get("access_token") or not quickbooks_settings.get("realm_id"):
		raise QuickbooksError(_("QuickBooks is not connected. Please connect to QuickBooks first."))

	try:
		quickbooks_settings.save()
	except Exception as e:
		raise QuickbooksError(str(e))


def _create_quickbooks_client(quickbooks_settings):
	try:
		if isinstance(quickbooks_settings, str):
			quickbooks_settings = frappe.get_doc("Quickbooks Settings", quickbooks_settings)
		elif hasattr(quickbooks_settings, "reload"):
			quickbooks_settings.reload()
	except Exception:
		pass

	sandbox_mode = cint(quickbooks_settings.get("sandbox", 1))

	existing_instance = QuickBooks.get_instance()
	if existing_instance:
		if (
			existing_instance.company_id != quickbooks_settings.realm_id
			or existing_instance.access_token != quickbooks_settings.access_token
		):
			existing_instance._drop()

	qb = QuickBooks(
		sandbox=sandbox_mode,
		consumer_key=quickbooks_settings.consumer_key,
		consumer_secret=quickbooks_settings.consumer_secret,
		access_token=quickbooks_settings.access_token,
		access_token_secret=quickbooks_settings.access_token_secret,
		company_id=quickbooks_settings.realm_id,
	)
	qb.minorversion = 75
	return qb


@frappe.whitelist()
def sync_quickbooks_customers_only():
	return _sync_resource_with_progress("Customer", sync_customers)


@frappe.whitelist()
def sync_quickbooks_accounts_only():
	return _sync_resource_with_progress("Account", sync_account)


@frappe.whitelist()
def sync_quickbooks_suppliers_only():
	return _sync_resource_with_progress("Supplier", sync_suppliers)


@frappe.whitelist()
def sync_quickbooks_employees_only():
	return _sync_resource_with_progress("Employee", create_Employee)


@frappe.whitelist()
def sync_quickbooks_products_only():
	return _sync_resource_with_progress("Product", create_Item)


@frappe.whitelist()
def sync_quickbooks_payment_methods_only():
	return _sync_resource_with_progress("Payment Method", sync_payment_method)


def _sync_resource_with_progress(resource_name, sync_function):
	time.time()
	quickbooks_settings = frappe.get_doc("Quickbooks Settings")

	if not quickbooks_settings.enable_quickbooks_online:
		return {"success": False, "message": "QuickBooks connector is not enabled."}

	try:
		_publish_realtime_progress(
			f"Syncing {resource_name}...", "running", f"Starting individual sync for {resource_name}"
		)
		_keep_db_connection_alive()
		validate_quickbooks_settings(quickbooks_settings)

		quickbooks_obj = _create_quickbooks_client(quickbooks_settings)

		frappe.flags.mute_messages = True
		try:
			sync_function(quickbooks_obj)
		finally:
			frappe.flags.mute_messages = False

		frappe.db.set_single_value("Quickbooks Settings", "last_sync_datetime", frappe.utils.now())

		_publish_realtime_progress(
			"Sync Completed", "completed", f"{resource_name} sync completed successfully"
		)
		return {"success": True, "message": f"{resource_name} sync completed successfully"}
	except Exception as e:
		error_msg = str(e)
		_publish_realtime_progress("Sync Failed", "error", error_msg)
		qb_log_error(
			title=f"QuickBooks {resource_name} Sync Error",
			status="Error",
			method=f"sync_quickbooks_{resource_name.lower().replace(' ', '_')}_only",
			message=_("""Error: {0}\n\nTraceback:\n{1}""").format(error_msg, frappe.get_traceback()),
			module="api",
		)
		return {"success": False, "message": error_msg}


@frappe.whitelist()
def get_quickbooks_debug_files(
	filename_filter=None,
	data_type=None,
	company_filter=None,
	sort_by="modified",
	sort_order="desc",
	list_full_response_only=False,
):
	"""
	Get list of all QuickBooks debug JSON files with filtering and sorting options.
	Get list of QuickBooks debug files with filtering and sorting.
	"""
	import os
	from datetime import datetime

	from frappe.utils import get_site_path

	try:
		site_path = get_site_path()
		files_dir = os.path.join(site_path, "private", "files")

		if not os.path.exists(files_dir):
			return {"success": True, "files": [], "count": 0}

		debug_files = []
		available_data_types = set()
		available_companies = set()

		# Convert boolean string to bool if necessary
		if isinstance(list_full_response_only, str):
			list_full_response_only = (
				list_full_response_only.lower() == "true" or list_full_response_only == "1"
			)

		try:
			# We only look for files starting with qb_ and ending with .json or .log
			all_files = [
				f
				for f in os.listdir(files_dir)
				if f.startswith("qb_") and (f.endswith(".json") or f.endswith(".log"))
			]
		except Exception:
			# Handle permission errors or other FS issues
			frappe.log_error("Error listing files", "get_quickbooks_debug_files")
			return {"success": True, "files": [], "count": 0}

		for filename in all_files:
			try:
				file_path = os.path.join(files_dir, filename)
				file_stat = os.stat(file_path)

				# Check for reconcile list only filter
				if list_full_response_only:
					if "list_full_response" not in filename:
						continue

				# Parse filename to extract metadata and company
				# Format: qb_{company}_{data_name}_full_response.json or qb_{data_name}_full_response.json
				# Format for debug logs: qb_debug_logs_{company}_{module}_full_response.json or .log
				parts = filename.replace(".json", "").replace(".log", "").split("_")

				# Extract company abbreviation from filename
				# Pattern: qb_{company}_{data_name}... or qb_{data_name}...
				# For debug logs: qb_debug_logs_{company}_{module}...
				company_abbr = None

				# Check if it's a debug log file first
				is_debug_log = len(parts) >= 3 and parts[1] == "debug" and parts[2] == "logs"

				if is_debug_log:
					# For debug logs: qb_debug_logs_{company}_{module}_full_response.json
					# Company is at parts[3]
					if len(parts) >= 4:
						potential_company = parts[3]
						# Company abbreviations are typically uppercase and short
						if potential_company.isupper() and len(potential_company) <= 5:
							company_abbr = potential_company
							available_companies.add(company_abbr)
				elif len(parts) >= 2:
					# Regular files: qb_{company}_{data_name}... or qb_{data_name}...
					# Check if second part is a company abbreviation (usually 2-5 uppercase letters)
					potential_company = parts[1]
					# Company abbreviations are typically uppercase and short
					if potential_company.isupper() and len(potential_company) <= 5:
						# Check if it's not a common data name
						common_data_names = [
							"debug",
							"log",
							"account",
							"customer",
							"supplier",
							"item",
							"product",
						]
						if potential_company not in common_data_names:
							company_abbr = potential_company
							available_companies.add(company_abbr)

				# Extract data name (everything between "qb_" and "_full" or "_timestamp")
				data_name = "unknown"

				if is_debug_log:
					# For debug logs: qb_debug_logs_{company}_{module}_full_response.json
					# data_name should be: debug_logs_{module} (excluding company)
					if "full" in parts:
						full_idx = parts.index("full")
						# Skip "qb", "debug", "logs", and company (parts[0:4]), keep rest until "full"
						if len(parts) >= 5 and full_idx > 4:
							module_parts = parts[4:full_idx]  # Everything after company until "full"
							data_name = f"debug_logs_{'_'.join(module_parts)}"
						else:
							data_name = "debug_logs"
					else:
						# For files without "full_response" in name
						if len(parts) >= 5:
							module_parts = parts[4:-2]  # Everything after company until last 2 parts
							data_name = f"debug_logs_{'_'.join(module_parts)}"
						else:
							data_name = "debug_logs"
				else:
					# Regular files: qb_{company}_{data_name}_full_response.json or qb_{data_name}_full_response.json
					start_idx = 2 if company_abbr else 1  # Skip "qb" and company if present

					if "full" in parts:
						full_idx = parts.index("full")
						data_name = "_".join(parts[start_idx:full_idx])
					else:
						# For files without "full_response" in name
						if len(parts) >= start_idx + 2:
							data_name = "_".join(parts[start_idx:-2])

				available_data_types.add(data_name)

				# Apply filename filter (case-insensitive)
				if filename_filter and filename_filter.strip():
					if filename_filter.lower() not in filename.lower():
						continue

				# Apply data type filter
				if data_type and data_type.strip():
					if data_name != data_type:
						continue

				# Apply company filter
				if company_filter and company_filter.strip():
					if company_abbr != company_filter:
						continue

				# Convert timestamp to datetime string
				try:
					modified_datetime = datetime.fromtimestamp(file_stat.st_mtime)
					modified_str = modified_datetime.strftime("%Y-%m-%d %H:%M:%S")
				except Exception:
					modified_str = "Unknown"

				debug_files.append(
					{
						"filename": filename,
						"data_name": data_name,
						"company": company_abbr or "",
						"size": file_stat.st_size,
						"size_mb": round(file_stat.st_size / (1024 * 1024), 2),
						"modified": modified_str,
						"timestamp": file_stat.st_mtime,
					}
				)

			except Exception:
				continue

		# Sort files based on parameters
		reverse_order = sort_order.lower() == "desc"

		if sort_by == "size":
			debug_files.sort(key=lambda x: x["size"], reverse=reverse_order)
		elif sort_by == "filename":
			debug_files.sort(key=lambda x: x["filename"].lower(), reverse=reverse_order)
		elif sort_by == "data_name":
			debug_files.sort(key=lambda x: x["data_name"].lower(), reverse=reverse_order)
		else:  # Default to modified (timestamp)
			debug_files.sort(key=lambda x: x["timestamp"], reverse=reverse_order)

		return {
			"success": True,
			"files": debug_files,
			"count": len(debug_files),
			"total_size_mb": round(sum(f["size"] for f in debug_files) / (1024 * 1024), 2),
			"available_data_types": sorted(list(available_data_types)),
			"available_companies": sorted(list(available_companies)),
			"filters_applied": {
				"filename_filter": filename_filter,
				"data_type": data_type,
				"company_filter": company_filter,
				"sort_by": sort_by,
				"sort_order": sort_order,
			},
		}

	except Exception as e:
		frappe.log_error(f"Error getting debug files: {e!s}", "get_quickbooks_debug_files")
		return {
			"success": False,
			"error": str(e),
			"files": [],
			"available_data_types": [],
			"available_companies": [],
		}


@frappe.whitelist()
def get_quickbooks_debug_file_content(filename):
	"""
	Get content of a specific QuickBooks debug JSON file.

	Args:
	    filename (str): Name of the file to retrieve

	Returns:
	    dict: File content and metadata
	"""
	import json
	import os

	from frappe.utils import get_site_path

	try:
		# Security: Only allow files that start with "qb_" and end with ".json" or ".log"
		if not filename.startswith("qb_") or (
			not filename.endswith(".json") and not filename.endswith(".log")
		):
			frappe.throw(_("Invalid filename. Only QuickBooks debug files (.json/.log) are allowed."))

		# Security: Prevent directory traversal
		if ".." in filename or "/" in filename or "\\" in filename:
			frappe.throw(_("Invalid filename. Path traversal not allowed."))

		site_path = get_site_path()
		file_path = os.path.join(site_path, "private", "files", filename)

		if not os.path.exists(file_path):
			frappe.throw(_("File not found: {0}").format(filename))

		# Read file content
		content = None
		if filename.endswith(".json"):
			with open(file_path, encoding="utf-8") as f:
				content = json.load(f)
		elif filename.endswith(".log"):
			# Parse logfmt file into list of dicts
			content = []
			with open(file_path, encoding="utf-8") as f:
				for line in f:
					line = line.strip()
					if not line:
						continue

					# Parse simple logfmt: [TIMESTAMP] LEVEL key=value ...
					try:
						entry = {}
						# Extract timestamp and level
						if line.startswith("[") and "]" in line:
							ts_end = line.find("]")
							entry["timestamp"] = line[1:ts_end]
							remainder = line[ts_end + 1 :].strip()

							# Extract Level if present (INFO, OK, etc)
							if " " in remainder:
								level, params_str = remainder.split(" ", 1)
								entry["level"] = level.strip()
							else:
								params_str = remainder

							# Parse key=value pairs
							import re

							# Matches key="value with spaces" or key=value
							pattern = r'(\w+)=(?:"([^"]*)"|(\S+))'
							matches = re.findall(pattern, params_str)
							for key, val_quoted, val_simple in matches:
								entry[key] = val_quoted if val_quoted else val_simple

							# Add full line as message just in case
							if "message" not in entry and "msg" not in entry:
								entry["raw_line"] = line

							content.append(entry)
						else:
							content.append({"raw_line": line})
					except Exception:
						content.append({"raw_line": line})

		file_stat = os.stat(file_path)

		# Calculate record count - count ALL records/rows in the JSON file
		record_count = 0

		# Check if this is a debug log file (pattern: qb_debug_logs*.json or *debug_logs*.json)
		is_debug_log_file = (
			filename == "qb_debug_logs.json"
			or filename.startswith("qb_debug_logs")
			or "debug_logs" in filename.lower()
		)

		if is_debug_log_file:
			# Debug logs file is a list of log entries (stored as direct list in JSON)
			if isinstance(content, list):
				# Count all log entries in the list
				record_count = len(content)
			elif isinstance(content, dict):
				# Sometimes debug logs might be wrapped in a dict, check for list values
				# Find the largest list (likely the main log entries)
				max_list_length = 0
				for value in content.values():
					if isinstance(value, list):
						max_list_length = max(max_list_length, len(value))
					elif isinstance(value, dict):
						# Check nested dicts for lists
						for nested_value in value.values():
							if isinstance(nested_value, list):
								max_list_length = max(max_list_length, len(nested_value))
				record_count = max_list_length if max_list_length > 0 else 1
			else:
				record_count = 1
		elif isinstance(content, dict) and "QueryResponse" in content:
			# Standard QuickBooks response format: {"QueryResponse": {"Account": [...], ...}}
			qr = content.get("QueryResponse", {})
			if qr:
				# Find the main data array (skip metadata fields)
				metadata_fields = {"maxResults", "startPosition", "totalCount"}
				for key, value in qr.items():
					if key not in metadata_fields:
						if isinstance(value, list):
							# This is the main data array - count all items
							record_count = len(value)
							break  # Use the first data array found
						elif isinstance(value, dict):
							# Single record
							record_count = 1
							break
				# If no data array found, count might be 0
				if record_count == 0:
					# Check if there are any lists at all
					for value in qr.values():
						if isinstance(value, list) and len(value) > 0:
							record_count = len(value)
							break
			else:
				record_count = 1
		elif isinstance(content, list):
			# Direct list format - count all items
			record_count = len(content)
		elif isinstance(content, dict):
			# Dict format - find the largest list (likely the main data)
			max_list_length = 0
			for value in content.values():
				if isinstance(value, list):
					max_list_length = max(max_list_length, len(value))
				elif isinstance(value, dict):
					# Check nested dicts for lists
					for nested_value in value.values():
						if isinstance(nested_value, list):
							max_list_length = max(max_list_length, len(nested_value))
			record_count = max_list_length if max_list_length > 0 else 1
		else:
			record_count = 1

		return {
			"success": True,
			"filename": filename,
			"content": content,
			"size": file_stat.st_size,
			"size_mb": round(file_stat.st_size / (1024 * 1024), 2),
			"record_count": record_count,
		}

	except Exception as e:
		frappe.log_error(
			f"Error reading debug file {filename}: {e!s}",
			"get_quickbooks_debug_file_content",
		)
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def delete_quickbooks_debug_file(filename):
	"""
	Delete a specific QuickBooks debug JSON file.

	Args:
	    filename (str): Name of the file to delete

	Returns:
	    dict: Success status
	"""
	import os

	from frappe.utils import get_site_path

	try:
		# Security: Only allow files that start with "qb_" and end with ".json" or ".log"
		if not filename.startswith("qb_") or (
			not filename.endswith(".json") and not filename.endswith(".log")
		):
			frappe.throw(_("Invalid filename. Only QuickBooks debug files (.json/.log) are allowed."))

		# Security: Prevent directory traversal
		if ".." in filename or "/" in filename or "\\" in filename:
			frappe.throw(_("Invalid filename. Path traversal not allowed."))

		site_path = get_site_path()
		file_path = os.path.join(site_path, "private", "files", filename)

		if not os.path.exists(file_path):
			frappe.throw(_("File not found: {0}").format(filename))

		# Delete file
		os.remove(file_path)

		return {
			"success": True,
			"message": _("File deleted successfully: {0}").format(filename),
		}

	except Exception as e:
		frappe.log_error(
			f"Error deleting debug file {filename}: {e!s}",
			"delete_quickbooks_debug_file",
		)
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def clear_all_quickbooks_debug_files(company_filter=None):
	"""
	Delete all QuickBooks debug JSON and LOG files.
	Optionally filter by company abbreviation.

	Args:
	    company_filter (str): Company abbreviation to filter files by (optional)

	Returns:
	    dict: Success status and count of deleted files
	"""
	import os

	from frappe.utils import get_site_path

	try:
		site_path = get_site_path()
		private_files_path = os.path.join(site_path, "private", "files")

		if not os.path.exists(private_files_path):
			return {
				"success": True,
				"deleted_count": 0,
				"message": "No debug files directory found",
			}

		# Delete all files that start with "qb_" and end with .json or .log
		deleted_count = 0
		for filename in os.listdir(private_files_path):
			if filename.startswith("qb_") and (filename.endswith(".json") or filename.endswith(".log")):
				# Apply company filter if provided
				if company_filter:
					parts = filename.replace(".json", "").replace(".log", "").split("_")
					company_abbr = None

					# Logic adapted from get_quickbooks_debug_files
					is_debug_log = len(parts) >= 3 and parts[1] == "debug" and parts[2] == "logs"

					if is_debug_log:
						# For debug logs: qb_debug_logs_{company}_{module}...
						if len(parts) >= 4:
							potential_company = parts[3]
							if potential_company.isupper() and len(potential_company) <= 5:
								company_abbr = potential_company
					elif len(parts) >= 2:
						# Regular files: qb_{company}_{data_name}...
						potential_company = parts[1]
						if potential_company.isupper() and len(potential_company) <= 5:
							common_data_names = [
								"debug",
								"log",
								"account",
								"customer",
								"supplier",
								"item",
								"product",
							]
							if potential_company not in common_data_names:
								company_abbr = potential_company

					# If company filter is set but we couldn't extract a company from filename,
					# or if the extracted company doesn't match, SKIP this file.
					if company_abbr != company_filter:
						continue

				file_path = os.path.join(private_files_path, filename)
				try:
					os.remove(file_path)
					deleted_count += 1
				except Exception:
					# Ignore errors deleting individual files
					pass

		return {
			"success": True,
			"deleted_count": deleted_count,
			"message": _("{0} debug files deleted successfully").format(deleted_count),
		}

	except Exception as e:
		frappe.log_error(f"Error clearing debug files: {e!s}", "clear_all_quickbooks_debug_files")
		return {"success": False, "error": str(e), "deleted_count": 0}


from quickbooks_master_sync.quickbooks_master_sync.reconciliation import reconcile_debug_file_content


@frappe.whitelist()
def compare_quickbooks_erpnext_balances(company_name=None, as_of_date=None, tolerance=0.01):
	from quickbooks_master_sync.quickbooks_master_sync.compare_balances import compare_account_balances

	try:
		if not company_name:
			company_name = frappe.defaults.get_user_default("company")

		result = compare_account_balances(
			company_name=company_name, as_of_date=as_of_date, tolerance=flt(tolerance)
		)
		return {"success": True, "result": result}
	except Exception as e:
		import traceback

		frappe.log_error(
			f"Error in compare_quickbooks_erpnext_balances: {e!s}\n{traceback.format_exc()}",
			"compare_quickbooks_erpnext_balances",
		)
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def compare_party_balances(party_type, company_name=None, as_of_date=None, tolerance=0.01):
	from quickbooks_master_sync.quickbooks_master_sync.compare_balances import (
		compare_party_balances as compare_parties,
	)

	try:
		if not company_name:
			company_name = frappe.defaults.get_user_default("company")

		result = compare_parties(
			party_type=party_type, company_name=company_name, as_of_date=as_of_date, tolerance=flt(tolerance)
		)
		return result
	except Exception as e:
		import traceback

		frappe.log_error(
			f"Error in compare_party_balances: {e!s}\n{traceback.format_exc()}", "compare_party_balances"
		)
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def compare_quickbooks_erpnext_date_wise_balances(
	erp_account, qb_id, start_date, end_date, company_name=None, tolerance=0.01
):
	from quickbooks_master_sync.quickbooks_master_sync.compare_balances import compare_account_date_wise

	try:
		if not company_name:
			company_name = frappe.defaults.get_user_default("company")

		result = compare_account_date_wise(
			erp_account=erp_account,
			qb_id=qb_id,
			start_date=start_date,
			end_date=end_date,
			company_name=company_name,
			tolerance=flt(tolerance),
		)
		return {"success": True, "result": result}
	except Exception as e:
		import traceback

		frappe.log_error(
			f"Error in compare_quickbooks_erpnext_date_wise_balances: {e!s}\n{traceback.format_exc()}",
			"compare_quickbooks_erpnext_date_wise_balances",
		)
		return {"success": False, "error": str(e)}
