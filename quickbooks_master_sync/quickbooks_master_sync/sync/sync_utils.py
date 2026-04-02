"""
Common utility functions for QuickBooks sync modules.
This module contains shared helper functions used across multiple sync files.
"""

import json
import os

import frappe
from frappe.utils import flt, get_site_path

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_debug, qb_log_exception
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

# Cache for QuickBooks company name to avoid repeated API calls
_qb_company_cache = None

# Module-level account cache: company -> {unique_qb_id -> account_name}
# This provides basic caching when SyncCache is not available
_account_cache = {}  # {company: {unique_qb_id: account_name}}


def _dbg(module_name, event, payload=None):
	"""
	Debug logging helper for sync modules.

	Args:
	    module_name (str): Name of the sync module (e.g., "sync_suppliers")
	    event (str): Event identifier
	    payload (dict, optional): Additional data to log
	"""
	qb_debug(module_name, event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, use_cache=True, module_name=None):
	"""
	Get the ERPNext company that corresponds to the QuickBooks company.

	First tries to use qb_company parameter if provided, then tries to match
	by QuickBooks company name, and finally falls back to session defaults.

	Args:
	    quickbooks_obj: QuickBooks client instance (optional)
	    qb_company (str): ERPNext company name to use directly (optional)
	    use_cache (bool): Whether to use cached QuickBooks company name
	    module_name (str): Module name for debug logging (optional)

	Returns:
	    str: ERPNext company name
	"""
	global _qb_company_cache

	# Use qb_company parameter if provided (highest priority)
	if qb_company:
		return qb_company

	# Try to get the QuickBooks company name and find matching ERPNext company
	if quickbooks_obj and hasattr(quickbooks_obj, "company_id"):
		try:
			# Use cache if available and enabled
			if _qb_company_cache is None or not use_cache:
				# Get QuickBooks CompanyInfo to find the company name
				company_info = quickbooks_obj.get_single_object("CompanyInfo", quickbooks_obj.company_id)
				ci = company_info.get("CompanyInfo", company_info) or {}
				qb_company_name = ci.get("CompanyName") or ci.get("LegalName")
				if use_cache:
					_qb_company_cache = qb_company_name
			else:
				qb_company_name = _qb_company_cache

			if qb_company_name:
				# Find ERPNext company with matching name
				erpnext_company = frappe.db.get_value("Company", {"company_name": qb_company_name}, "name")
				if erpnext_company:
					if module_name:
						_dbg(
							module_name,
							"_get_quickbooks_company:matched",
							{
								"qb_company_name": qb_company_name,
								"erpnext_company": erpnext_company,
							},
						)
					return erpnext_company
		except Exception as e:
			if module_name:
				_dbg(
					module_name,
					"_get_quickbooks_company:qb_lookup_failed",
					{"error": str(e)},
				)
			# Continue to fallback if lookup fails
			pass

	# Fallback to session defaults
	return (
		frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)


def reset_company_cache():
	"""Reset the QuickBooks company cache."""
	global _qb_company_cache
	_qb_company_cache = None


def reset_account_cache(company=None):
	"""Reset the account cache for a specific company or all companies."""
	global _account_cache
	if company:
		_account_cache.pop(company, None)
	else:
		_account_cache.clear()


def ensure_fiscal_year_for_date(company, posting_date, module_name=None):
	"""
	Ensure there is an active Fiscal Year covering posting_date.
	Our convention: Fiscal Year runs from July 1 (YYYY-07-01) to June 30 (YYYY+1-06-30).
	If missing, create it and link the company.

	Args:
	    company: Company name
	    posting_date: Posting date for the transaction
	    module_name: Module name for debug logging (optional)

	Returns:
	    str: Fiscal year name, or None on error
	"""
	try:
		from datetime import date

		from frappe.utils import getdate

		if not posting_date:
			return None

		txn_date = getdate(posting_date)

		# Determine fiscal year start based on July 1 → June 30 rule
		start_year = txn_date.year if txn_date.month >= 7 else txn_date.year - 1
		start_date = date(start_year, 7, 1)
		end_date = date(start_year + 1, 6, 30)
		fy_year = f"{start_year}-{start_year + 1}"

		# Check if fiscal year exists by year field first
		existing = frappe.db.get_value("Fiscal Year", {"year": fy_year}, "name")

		# If not found by year, check by date range
		if not existing:
			existing = frappe.db.get_value(
				"Fiscal Year",
				{"year_start_date": start_date, "year_end_date": end_date},
				"name",
			)

		if not existing:
			# Create Fiscal Year
			fy = frappe.new_doc("Fiscal Year")
			fy.year = fy_year  # Set the year field
			fy.year_start_date = start_date
			fy.year_end_date = end_date

			# Mark as auto created if field exists
			if hasattr(fy, "auto_created"):
				fy.auto_created = 1

			# Ensure it's not disabled
			if hasattr(fy, "disabled"):
				fy.disabled = 0

			# Insert with proper flags
			fy.flags.ignore_permissions = True
			fy.flags.ignore_validate = True
			fy.insert()
			frappe.db.commit()

			existing = fy.name
			if module_name:
				_dbg(
					module_name,
					"ensure_fiscal_year:created",
					{
						"fiscal_year": existing,
						"year": fy_year,
						"start_date": str(start_date),
						"end_date": str(end_date),
					},
				)

		# Make sure it's active (not disabled)
		try:
			frappe.db.set_value("Fiscal Year", existing, "disabled", 0)
			frappe.db.commit()
		except Exception as e:
			if module_name:
				_dbg(
					module_name,
					"ensure_fiscal_year:disable_error",
					{
						"fiscal_year": existing,
						"error": str(e),
					},
				)

		# Link Fiscal Year to company if child table exists
		try:
			fy_doc = frappe.get_doc("Fiscal Year", existing)
			if hasattr(fy_doc, "companies"):
				already_linked = False
				for row in getattr(fy_doc, "companies", []):
					if getattr(row, "company", None) == company:
						already_linked = True
						break
				if not already_linked and company:
					fy_doc.append("companies", {"company": company})
					fy_doc.flags.ignore_permissions = True
					fy_doc.flags.ignore_validate = True
					fy_doc.save()
					frappe.db.commit()
					if module_name:
						_dbg(
							module_name,
							"ensure_fiscal_year:linked_company",
							{
								"fiscal_year": existing,
								"company": company,
							},
						)
		except Exception as e:
			if module_name:
				_dbg(
					module_name,
					"ensure_fiscal_year:link_error",
					{
						"fiscal_year": existing,
						"company": company,
						"error": str(e),
					},
				)

		if module_name:
			_dbg(
				module_name,
				"ensure_fiscal_year:ok",
				{
					"company": company,
					"posting_date": str(posting_date),
					"fiscal_year": existing,
					"start_date": str(start_date),
					"end_date": str(end_date),
				},
			)
		return existing

	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"ensure_fiscal_year:error",
				{
					"company": company,
					"posting_date": str(posting_date),
					"error": str(e),
					"error_type": type(e).__name__,
				},
			)
		frappe.db.rollback()
		# Don't block the caller; let the original operation surface its own error
		return None


def _resolve_country_name(country_code):
	"""
	Resolve country code to country name.

	Args:
	    country_code (str): Country code (e.g., "US", "IN")

	Returns:
	    str: Country name or None if not found
	"""
	if not country_code:
		return None
	return frappe.db.get_value("Country", {"code": country_code}, "name") or None


def _get_company_abbr():
	"""
	Get company abbreviation from current company context.

	Returns:
	    str: Company abbreviation, or empty string if not found
	"""
	try:
		# Try to get company from user defaults
		company = (
			frappe.defaults.get_user_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
		)

		if company:
			company_abbr = frappe.db.get_value("Company", company, "abbr")
			if company_abbr:
				return company_abbr
	except Exception:
		pass

	return ""


def get_qb_data_from_json(data_name, module_name=None):
	"""
	Load QuickBooks data from JSON file if it exists (cache check before API call).
	First tries to find file with company abbreviation, then falls back to file without.

	Args:
	    data_name (str): Name identifier for the data (e.g., "customers", "suppliers")
	    module_name (str, optional): Module name for debug logging

	Returns:
	    list or None: The data from JSON file, or None if file doesn't exist
	"""
	try:
		site_path = get_site_path()
		private_files_path = os.path.join(site_path, "private", "files")

		if not os.path.exists(private_files_path):
			return None

		# Try to find file with company abbreviation first, then fall back to without
		company_abbr = _get_company_abbr()
		company_suffix = f"_{company_abbr}" if company_abbr else ""

		# Priority 1: Look for file with company abbreviation: qb_{company_abbr}_{data_name}_full_response.json
		cache_file = os.path.join(private_files_path, f"qb{company_suffix}_{data_name}_full_response.json")

		# Priority 2: Fall back to file without company abbreviation (backward compatibility)
		if not os.path.exists(cache_file):
			cache_file = os.path.join(private_files_path, f"qb_{data_name}_full_response.json")

		if not os.path.exists(cache_file):
			if module_name:
				_dbg(
					module_name,
					"get_qb_data_from_json:not_found",
					{
						"data_name": data_name,
						"cache_file": cache_file,
						"files_path_exists": os.path.exists(private_files_path),
					},
				)
			return None

		if module_name:
			_dbg(
				module_name,
				"get_qb_data_from_json:file_found",
				{
					"data_name": data_name,
					"cache_file": os.path.basename(cache_file),
				},
			)

		latest_file = cache_file

		with open(latest_file, encoding="utf-8") as f:
			data = json.load(f)

		# Extract the actual data from the response structure
		# Handle both direct list and QueryResponse structure
		if isinstance(data, dict):
			# Try to extract from QueryResponse structure
			query_response = data.get("QueryResponse", {})
			if query_response:
				# Get the first list-like value (e.g., "Customer", "Account", "Item")
				for _key, value in query_response.items():
					if isinstance(value, list):
						if module_name:
							_dbg(
								module_name,
								"get_qb_data_from_json:loaded",
								{
									"data_name": data_name,
									"file": os.path.basename(latest_file),
									"count": len(value),
								},
							)
						return value
					elif isinstance(value, dict):
						# Single item response
						if module_name:
							_dbg(
								module_name,
								"get_qb_data_from_json:loaded",
								{
									"data_name": data_name,
									"file": os.path.basename(latest_file),
									"count": 1,
								},
							)
						return [value]
			# If no QueryResponse, return the data as-is if it's a list
			if isinstance(data, list):
				if module_name:
					_dbg(
						module_name,
						"get_qb_data_from_json:loaded",
						{
							"data_name": data_name,
							"file": os.path.basename(latest_file),
							"count": len(data),
						},
					)
				return data
		elif isinstance(data, list):
			if module_name:
				_dbg(
					module_name,
					"get_qb_data_from_json:loaded",
					{
						"data_name": data_name,
						"file": os.path.basename(latest_file),
						"count": len(data),
					},
				)
			return data

		# If we get here, the data format is unexpected
		if module_name:
			_dbg(
				module_name,
				"get_qb_data_from_json:unexpected_format",
				{
					"data_name": data_name,
					"file": os.path.basename(latest_file),
					"data_type": type(data).__name__,
					"data_keys": list(data.keys()) if isinstance(data, dict) else None,
				},
			)
		# Return None to trigger API call for unexpected format
		return None

	except json.JSONDecodeError as e:
		if module_name:
			_dbg(
				module_name,
				"get_qb_data_from_json:json_decode_error",
				{
					"error": str(e),
					"data_name": data_name,
					"cache_file": f"qb_{data_name}_full_response.json",
				},
			)
		# JSON decode error - file is corrupted, return None to trigger API call
		return None
	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"get_qb_data_from_json:error",
				{
					"error": str(e),
					"error_type": type(e).__name__,
					"data_name": data_name,
					"cache_file": f"qb_{data_name}_full_response.json",
				},
			)
		# Other errors - return None to trigger API call
		return None


def save_qb_data_to_json(data, data_name, module_name=None, full_response=None):
	"""
	Save QuickBooks data to JSON file for debugging.
	Files are saved with company abbreviation in the filename for multi-company support.

	Args:
	    data: The data to save (can be dict, list, etc.)
	    data_name (str): Name identifier for the data
	        (e.g., "customers", "suppliers")
	    module_name (str, optional): Module name for debug logging
	    full_response (dict, optional): Full response object to save separately

	Returns:
	    dict: Information about saved files (or None if save failed)
	"""
	try:
		site_path = get_site_path()
		private_files_path = os.path.join(site_path, "private", "files")
		os.makedirs(private_files_path, exist_ok=True)

		# Get company abbreviation for filename
		company_abbr = _get_company_abbr()
		company_suffix = f"_{company_abbr}" if company_abbr else ""

		saved_files = {}

		# Save full response if provided (always replace single file, no timestamp)
		# Format: qb_{company_abbr}_{data_name}_full_response.json
		if full_response is not None:
			# Prevent directory traversal by using basename
			safe_data_name = os.path.basename(data_name).replace("/", "_").replace("\\", "_")
			full_response_filename = f"qb{company_suffix}_{safe_data_name}_full_response.json"
			full_response_path = os.path.abspath(os.path.join(private_files_path, full_response_filename))

			# Verify path is within private_files_path
			if not full_response_path.startswith(os.path.abspath(private_files_path)):
				raise Exception(frappe._("Invalid file path: {0}").format(full_response_filename))

			with open(full_response_path, "w", encoding="utf-8") as f:
				json.dump(full_response, f, indent=2, ensure_ascii=False, default=str)
			saved_files["full_response"] = full_response_filename

		# # Save main data
		# data_filename = f"qb_{data_name}_{timestamp}.json"
		# data_path = os.path.join(private_files_path, data_filename)
		# with open(data_path, "w", encoding="utf-8") as f:
		#     json.dump(data, f, indent=2, ensure_ascii=False, default=str)
		# saved_files["data"] = data_filename

		if module_name:
			_dbg(
				module_name,
				"save_qb_data_to_json:saved",
				{
					"data_name": data_name,
					"files": saved_files,
					"count": len(data) if isinstance(data, list) else 1,
				},
			)

		return saved_files
	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"save_qb_data_to_json:error",
				{"error": str(e), "data_name": data_name},
			)
		return None


def query_with_pagination(
	quickbooks_obj,
	base_query,
	object_name,
	module_name=None,
	max_results=100,
	data_name=None,
	use_cache=False,
):
	"""
	Query QuickBooks with pagination support to fetch all records.
	Only checks if data exists in JSON cache if use_cache=True.
	Otherwise makes API call and saves to JSON cache.

	Args:
	    quickbooks_obj: QuickBooks client instance
	    base_query (str): Base SQL query without pagination (e.g., "SELECT * FROM Customer")
	    object_name (str): Name of the QuickBooks object in QueryResponse (e.g., "Customer", "Item", "Account")
	    module_name (str, optional): Module name for debug logging
	    max_results (int): Maximum results per page (default: 100, QuickBooks max is 1000)
	    data_name (str, optional): Name identifier for JSON cache (e.g., "customers_list", "accounts_list")
	        If not provided, will be derived from object_name
	    use_cache (bool): Whether to try loading from JSON cache first (default: False)

	Returns:
	    list: All records from all pages
	"""
	# Determine data_name for JSON cache if not provided
	if not data_name:
		# Convert object_name to data_name format (e.g., "Customer" -> "customers_list")
		data_name = f"{object_name.lower()}s_list"

	# Check JSON cache first if requested
	cached_data = None
	if use_cache:
		cached_data = get_qb_data_from_json(data_name, module_name)

	# Check if cache exists and is valid (not None)
	# Empty list [] is valid cache (means API returned no results previously)
	if use_cache and cached_data is not None:
		# Even empty list is valid cache (means API returned no results)
		if module_name:
			_dbg(
				module_name,
				"query_with_pagination:using_cache",
				{
					"data_name": data_name,
					"count": len(cached_data) if isinstance(cached_data, list) else 1,
					"cache_file": f"qb_{data_name}_full_response.json",
					"is_empty": len(cached_data) == 0 if isinstance(cached_data, list) else False,
				},
			)
		# Return cached data (even if empty list - that's valid cached result)
		result = cached_data if isinstance(cached_data, list) else [cached_data]
		if module_name:
			_dbg(
				module_name,
				"query_with_pagination:returning_cache",
				{
					"data_name": data_name,
					"result_count": len(result),
				},
			)
		return result

	# Cache miss - proceed with API call
	if module_name:
		_dbg(
			module_name,
			"query_with_pagination:cache_miss",
			{
				"data_name": data_name,
				"query": base_query,
			},
		)

	all_records = []
	all_pages_responses = []  # Store each page's full response for debugging
	start_position = 1
	page_num = 1

	while True:
		# Build query with pagination
		if start_position > 1:
			query = f"{base_query} STARTPOSITION {start_position} MAXRESULTS {max_results}"
		else:
			query = f"{base_query} MAXRESULTS {max_results}"

		# if module_name:
		#     _dbg(module_name, "query_with_pagination:page", {
		#         "page": page_num,
		#         "start_position": start_position,
		#         "query": query
		#     })

		try:
			response = quickbooks_obj.query(query)

			# Save each page's full response for debugging
			if module_name:
				_dbg(
					module_name,
					"query_with_pagination:page_response",
					{
						"page": page_num,
						"start_position": start_position,
						"max_results": max_results,
						"response_keys": list(response.keys()) if isinstance(response, dict) else "not_dict",
					},
				)

			# Store this page's full response
			all_pages_responses.append(
				{
					"page": page_num,
					"start_position": start_position,
					"max_results": max_results,
					"response": response,
				}
			)

			# Log full response for debugging
			# if module_name:
			#     _dbg(module_name, "query_with_pagination:raw_response", {
			#         "page": page_num,
			#         "response_keys": list(response.keys()) if isinstance(response, dict) else "not_dict",
			#         "has_query_response": "QueryResponse" in response if isinstance(response, dict) else False,
			#         "has_fault": "Fault" in response or "fault" in response if isinstance(response, dict) else False,
			#     })

			# Check for errors in response
			if isinstance(response, dict):
				fault = response.get("Fault") or response.get("fault")
				if fault:
					error_msg = str(fault)
					if module_name:
						_dbg(
							module_name,
							"query_with_pagination:fault_detected",
							{"page": page_num, "fault": error_msg},
						)
					raise Exception(f"QuickBooks API Fault: {error_msg}")

			query_response = response.get("QueryResponse", {})

			# Log query response structure
			# if module_name:
			#     _dbg(module_name, "query_with_pagination:query_response", {
			#         "page": page_num,
			#         "query_response_keys": list(query_response.keys()) if isinstance(query_response, dict) else "not_dict",
			#         "has_object": object_name in query_response if isinstance(query_response, dict) else False,
			#         "max_results": query_response.get("maxResults") if isinstance(query_response, dict) else None,
			#         "start_position": query_response.get("startPosition") if isinstance(query_response, dict) else None,
			#     })

			# Get records from this page
			page_records = query_response.get(object_name, [])
			if not isinstance(page_records, list):
				page_records = [page_records] if page_records else []

			# if module_name:
			#     _dbg(module_name, "query_with_pagination:page_records_extracted", {
			#         "page": page_num,
			#         "record_count": len(page_records),
			#         "is_list": isinstance(page_records, list),
			#         "first_record_id": page_records[0].get("Id") if page_records and len(page_records) > 0 else None,
			#     })

			if not page_records:
				# No more records
				# if module_name:
				#     _dbg(module_name, "query_with_pagination:no_more", {
				#         "page": page_num,
				#         "query_response": query_response,
				#         "object_name": object_name
				#     })
				break

			all_records.extend(page_records)

			# if module_name:
			#     _dbg(module_name, "query_with_pagination:fetched_page", {
			#         "page": page_num,
			#         "count": len(page_records),
			#         "total_so_far": len(all_records)
			#     })

			# Check if there are more pages
			# If we got fewer results than max_results, we're done
			if len(page_records) < max_results:
				# if module_name:
				#     _dbg(module_name, "query_with_pagination:last_page", {
				#         "page": page_num,
				#         "count": len(page_records)
				#     })
				break

			# Move to next page
			start_position += len(page_records)
			page_num += 1

		except Exception as e:
			if module_name:
				# _dbg(module_name, "query_with_pagination:error", {
				#     "page": page_num,
				#     "error": str(e)
				# })
				qb_log_exception(
					method="query_with_pagination",
					err=e,
					request_data={"page": page_num, "start_position": start_position, "query": query},
					module=module_name or "sync_utils",
				)
			# Continue with what we have so far
			break

	if module_name:
		_dbg(
			module_name,
			"query_with_pagination:complete",
			{
				"total_count": len(all_records),
				"pages": page_num,
				"total_pages_fetched": len(all_pages_responses),
			},
		)

	# Save to JSON cache for future use (always save, even if empty, so file exists)
	# Empty results are valid cache - means API returned no results
	try:
		# Save accumulated data (all pages combined)
		save_qb_data_to_json(
			data=all_records,
			data_name=data_name,
			module_name=module_name,
			full_response={"QueryResponse": {object_name: all_records}},
		)

		# Also save all pages' responses separately for debugging pagination
		if all_pages_responses:
			# Save pagination debug file with all pages
			site_path = get_site_path()
			private_files_path = os.path.join(site_path, "private", "files")
			os.makedirs(private_files_path, exist_ok=True)

			# Get company abbreviation for filename
			company_abbr = _get_company_abbr()
			company_suffix = f"_{company_abbr}" if company_abbr else ""

			# Save pagination responses: qb_{company_abbr}_{data_name}_pagination_full_response.json
			pagination_filename = f"qb{company_suffix}_{data_name}_pagination_full_response.json"
			pagination_path = os.path.join(private_files_path, pagination_filename)

			pagination_data = {
				"total_records": len(all_records),
				"total_pages": len(all_pages_responses),
				"max_results_per_page": max_results,
				"pages": all_pages_responses,
			}

			with open(pagination_path, "w", encoding="utf-8") as f:
				json.dump(pagination_data, f, indent=2, ensure_ascii=False, default=str)

			if module_name:
				_dbg(
					module_name,
					"query_with_pagination:pagination_saved",
					{
						"pagination_file": pagination_filename,
						"total_pages": len(all_pages_responses),
						"total_records": len(all_records),
					},
				)
	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"query_with_pagination:cache_save_error",
				{"error": str(e), "data_name": data_name},
			)

	return all_records


def get_account_from_quickbooks_ref(
	qb_account_ref,
	company=None,
	account_type=None,
	root_type=None,
	fallback_account=None,
	module_name=None,
	strict_qb_id_only=False,
	cache=None,
):
	"""
	Get ERPNext account from QuickBooks account reference, ensuring exact match when available.

	This function prioritizes QuickBooks account references to maintain balance accuracy.
	Only falls back to defaults if QuickBooks doesn't provide account information.

	Args:
	    qb_account_ref: QuickBooks account reference dict with 'value' (ID) and/or 'name'
	    company: Company name (optional, will resolve if not provided)
	    account_type: Filter by account_type (e.g., "Receivable", "Payable", "Expense Account")
	    root_type: Filter by root_type (e.g., "Asset", "Liability", "Income", "Expense")
	    fallback_account: Account name to use if QuickBooks ref not found (optional)
	    module_name: Module name for debug logging (optional)
	    strict_qb_id_only: If True, ONLY search by QuickBooks ID (no name fallback).
	                      Use True for transactions (SI, PI, Journal Entries) to ensure accuracy.
	    cache: SyncCache instance (optional) - if provided, uses cache to avoid DB queries

	Returns:
	    str: ERPNext account name, or None if not found
	"""
	if not qb_account_ref:
		if module_name:
			_dbg(
				module_name,
				"get_account_from_qb_ref:no_ref",
				{
					"fallback": fallback_account,
					"note": "QuickBooks did not provide account reference, using fallback",
				},
			)
		return fallback_account

	# Resolve company
	if not company:
		company = (
			frappe.defaults.get_user_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
		)

	if not company:
		if module_name:
			_dbg(
				module_name,
				"get_account_from_qb_ref:no_company",
				{"qb_ref": qb_account_ref, "fallback": fallback_account},
			)
		return fallback_account

	# Extract QuickBooks account ID and name
	qb_account_id = None
	qb_account_name = None

	if isinstance(qb_account_ref, dict):
		qb_account_id = qb_account_ref.get("value")
		qb_account_name = qb_account_ref.get("name")
	else:
		# If it's a string, treat as ID
		qb_account_id = str(qb_account_ref)

	account = None

	# Priority 1: Find by QuickBooks account ID (exact match) - use unique QB ID
	if qb_account_id:
		unique_qb_account_id = make_unique_qb_id(qb_account_id, company)

		# Try cache first (if provided) - much faster than DB query
		if cache:
			cached_account = cache.get_account_by_qb_id(unique_qb_account_id)
			if cached_account:
				# Verify account matches all filters and is not a group account
				account_doc = cache.get_account_doc(cached_account)
				if account_doc and account_doc.get("is_group") != 1:
					# Check if account matches account_type filter (if provided)
					if account_type and account_doc.get("account_type") != account_type:
						# Account doesn't match account_type filter, fall through to DB query
						account_doc = None
					# Check if account matches root_type filter (if provided)
					if root_type and account_doc and account_doc.get("root_type") != root_type:
						# Account doesn't match root_type filter, fall through to DB query
						account_doc = None

					if account_doc:
						account = cached_account
						if module_name:
							_dbg(
								module_name,
								"get_account_from_qb_ref:found_in_cache",
								{
									"qb_account_id": qb_account_id,
									"unique_qb_account_id": unique_qb_account_id,
									"erpnext_account": account,
									"account_type": account_doc.get("account_type"),
									"root_type": account_doc.get("root_type"),
									"note": "Found in cache - avoiding DB query",
								},
							)
						return account
				# If group account or doesn't match filters, fall through to DB query

		# Try module-level cache if SyncCache not provided
		if not cache and company:
			if company not in _account_cache:
				_account_cache[company] = {}
			company_cache = _account_cache[company]

			if unique_qb_account_id in company_cache:
				cached_account = company_cache[unique_qb_account_id]
				# Verify account still exists and matches filters (single query for efficiency)
				if account_type or root_type:
					# Need to verify filters - query account_type and root_type
					account_data = frappe.db.get_value(
						"Account", cached_account, ["account_type", "root_type"], as_dict=True
					)
					if account_data:
						# Check if account matches filters
						if account_type and account_data.get("account_type") != account_type:
							# Account doesn't match account_type filter, fall through to DB query
							account_data = None
						elif root_type and account_data and account_data.get("root_type") != root_type:
							# Account doesn't match root_type filter, fall through to DB query
							account_data = None

						if account_data:
							account = cached_account
							if module_name:
								_dbg(
									module_name,
									"get_account_from_qb_ref:found_in_module_cache",
									{
										"qb_account_id": qb_account_id,
										"unique_qb_account_id": unique_qb_account_id,
										"erpnext_account": account,
										"note": "Found in module-level cache - avoiding DB query",
									},
								)
							return account
					else:
						# Account was deleted, remove from cache
						del company_cache[unique_qb_account_id]
				else:
					# No filters to verify - just check if account exists
					if frappe.db.exists("Account", cached_account):
						account = cached_account
						if module_name:
							_dbg(
								module_name,
								"get_account_from_qb_ref:found_in_module_cache",
								{
									"qb_account_id": qb_account_id,
									"unique_qb_account_id": unique_qb_account_id,
									"erpnext_account": account,
									"note": "Found in module-level cache - avoiding DB query",
								},
							)
						return account
					else:
						# Account was deleted, remove from cache
						del company_cache[unique_qb_account_id]

		# Cache miss or no cache - query database
		# First try with full filters (account_type and root_type)
		filters = {
			"quickbooks_account_id": unique_qb_account_id,
			"company": company,
			"is_group": 0,  # Only non-group accounts can be used in transactions
		}
		if account_type:
			filters["account_type"] = account_type
		if root_type:
			filters["root_type"] = root_type

		if module_name:
			_dbg(
				module_name,
				"get_account_from_qb_ref:searching",
				{
					"qb_account_id": qb_account_id,
					"unique_qb_account_id": unique_qb_account_id,
					"company": company,
					"filters": filters,
					"cache_used": cache is not None,
				},
			)

		account = frappe.db.get_value("Account", filters, "name")

		# If not found with strict filters, try without account_type (may be empty for leaf accounts)
		# BUT: Skip this fallback if strict_qb_id_only=True (we want exact match only)
		if not account and account_type and not strict_qb_id_only:
			fallback_filters = {
				"quickbooks_account_id": unique_qb_account_id,
				"company": company,
				"is_group": 0,
			}
			if root_type:
				fallback_filters["root_type"] = root_type

			account = frappe.db.get_value("Account", fallback_filters, "name")

			# Store in module-level cache if found
			if account and company and not cache:
				if company not in _account_cache:
					_account_cache[company] = {}
				_account_cache[company][unique_qb_account_id] = account

			if account and module_name:
				_dbg(
					module_name,
					"get_account_from_qb_ref:found_by_id_fallback",
					{
						"qb_account_id": qb_account_id,
						"unique_qb_account_id": unique_qb_account_id,
						"erpnext_account": account,
						"note": "Found without account_type filter (may have empty account_type)",
						"requested_account_type": account_type,
						"strict_qb_id_only": strict_qb_id_only,
					},
				)

		# If still not found, allow a group account match by QB ID, then resolve to a leaf account.
		# This is safe for transactions because ERPNext disallows group accounts in entries.
		if not account:
			group_filters = {
				"quickbooks_account_id": unique_qb_account_id,
				"company": company,
			}
			if account_type:
				group_filters["account_type"] = account_type
			if root_type:
				group_filters["root_type"] = root_type

			group_or_leaf = frappe.db.get_value("Account", group_filters, "name")
			if group_or_leaf:
				leaf = get_leaf_account_from_group(
					group_or_leaf,
					company=company,
					account_type=account_type,
					module_name=module_name,
				)
				if leaf:
					account = leaf

					# Store in module-level cache if found
					if account and company and not cache:
						if company not in _account_cache:
							_account_cache[company] = {}
						_account_cache[company][unique_qb_account_id] = account

		if account:
			# Store in module-level cache if found (for direct QB ID match)
			if company and not cache:
				if company not in _account_cache:
					_account_cache[company] = {}
				_account_cache[company][unique_qb_account_id] = account
			return account
		else:
			if module_name:
				_dbg(
					module_name,
					"get_account_from_qb_ref:not_found_by_id",
					{
						"qb_account_id": qb_account_id,
						"unique_qb_account_id": unique_qb_account_id,
						"company": company,
						"filters": filters,
						"note": "Account not found with these filters - will try fallback methods",
					},
				)

	# If strict_qb_id_only is True, don't fall back to broad name matching.
	# However, for deleted accounts we allow a very narrow exact-name fallback:
	# QB names often contain " (deleted)" but ERPNext accounts are created without it.
	if strict_qb_id_only:
		if not account and qb_account_name and company:
			qb_name_lower = str(qb_account_name).lower()
			if "(deleted)" in qb_name_lower:
				clean_name = str(qb_account_name).replace(" (deleted)", "").strip()
				if clean_name:
					account = frappe.db.get_value(
						"Account",
						{"account_name": clean_name, "company": company, "is_group": 0},
						"name",
					)

					if not account:
						# If account name suffix is configured, try that exact variant
						try:
							qb_settings = frappe.get_doc("Quickbooks Settings")
							qb_suffix = getattr(qb_settings, "account_name_suffix", "") or ""
						except Exception:
							qb_suffix = ""

						if qb_suffix:
							suffixed = f"{clean_name} - {qb_suffix}"
							account = frappe.db.get_value(
								"Account",
								{"account_name": suffixed, "company": company, "is_group": 0},
								"name",
							)

					if account:
						if module_name:
							unique_qb_account_id = (
								make_unique_qb_id(qb_account_id, company) if qb_account_id else None
							)
							_dbg(
								module_name,
								"get_account_from_qb_ref:matched_deleted_by_name",
								{
									"qb_account_id": qb_account_id,
									"unique_qb_account_id": unique_qb_account_id,
									"company": company,
									"qb_account_name": qb_account_name,
									"clean_name": clean_name,
									"erpnext_account": account,
									"note": "Strict QB-ID lookup failed; matched deleted account by cleaned name",
								},
							)
						return account

		if module_name:
			# Get the unique_qb_account_id for logging

			unique_qb_account_id = make_unique_qb_id(qb_account_id, company) if qb_account_id else None

			_dbg(
				module_name,
				"get_account_from_qb_ref:strict_mode_not_found",
				{
					"qb_account_id": qb_account_id,
					"unique_qb_account_id": unique_qb_account_id,
					"company": company,
					"qb_account_name": qb_account_name,
					"account_type": account_type,
					"root_type": root_type,
					"fallback": fallback_account,
					"error": "Account not found by QuickBooks ID (strict mode - no name fallback)",
				},
			)
		return fallback_account

	# Priority 2: Find by QuickBooks account name (if ID lookup failed and not strict mode)
	if not account and qb_account_name:
		filters = {"account_name": qb_account_name, "company": company, "is_group": 0}
		if account_type:
			filters["account_type"] = account_type
		if root_type:
			filters["root_type"] = root_type

		account = frappe.db.get_value("Account", filters, "name")

		if account:
			if module_name:
				_dbg(
					module_name,
					"get_account_from_qb_ref:found_by_name",
					{
						"qb_account_name": qb_account_name,
						"erpnext_account": account,
						"qb_account_id": qb_account_id,
						"note": "Found by name (ID lookup failed)",
					},
				)
			return account

		if account:
			if module_name:
				_dbg(
					module_name,
					"get_account_from_qb_ref:found_by_partial_name",
					{
						"qb_account_name": qb_account_name,
						"erpnext_account": account,
						"note": "Found by partial name match",
					},
				)
			return account

	# Priority 4: Hierarchical Name Search (Split by colon)
	if not account and qb_account_name and ":" in qb_account_name:
		# Hierarchical name (e.g. "Asset:Cash:Bank") -> try "Bank"
		parts = [p.strip() for p in qb_account_name.split(":")]
		leaf_name = parts[-1]

		if leaf_name:
			filters = {"account_name": leaf_name, "company": company, "is_group": 0}
			if account_type:
				filters["account_type"] = account_type
			if root_type:
				filters["root_type"] = root_type

			account = frappe.db.get_value("Account", filters, "name")

			if account:
				if module_name:
					_dbg(
						module_name,
						"get_account_from_qb_ref:found_by_hierarchical_leaf",
						{
							"qb_account_name": qb_account_name,
							"leaf_name": leaf_name,
							"erpnext_account": account,
							"note": "Found by hierarchical leaf part (after colon)",
						},
					)
				return account

	# QuickBooks account not found - use fallback or return None
	if module_name:
		_dbg(
			module_name,
			"get_account_from_qb_ref:not_found",
			{
				"qb_account_id": qb_account_id,
				"qb_account_name": qb_account_name,
				"account_type": account_type,
				"root_type": root_type,
				"fallback": fallback_account,
				"strict_qb_id_only": strict_qb_id_only,
				"warning": "QuickBooks account not found in ERPNext, using fallback",
			},
		)

	return fallback_account


def ensure_account_from_quickbooks_ref(
	qb_account_ref,
	quickbooks_obj=None,
	company=None,
	account_type=None,
	root_type=None,
	fallback_account=None,
	module_name=None,
	strict_qb_id_only=False,
	cache=None,
):
	"""
	Get an ERPNext account from a QuickBooks AccountRef, and if missing try to create it from QuickBooks.

	This is intended for transactional sync flows where:
	- We want strict QB-ID mapping for correctness
	- But QuickBooks may reference accounts that were deleted or never synced into ERPNext

	Behavior:
	- First calls `get_account_from_quickbooks_ref(...)`.
	- If no account is found and `quickbooks_obj` is provided, fetch the QB Account by ID and
	  create it in ERPNext via `sync_account.create_account`, then retry the lookup.
	- Returns the mapped account or `fallback_account`.
	"""
	if not qb_account_ref:
		return fallback_account

	qb_account_id = qb_account_ref.get("value")

	# Try cache first if available (fastest)
	if cache and qb_account_id:
		# Check by strict ID match first
		cached_account = cache.get_account_by_qb_id(qb_account_id)
		if cached_account:
			return cached_account

	def _enable_if_disabled(account_name, reason=None):
		"""ERPNext blocks posting to disabled accounts; enable when referenced by QB transactions."""
		if not account_name:
			return
		try:
			disabled = frappe.db.get_value("Account", account_name, "disabled")
			if disabled:
				frappe.db.set_value("Account", account_name, "disabled", 0)
				if module_name:
					_dbg(
						module_name,
						"ensure_account_from_qb_ref:enabled_disabled_account",
						{
							"erpnext_account": account_name,
							"reason": reason or "Referenced by transaction sync",
							"note": "Enabled disabled account to allow GL posting",
						},
					)
		except Exception:
			# Don't break sync if we can't toggle this flag for some reason
			return

	account = get_account_from_quickbooks_ref(
		qb_account_ref=qb_account_ref,
		company=company,
		account_type=account_type,
		root_type=root_type,
		fallback_account=fallback_account,
		module_name=module_name,
		strict_qb_id_only=strict_qb_id_only,
		cache=cache,
	)

	# CRITICAL: If the account is a group, we MUST resolve it to a leaf account
	# because ERPNext doesn't allow group accounts in Ledger entries.
	if account:
		is_group = False
		if cache:
			acc_doc = cache.get_account_doc(account)
			is_group = acc_doc.get("is_group") == 1 if acc_doc else False
		else:
			is_group = frappe.db.get_value("Account", account, "is_group") == 1

		if is_group:
			leaf = get_leaf_account_from_group(
				account, company=company, account_type=account_type, module_name=module_name
			)
			if leaf:
				if module_name:
					_dbg(
						module_name,
						"ensure_account_from_qb_ref:resolved_group_to_leaf",
						{
							"group_account": account,
							"leaf_account": leaf,
							"note": "Automatically resolved group account to leaf for transactional integrity",
						},
					)
				account = leaf
			else:
				if module_name:
					_dbg(
						module_name,
						"ensure_account_from_qb_ref:group_resolution_failed",
						{
							"group_account": account,
							"error": "Could not find any postable leaf accounts under this group",
						},
					)
				# Fall through to fallback or creation if resolution failed

	if account:
		qb_name = qb_account_ref.get("name") if isinstance(qb_account_ref, dict) else None
		if qb_name and "(deleted)" in str(qb_name).lower():
			_enable_if_disabled(account, reason="QuickBooks AccountRef indicates (deleted)")
		return account

	if not quickbooks_obj:
		return fallback_account

	if not isinstance(qb_account_ref, dict):
		return fallback_account

	qb_account_id = qb_account_ref.get("value")
	qb_account_name = qb_account_ref.get("name")
	if not qb_account_id:
		return fallback_account

	# Resolve company (needed for unique QB ID generation inside create_account)
	if not company:
		company = (
			frappe.defaults.get_user_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
		)

	if not company:
		return fallback_account

	try:
		qb_resp = quickbooks_obj.get_single_object("Account", qb_account_id)
		qb_account = qb_resp.get("Account", qb_resp) if isinstance(qb_resp, dict) else None
		if not isinstance(qb_account, dict):
			return fallback_account

		# Normalize deleted account names so ERPNext doesn't store " (deleted)" in account_name
		if qb_account_name and "(deleted)" in str(qb_account_name).lower():
			clean_name = str(qb_account_name).replace(" (deleted)", "").strip()
			if clean_name:
				qb_account["Name"] = clean_name
			# QB deleted accounts are usually inactive. ERPNext disallows GL posting to disabled accounts,
			# but historical transactions may still reference them.
			qb_account["Active"] = True
		elif qb_account.get("Active") is False:
			# Inactive in QB but referenced in a transaction: keep ERP account enabled so GL can be posted.
			qb_account["Active"] = True

		from .sync_account import create_account

		create_account(qb_account, quickbooks_account_list=[], qb_company=company)

		# Retry strict lookup now that the account should exist
		# IMPORTANT: When strict_qb_id_only=True, don't pass account_type/root_type filters
		# The QB ID is unique and sufficient - account_type may not match if account was created with different type
		retry_account_type = None if strict_qb_id_only else account_type
		retry_root_type = None if strict_qb_id_only else root_type

		account = get_account_from_quickbooks_ref(
			qb_account_ref=qb_account_ref,
			company=company,
			account_type=retry_account_type,
			root_type=retry_root_type,
			fallback_account=fallback_account,
			module_name=module_name,
			strict_qb_id_only=strict_qb_id_only,
		)
		if account:
			# Re-verify leaf status for newly created/retried account
			is_group = frappe.db.get_value("Account", account, "is_group") == 1
			if is_group:
				leaf = get_leaf_account_from_group(
					account, company=company, account_type=account_type, module_name=module_name
				)
				if leaf:
					account = leaf

		# Update cache if we found/created an account
		if account and cache and qb_account_id:
			cache.accounts_by_qb_id[qb_account_id] = account

		if account and module_name:
			_dbg(
				module_name,
				"ensure_account_from_qb_ref:created_and_mapped",
				{
					"qb_account_id": qb_account_id,
					"qb_account_name": qb_account_name,
					"company": company,
					"erpnext_account": account,
					"note": "Created missing ERPNext account from QuickBooks and re-mapped",
				},
			)
		if account:
			_enable_if_disabled(account, reason="Auto-created/used by transaction sync")
		return account or fallback_account
	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"ensure_account_from_qb_ref:error",
				{
					"qb_account_id": qb_account_id,
					"qb_account_name": qb_account_name,
					"company": company,
					"error": str(e),
					"error_type": type(e).__name__,
					"note": "Failed to auto-create account from QuickBooks",
				},
			)
		qb_log_exception(
			method="ensure_account_from_quickbooks_ref",
			err=e,
			request_data={
				"qb_account_id": qb_account_id,
				"qb_account_name": qb_account_name,
				"company": company,
			},
			module=module_name or "sync_utils",
		)
		return fallback_account


def get_leaf_account_from_group(account_name, company=None, account_type=None, module_name=None, cache=None):
	"""
	Get a leaf (non-group) account. If the account is a group account, find the first child leaf account.

	ERPNext does not allow group accounts in Journal Entries. This function ensures we always
	get a leaf account that can be used in transactions.

	Args:
	    account_name: ERPNext account name to check
	    company: Company name (optional, will be extracted from account if not provided)
	    account_type: Optional account_type filter for child account search
	    module_name: Module name for debug logging (optional)
	    cache: SyncCache object (optional)

	Returns:
	    str: Leaf account name, or None if no suitable account found
	"""
	if not account_name:
		return None

	# Check if account exists
	if not frappe.db.exists("Account", account_name):
		if module_name:
			_dbg(
				module_name,
				"get_leaf_account_from_group:not_found",
				{"account": account_name, "warning": "Account does not exist in ERPNext"},
			)
		return None

	# Get company if not provided
	is_group = None
	if cache:
		acc_metadata = cache.account_metadata.get(account_name)
		if acc_metadata:
			if not company:
				company = acc_metadata.get("company")
			is_group = acc_metadata.get("is_group")

		if is_group is None:
			# Fallback to get_account_doc which populates metadata
			acc_doc = cache.get_account_doc(account_name)
			if acc_doc:
				if not company:
					company = acc_doc.get("company")
				is_group = acc_doc.get("is_group")

	if not company and not cache:
		company = frappe.db.get_value("Account", account_name, "company")

	# Check if it's a group account
	if is_group is None:
		is_group = frappe.db.get_value("Account", account_name, "is_group")

	if not is_group:
		# Already a leaf account, return as-is
		return account_name

	# Check cache for leaf mapping
	if cache and hasattr(cache, "leaf_accounts") and account_name in cache.leaf_accounts:
		return cache.leaf_accounts[account_name]

	# It's a group account, find the first enabled child leaf account
	filters = {
		"parent_account": account_name,
		"is_group": 0,
		"disabled": 0,
	}
	if company:
		filters["company"] = company
	if account_type:
		filters["account_type"] = account_type

	child_account = frappe.db.get_value("Account", filters, "name", order_by="name ASC")

	if child_account:
		if module_name:
			_dbg(
				module_name,
				"get_leaf_account_from_group:using_child",
				{
					"group_account": account_name,
					"child_account": child_account,
					"account_type": account_type,
					"note": "Group account mapped to child leaf account for Journal Entry",
				},
			)

		if cache and hasattr(cache, "leaf_accounts"):
			cache.leaf_accounts[account_name] = child_account

		return child_account

	# Try without account_type filter if it was specified
	if account_type:
		filters_without_type = {
			"parent_account": account_name,
			"is_group": 0,
			"disabled": 0,
		}
		if company:
			filters_without_type["company"] = company

		child_account = frappe.db.get_value("Account", filters_without_type, "name", order_by="name ASC")

		if child_account:
			if module_name:
				_dbg(
					module_name,
					"get_leaf_account_from_group:using_child_no_type_filter",
					{
						"group_account": account_name,
						"child_account": child_account,
						"note": "Group account mapped to child leaf account (without account_type filter)",
					},
				)

			if cache and hasattr(cache, "leaf_accounts"):
				cache.leaf_accounts[account_name] = child_account

			return child_account

	# No suitable child account found
	if module_name:
		_dbg(
			module_name,
			"get_leaf_account_from_group:no_child_found",
			{
				"group_account": account_name,
				"company": company,
				"account_type": account_type,
				"error": "Group account has no non-group child accounts, cannot create Journal Entry",
			},
		)
	return None


def validate_account_for_transaction(
	account_name, company=None, account_type=None, module_name=None, cache=None
):
	"""
	Validate that an account can be used in transactions (not a group account).
	If it's a group account, try to find a suitable leaf account.

	This function should be used to validate ALL accounts before using them in transactions,
	especially accounts from QuickBooks Settings, Item Defaults, or Company Defaults.

	Args:
	    account_name: ERPNext account name to validate
	    company: Company name (optional, for finding leaf accounts)
	    account_type: Optional account_type filter for finding leaf accounts
	    module_name: Module name for debug logging (optional)

	Returns:
	    str: Valid leaf account name, or None if account cannot be used
	"""
	if not account_name:
		return None

	# Use get_leaf_account_from_group to handle both leaf and group accounts
	valid_account = get_leaf_account_from_group(account_name, company, account_type, module_name, cache=cache)

	if not valid_account and module_name:
		_dbg(
			module_name,
			"validate_account_for_transaction:invalid",
			{
				"original_account": account_name,
				"company": company,
				"account_type": account_type,
				"warning": "Account is a group account and has no suitable child accounts",
			},
		)

	return valid_account


def ensure_party_enabled(party_type, party_name, module_name=None):
	"""
	Ensure a customer or supplier is enabled before creating transactions.
	ERPNext blocks transactions with disabled parties, so we force enable them.

	Args:
	    party_type (str): "Customer" or "Supplier"
	    party_name (str): Name of the customer/supplier document
	    module_name (str): Module name for debug logging (optional)

	Returns:
	    bool: True if party was enabled, False if already enabled or not found
	"""
	if not party_name or not party_type:
		return False

	try:
		# Check if party exists and is disabled
		disabled = frappe.db.get_value(party_type, party_name, "disabled")
		if disabled:
			# Force enable the party
			frappe.db.set_value(party_type, party_name, "disabled", 0)
			frappe.db.commit()
			_dbg(
				module_name or "sync_utils",
				"ensure_party_enabled:enabled",
				{
					"party_type": party_type,
					"party_name": party_name,
					"note": "Force enabled disabled party to allow transaction creation",
				},
			)
			return True
		return False
	except Exception as e:
		_dbg(
			module_name or "sync_utils",
			"ensure_party_enabled:error",
			{"party_type": party_type, "party_name": party_name, "error": str(e)},
		)
		return False


def ensure_currency_exchange(
	from_currency, to_currency, exchange_date, exchange_rate, company=None, module_name=None
):
	"""
	Ensure Currency Exchange doctype entry exists for the given date and currency pair.
	If it doesn't exist, create it with the provided exchange rate.

	Args:
	    from_currency (str): Source currency code (e.g., "EUR")
	    to_currency (str): Target currency code (e.g., "BDT")
	    exchange_date (str/datetime): Date for the exchange rate
	    exchange_rate (float): Exchange rate from from_currency to to_currency
	    company (str, optional): Company name (for multi-company support)
	    module_name (str, optional): Module name for debug logging

	Returns:
	    str or None: Name of the Currency Exchange entry, or None if creation failed
	"""
	try:
		from frappe.utils import flt, getdate

		# Normalize date
		if isinstance(exchange_date, str):
			exchange_date = getdate(exchange_date)
		else:
			exchange_date = getdate(exchange_date)

		# Validate currencies
		if not from_currency or not to_currency:
			if module_name:
				_dbg(
					module_name,
					"ensure_currency_exchange:invalid_currencies",
					{
						"from_currency": from_currency,
						"to_currency": to_currency,
						"error": "Missing currency codes",
					},
				)
			return None

		# If currencies are the same, no exchange rate needed
		if from_currency == to_currency:
			if module_name:
				_dbg(
					module_name,
					"ensure_currency_exchange:same_currency",
					{"currency": from_currency, "note": "No exchange rate needed for same currency"},
				)
			return None

		# Validate exchange rate
		try:
			exchange_rate = flt(exchange_rate)
			if exchange_rate <= 0:
				if module_name:
					_dbg(
						module_name,
						"ensure_currency_exchange:invalid_rate",
						{"exchange_rate": exchange_rate, "error": "Exchange rate must be positive"},
					)
				return None
		except (ValueError, TypeError):
			if module_name:
				_dbg(
					module_name,
					"ensure_currency_exchange:rate_conversion_error",
					{"exchange_rate": exchange_rate, "error": "Could not convert exchange rate to float"},
				)
			return None

		# Check if Currency Exchange entry already exists for this date and currency pair
		# Currency Exchange doctype has: from_currency, to_currency, date, exchange_rate
		existing = frappe.db.get_value(
			"Currency Exchange",
			{"from_currency": from_currency, "to_currency": to_currency, "date": exchange_date},
			"name",
		)

		if existing:
			if module_name:
				_dbg(
					module_name,
					"ensure_currency_exchange:exists",
					{
						"from_currency": from_currency,
						"to_currency": to_currency,
						"date": str(exchange_date),
						"exchange_rate": exchange_rate,
						"existing_entry": existing,
					},
				)
			return existing

		# Create new Currency Exchange entry
		currency_exchange = frappe.new_doc("Currency Exchange")
		currency_exchange.from_currency = from_currency
		currency_exchange.to_currency = to_currency
		currency_exchange.date = exchange_date
		currency_exchange.exchange_rate = exchange_rate

		# Set company if provided
		if company:
			currency_exchange.company = company

		currency_exchange.insert(ignore_permissions=True)
		frappe.db.commit()

		if module_name:
			_dbg(
				module_name,
				"ensure_currency_exchange:created",
				{
					"from_currency": from_currency,
					"to_currency": to_currency,
					"date": str(exchange_date),
					"exchange_rate": exchange_rate,
					"company": company,
					"entry_name": currency_exchange.name,
				},
			)

		return currency_exchange.name

	except Exception as e:
		if module_name:
			_dbg(
				module_name,
				"ensure_currency_exchange:error",
				{
					"from_currency": from_currency,
					"to_currency": to_currency,
					"date": str(exchange_date) if exchange_date else None,
					"exchange_rate": exchange_rate,
					"error": str(e),
					"error_type": type(e).__name__,
				},
			)
		frappe.log_error(f"Error ensuring Currency Exchange: {e!s}", "ensure_currency_exchange")
		return None


class SyncCache:
	def __init__(self, company):
		self.company = company
		self.invoices = {}  # QB ID -> Name
		self.invoices_by_name = {}  # Name -> Doc (dict)
		self.purchase_invoices = {}  # QB ID -> Name
		self.purchase_invoices_by_name = {}  # Name -> Doc (dict)
		self.journal_entries = {}  # QB ID -> Name
		self.payment_entries = set()  # Set of QB IDs
		self.accounts_by_qb_id = {}  # QB ID -> Name
		self.accounts_by_name = {}  # Name -> Doc (dict)
		self.customers = {}  # QB ID -> Name
		self.customers_by_name = {}  # Name -> Dict (territory, disabled, default_currency)
		self.suppliers = {}  # QB ID -> Name
		self.items = {}  # QB ID -> Name
		self.addresses = {}  # QB ID -> Name
		self.modes_of_payment = {}  # QB ID -> Name
		self.customer_accounts = {}  # Customer Name -> List of Accounts
		self.undeposited_funds_accounts = []
		self.stock_accounts = None  # Lazy loaded set
		self.company_defaults = {}  # Company default accounts
		self.customer_currencies = {}  # Customer Name -> Default Currency
		self.supplier_currencies = {}  # Supplier Name -> Default Currency
		self.fallback_accounts_cache = {}  # (account_type) -> account_name
		self.party_by_name_cache = {}  # (party_type, party_name) -> erpnext_name
		self.cost_centers = {}  # QB ID -> Name
		self.payment_terms_templates = {}  # QB ID -> Name
		self.mode_of_payment_account_map = {}  # (default_account) -> Mode of Payment
		self.non_taxable_template = None
		self.amounts = {}  # Unique QB ID -> Amount (flt)

		# New High-Performance Caching
		self.fiscal_years = {}  # (company, date_str) -> fy_name
		self.currency_exchanges = {}  # (from, to, date_str) -> exchange_name
		self.party_accounts_cache = {}  # (party_type, party_name) -> account_name
		self.account_metadata = {}  # name -> {is_stock: bool, type: str, ...}
		self.fallbacks = {}  # type -> name
		self.default_tax_accounts = {}  # company -> account_name
		self.parties = {}  # (type, qb_id) -> erpnext_name
		self.party_gl_currencies = {}  # (party_type, party_name) -> list of currencies
		self.party_control_accounts = {}  # (party_type, party_name, company) -> account_name
		self.leaf_accounts = {}  # (account_name, account_type) -> leaf_name
		self.party_enabled = {}  # (party_type, party_name) -> bool
		self.item_uoms = {}  # item_code -> stock_uom
		self.uoms = {}  # uom -> must_be_whole_number
		self.item_defaults = {}  # (item_code, company) -> dict
		self.accounts_settings = None  # Lazy loaded
		self.default_company = None  # Lazy loaded
		self.company_abbr = None
		self.quickbooks_settings = None

		# Prefetch status
		self.prefetched_phases = set()

		self.load_data()

	def get_quickbooks_settings(self):
		"""Lazy load Quickbooks Settings"""
		if not self.quickbooks_settings:
			self.quickbooks_settings = frappe.get_doc("Quickbooks Settings", "Quickbooks Settings")
		return self.quickbooks_settings

	def prefetch_journal_entries(self, qb_je_ids):
		"""Batch load Journal Entry names for a list of QB IDs"""
		if not qb_je_ids:
			return

		# Filter out already cached IDs
		missing_ids = [str(id) for id in qb_je_ids if id not in self.journal_entries]
		if not missing_ids:
			return

		# Fetch in one query
		found = frappe.get_all(
			"Journal Entry",
			filters={"quickbooks_journal_entry_id": ["in", missing_ids]},
			fields=["name", "quickbooks_journal_entry_id"],
		)

		for entry in found:
			self.journal_entries[entry.quickbooks_journal_entry_id] = entry.name

		# Mark remaining as None to avoid re-querying
		for qb_id in missing_ids:
			if qb_id not in self.journal_entries:
				self.journal_entries[qb_id] = None

	def prefetch_accounts_by_qb_ids(self, qb_acc_ids):
		"""Batch load Account details for a list of QB Account IDs"""
		if not qb_acc_ids:
			return

		missing_ids = [str(id) for id in qb_acc_ids if id not in self.accounts_by_qb_id]
		if not missing_ids:
			return

		found = frappe.get_all(
			"Account",
			filters={"quickbooks_account_id": ["in", missing_ids], "company": self.company},
			fields=[
				"name",
				"quickbooks_account_id",
				"account_currency",
				"account_type",
				"account_name",
				"is_group",
				"parent_account",
				"company",
			],
		)

		for acc in found:
			self.accounts_by_qb_id[acc.quickbooks_account_id] = acc.name
			self.accounts_by_name[acc.name] = acc

			# Pre-calculate metadata
			acc_name_lower = str(acc.name).lower()
			disp_name_lower = str(acc.account_name).lower()
			is_stock = (
				acc.account_type in ["Stock", "Stock Adjustment"]
				or "inventory" in acc_name_lower
				or "inventory" in disp_name_lower
				or ("stock" in acc_name_lower and "stock adjustment" not in acc_name_lower)
				or ("stock" in disp_name_lower and "stock adjustment" not in disp_name_lower)
			)
			self.account_metadata[acc.name] = {
				"is_stock": is_stock,
				"account_type": acc.account_type,
				"account_name": acc.account_name,
				"is_group": acc.is_group,
			}

		# Mark remaining as None
		for qb_id in missing_ids:
			if qb_id not in self.accounts_by_qb_id:
				self.accounts_by_qb_id[qb_id] = None

	def get_fiscal_year_cached(self, posting_date):
		"""Cached logic for ensure_fiscal_year_for_date"""
		if not posting_date:
			return None
		date_str = str(posting_date)
		key = (self.company, date_str)

		if key not in self.fiscal_years:
			self.fiscal_years[key] = ensure_fiscal_year_for_date(self.company, posting_date)
		return self.fiscal_years[key]

	def get_currency_exchange_cached(self, from_curr, to_curr, date, rate):
		"""Cached logic for ensure_currency_exchange"""
		if not from_curr or not to_curr or from_curr == to_curr:
			return None
		date_str = str(date)
		key = (from_curr, to_curr, date_str)

		if key not in self.currency_exchanges:
			self.currency_exchanges[key] = ensure_currency_exchange(
				from_currency=from_curr,
				to_currency=to_curr,
				exchange_date=date,
				exchange_rate=rate,
				company=self.company,
			)
		return self.currency_exchanges[key]

	def get_account_meta(self, account_name):
		"""Get pre-calculated metadata for an account"""
		if account_name not in self.account_metadata:
			# Lazy load single account metadata
			acc = frappe.db.get_value(
				"Account", account_name, ["name", "account_name", "account_type", "is_group"], as_dict=True
			)
			if acc:
				n_lower, d_lower = str(acc.name).lower(), str(acc.account_name).lower()
				is_stock = (
					acc.account_type in ["Stock", "Stock Adjustment"]
					or "inventory" in n_lower
					or "inventory" in d_lower
					or ("stock" in n_lower and "stock adjustment" not in n_lower)
					or ("stock" in d_lower and "stock adjustment" not in d_lower)
				)
				self.account_metadata[account_name] = {
					"is_stock": is_stock,
					"account_type": acc.account_type,
					"account_name": acc.account_name,
					"is_group": acc.is_group,
				}
			else:
				return {}
		return self.account_metadata[account_name]

	def get_party_name_cached(self, party_type, qb_id):
		"""Cached party lookup (Customer/Supplier)"""
		if not party_type or not qb_id:
			return None
		key = (party_type, qb_id)
		if key in self.parties:
			return self.parties[key]

		# Unique ID resolve
		abbr = self.get_company_abbr()
		unique_id = f"{abbr}-{qb_id}" if abbr else qb_id

		erp_name = None
		if party_type == "Customer":
			erp_name = frappe.db.get_value("Customer", {"quickbooks_cust_id": unique_id}, "name")
		elif party_type == "Supplier":
			erp_name = frappe.db.get_value("Supplier", {"quickbooks_supp_id": unique_id}, "name")
		elif party_type == "Employee":
			erp_name = frappe.db.get_value(
				"Employee", {"quickbooks_emp_id": unique_id, "company": self.company}, "name"
			)

		self.parties[key] = erp_name
		return erp_name

	def prefetch_parties(self, party_type, qb_ids):
		"""Batch load party names for a list of QB IDs"""
		if not party_type or not qb_ids:
			return

		missing_ids = []
		abbr = self.get_company_abbr()

		for qb_id in qb_ids:
			key = (party_type, qb_id)
			if key not in self.parties:
				unique_id = f"{abbr}-{qb_id}" if abbr else qb_id
				missing_ids.append(unique_id)

		if not missing_ids:
			return

		id_field = (
			"quickbooks_cust_id"
			if party_type == "Customer"
			else "quickbooks_supp_id"
			if party_type == "Supplier"
			else "quickbooks_emp_id"
		)

		found = frappe.get_all(
			party_type, filters={id_field: ["in", missing_ids]}, fields=["name", id_field, "default_currency"]
		)

		for p in found:
			uid = p.get(id_field)
			# Reverse map unique ID to QB ID
			original_qb_id = uid.replace(f"{abbr}-", "") if abbr else uid
			self.parties[(party_type, original_qb_id)] = p.name

			if p.get("default_currency"):
				if party_type == "Customer":
					self.customer_currencies[p.name] = p.default_currency
				elif party_type == "Supplier":
					self.supplier_currencies[p.name] = p.default_currency

		# Mark remaining as None
		for qb_id in qb_ids:
			key = (party_type, qb_id)
			if key not in self.parties:
				self.parties[key] = None

	def prefetch_gl_currencies(self, parties):
		"""Batch load existing GL Entry currencies for a list of (party_type, party_name)"""
		if not parties:
			return

		# Deduplicate and filter out already cached
		missing_parties = []
		for p_type, p_name in parties:
			if not p_name:
				continue
			if (p_type, p_name) not in self.party_gl_currencies:
				missing_parties.append((p_type, p_name))

		if not missing_parties:
			return

		# Optimization: group by party_type for fewer queries
		by_type = {}
		for p_type, p_name in missing_parties:
			if p_type not in by_type:
				by_type[p_type] = []
			by_type[p_type].append(p_name)

		for p_type, p_names in by_type.items():
			# Query GL Entry for distinct currencies per party
			data = frappe.db.sql(
				"""
                SELECT party, account_currency
                FROM `tabGL Entry`
                WHERE party_type = %s
                AND party IN %s
                AND company = %s
                AND account_currency IS NOT NULL
                AND account_currency != ''
            """,
				(p_type, p_names, self.company),
				as_dict=True,
			)

			# Group by party
			temp = {}
			for row in data:
				p_name = row.party
				if p_name not in temp:
					temp[p_name] = []
				if row.account_currency not in temp[p_name]:
					temp[p_name].append(row.account_currency)

			# Update cache
			for p_name in p_names:
				self.party_gl_currencies[(p_type, p_name)] = temp.get(p_name, [])

	def prefetch_party_control_accounts(self, parties):
		"""Batch load party-specific accounts (control accounts) for a list of (party_type, party_name)"""
		if not parties:
			return

		missing = []
		for p_type, p_name in parties:
			if not p_name:
				continue
			if (p_type, p_name, self.company) not in self.party_control_accounts:
				missing.append((p_type, p_name))

		if not missing:
			return

		# Query Party Account table
		for p_type, p_name in missing:
			acc = frappe.db.get_value(
				"Party Account", {"parenttype": p_type, "parent": p_name, "company": self.company}, "account"
			)
			self.party_control_accounts[(p_type, p_name, self.company)] = acc

	def get_party_control_account(self, party_type, party_name):
		"""Get cached party control account"""
		if not party_name:
			return None
		key = (party_type, party_name, self.company)
		if key not in self.party_control_accounts:
			self.prefetch_party_control_accounts([(party_type, party_name)])
		return self.party_control_accounts.get(key)

	def get_party_gl_currencies(self, party_type, party_name):
		"""Get cached GL currencies for a party"""
		if not party_name:
			return []
		key = (party_type, party_name)
		if key not in self.party_gl_currencies:
			# Lazy load single
			self.prefetch_gl_currencies([(party_type, party_name)])
		return self.party_gl_currencies.get(key, [])

	def get_leaf_account_cached(self, account_name, account_type=None):
		"""Cached logic for get_leaf_account_from_group"""
		key = (account_name, account_type)
		if key not in self.leaf_accounts:
			self.leaf_accounts[key] = get_leaf_account_from_group(
				account_name, company=self.company, account_type=account_type
			)
		return self.leaf_accounts[key]

	def ensure_party_enabled_cached(self, party_type, party_name):
		"""Cached logic for ensure_party_enabled"""
		if not party_name or not party_type:
			return False

		key = (party_type, party_name)
		if self.party_enabled.get(key) is True:
			return False  # Already ensured

		result = ensure_party_enabled(party_type, party_name, module_name="sync_utils")

		# Mark as enabled regardless of whether we had to enable it or it was already enabled
		# since ensure_party_enabled guarantees it's enabled after calling
		self.party_enabled[key] = True
		return result

	def get_company_abbr(self):
		"""Get cached company abbreviation"""
		if not self.company_abbr and self.company:
			self.company_abbr = frappe.db.get_value("Company", self.company, "abbr")
		return self.company_abbr

	def prefetch_items(self):
		"""Pre-load all item names and stock UOMs for the company"""
		if "items" in self.prefetched_phases:
			return

		abbr = self.get_company_abbr()
		# Find items with QB ID for this company (via abbr prefix)
		pattern = f"{abbr}-%" if abbr else "%"

		items = frappe.get_all(
			"Item",
			filters={"quickbooks_item_id": ["like", pattern], "disabled": 0},
			fields=["name", "quickbooks_item_id", "stock_uom"],
		)

		for item in items:
			uid = item.quickbooks_item_id
			qb_id = uid.replace(f"{abbr}-", "") if abbr else uid
			self.items[qb_id] = item.name
			self.item_uoms[item.name] = item.stock_uom

		self.prefetched_phases.add("items")

	def prefetch_for_invoice_sync(self):
		"""Warm up cache for Sales/Purchase Invoice sync"""
		if "invoices" in self.prefetched_phases:
			return

		_dbg("sync_utils", "SyncCache:prefetch_for_invoice_sync:start")

		# 1. Items (essential for lines)
		self.prefetch_items()

		# 2. Often used accounts
		self.get_fallback_account("Receivable")
		self.get_fallback_account("Payable")
		self.get_fallback_account("Stock")

		# 3. All non-taxable templates
		self.non_taxable_template = frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": self.company}, "name"
		)

		self.prefetched_phases.add("invoices")
		_dbg("sync_utils", "SyncCache:prefetch_for_invoice_sync:done")

	def prefetch_for_payment_sync(self):
		"""Warm up cache for Payments/Deposits sync"""
		if "payments" in self.prefetched_phases:
			return

		_dbg("sync_utils", "SyncCache:prefetch_for_payment_sync:start")

		# 1. Accounts (DepositTo and Line accounts)
		# We don't prefetch all accounts, but we ensure group-to-leaf mapping is warmed up
		self.get_fallback_account("Receivable")
		self.get_fallback_account("Payable")

		# 2. Modes of Payment
		# Prefetch all Modes of Payment for the company
		mops = frappe.get_all("Mode of Payment", fields=["name"])
		for _mop in mops:
			# We don't have a direct QB ID mapping here usually, but we warm up the name cache
			pass

		self.prefetched_phases.add("payments")
		_dbg("sync_utils", "SyncCache:prefetch_for_payment_sync:done")

	def get_amount(self, unique_id):
		return self.amounts.get(unique_id)

	def set_amount(self, unique_id, amount):
		if unique_id:
			self.amounts[unique_id] = flt(amount)

	def load_data(self):
		"""Pre-load ONLY essential company defaults. Other data will be lazy-loaded on demand."""
		# Load Company Defaults (essential for nearly all sync operations)
		self.company_defaults = (
			frappe.db.get_value(
				"Company",
				self.company,
				[
					"default_receivable_account",
					"default_payable_account",
					"default_income_account",
					"default_expense_account",
					"default_currency",
				],
				as_dict=True,
			)
			or {}
		)

		# Initialize internal defaults with values from company_defaults
		self.defaults = {
			"default_receivable_account": self.company_defaults.get("default_receivable_account"),
			"default_payable_account": self.company_defaults.get("default_payable_account"),
			"default_currency": self.company_defaults.get("default_currency"),
			"default_income_account": self.company_defaults.get("default_income_account"),
			"default_expense_account": self.company_defaults.get("default_expense_account"),
		}

	def get_invoice(self, qb_invoice_id):
		"""Lazy load Sales Invoice by QB ID"""
		if qb_invoice_id not in self.invoices:
			inv = frappe.db.get_value(
				"Sales Invoice",
				{"quickbooks_invoce_id": qb_invoice_id},
				[
					"name",
					"quickbooks_invoce_id",
					"docstatus",
					"outstanding_amount",
					"currency",
					"customer",
					"debit_to",
				],
				as_dict=True,
			)
			self.invoices[qb_invoice_id] = inv
			if inv:
				self.invoices_by_name[inv.name] = inv
		return self.invoices.get(qb_invoice_id)

	def get_query_invoice_by_name(self, name):
		"""Lazy load Sales Invoice by ERPNext Name"""
		if name not in self.invoices_by_name:
			inv = frappe.db.get_value(
				"Sales Invoice",
				{"name": name},
				[
					"name",
					"quickbooks_invoce_id",
					"docstatus",
					"outstanding_amount",
					"currency",
					"customer",
					"debit_to",
				],
				as_dict=True,
			)
			self.invoices_by_name[name] = inv
			if inv and inv.get("quickbooks_invoce_id"):
				self.invoices[inv.quickbooks_invoce_id] = inv
		return self.invoices_by_name.get(name)

	def prefetch_invoices(self, qb_invoice_ids):
		"""Batch load Sales Invoices for a list of unique QB IDs"""
		if not qb_invoice_ids:
			return

		missing_ids = [str(id) for id in qb_invoice_ids if id not in self.invoices]
		if not missing_ids:
			return

		found = frappe.get_all(
			"Sales Invoice",
			filters={"quickbooks_invoce_id": ["in", missing_ids]},
			fields=[
				"name",
				"quickbooks_invoce_id",
				"docstatus",
				"outstanding_amount",
				"currency",
				"customer",
				"debit_to",
			],
		)

		for inv in found:
			self.invoices[inv.quickbooks_invoce_id] = inv
			self.invoices_by_name[inv.name] = inv

		for qb_id in missing_ids:
			if qb_id not in self.invoices:
				self.invoices[qb_id] = None

	def add_invoice(self, unique_qb_id, name):
		"""Add a newly created Sales Invoice to the cache"""
		if unique_qb_id and name:
			inv = {"name": name, "quickbooks_invoce_id": unique_qb_id}
			self.invoices[unique_qb_id] = inv
			self.invoices_by_name[name] = inv

	def get_purchase_invoice(self, qb_pi_id):
		"""Lazy load Purchase Invoice by QB ID"""
		if qb_pi_id not in self.purchase_invoices:
			pi = frappe.db.get_value(
				"Purchase Invoice",
				{"quickbooks_purchase_invoice_id": qb_pi_id},
				["name", "quickbooks_purchase_invoice_id", "docstatus", "outstanding_amount", "currency"],
				as_dict=True,
			)
			self.purchase_invoices[qb_pi_id] = pi
			if pi:
				self.purchase_invoices_by_name[pi.name] = pi
		return self.purchase_invoices.get(qb_pi_id)

	def get_purchase_invoice_doc(self, qb_pi_id):
		"""Alias for get_purchase_invoice"""
		return self.get_purchase_invoice(qb_pi_id)

	def get_purchase_invoice_by_name(self, name):
		"""Lazy load Purchase Invoice by ERPNext Name"""
		if name not in self.purchase_invoices_by_name:
			pi = frappe.db.get_value(
				"Purchase Invoice",
				{"name": name},
				["name", "quickbooks_purchase_invoice_id", "docstatus", "outstanding_amount", "currency"],
				as_dict=True,
			)
			self.purchase_invoices_by_name[name] = pi
			if pi and pi.get("quickbooks_purchase_invoice_id"):
				self.purchase_invoices[pi.get("quickbooks_purchase_invoice_id")] = pi
		return self.purchase_invoices_by_name.get(name)

	def get_journal_entry(self, qb_je_id):
		"""Lazy load Journal Entry name by QB ID"""
		if qb_je_id not in self.journal_entries:
			name = frappe.db.get_value("Journal Entry", {"quickbooks_journal_entry_id": qb_je_id}, "name")
			self.journal_entries[qb_je_id] = name
		return self.journal_entries.get(qb_je_id)

	def get_account_by_qb_id(self, qb_id):
		"""Lazy load Account name by QB ID"""
		if qb_id not in self.accounts_by_qb_id:
			name = frappe.db.get_value(
				"Account", {"quickbooks_account_id": qb_id, "company": self.company}, "name"
			)
			self.accounts_by_qb_id[qb_id] = name
		return self.accounts_by_qb_id.get(qb_id)

	def get_account_doc(self, name):
		"""Lazy load Account details by name"""
		if name not in self.accounts_by_name:
			acc = frappe.db.get_value(
				"Account",
				{"name": name, "company": self.company},
				[
					"name",
					"quickbooks_account_id",
					"account_currency",
					"account_type",
					"account_name",
					"is_group",
					"parent_account",
					"company",
				],
				as_dict=True,
			)
			self.accounts_by_name[name] = acc
			if acc and acc.get("quickbooks_account_id"):
				self.accounts_by_qb_id[acc.quickbooks_account_id] = name
		return self.accounts_by_name.get(name)

	def entry_exists(self, qb_je_id):
		"""Check if Journal Entry or Payment Entry exists for QB ID"""
		if self.get_journal_entry(qb_je_id):
			return True

		# Check Payment Entry (separate set for efficiency, but maybe lazy check is enough)
		# Note: We don't have a get_payment_entry, so we'll check DB and cache locally
		if qb_je_id in self.payment_entries:
			return True

		exists = frappe.db.exists("Payment Entry", {"quickbooks_journal_entry_id": qb_je_id})
		if exists:
			self.payment_entries.add(qb_je_id)
			return True
		return False

	def get_mode_of_payment(self, key):
		"""Lazy load Mode of Payment"""
		str_key = str(key)
		if str_key not in self.modes_of_payment:
			# Try by QB ID first
			mop = frappe.db.get_value("Mode of Payment", {"quickbooks_payment_method_id": str_key}, "name")
			if not mop:
				# Try by Name
				mop = frappe.db.get_value("Mode of Payment", {"mode_of_payment": key}, "name")

			self.modes_of_payment[str_key] = mop
		return self.modes_of_payment.get(str_key)

	def get_customer(self, qb_cust_id):
		"""Lazy load Customer by QB ID"""
		if qb_cust_id not in self.customers:
			name = frappe.db.get_value("Customer", {"quickbooks_cust_id": qb_cust_id}, "name")
			self.customers[qb_cust_id] = name
		return self.customers.get(qb_cust_id)

	def get_customer_info(self, name):
		"""Lazy load Customer info (territory, disabled, default_currency)"""
		if name not in self.customers_by_name:
			cust = frappe.db.get_value(
				"Customer",
				{"name": name},
				["name", "quickbooks_cust_id", "default_currency", "territory", "disabled"],
				as_dict=True,
			)
			self.customers_by_name[name] = cust
			if cust and cust.get("default_currency"):
				self.customer_currencies[name] = cust.get("default_currency")
		return self.customers_by_name.get(name)

	def get_supplier(self, qb_supp_id):
		"""Lazy load Supplier by QB ID"""
		if qb_supp_id not in self.suppliers:
			supp = frappe.db.get_value(
				"Supplier", {"quickbooks_supp_id": qb_supp_id}, ["name", "default_currency"], as_dict=True
			)
			if supp:
				self.suppliers[qb_supp_id] = supp.name
				if supp.get("default_currency"):
					self.supplier_currencies[supp.name] = supp.get("default_currency")
			else:
				self.suppliers[qb_supp_id] = None
		return self.suppliers.get(qb_supp_id)

	def get_item(self, qb_item_id):
		"""Lazy load Item by QB ID"""
		if qb_item_id not in self.items:
			name = frappe.db.get_value("Item", {"quickbooks_item_id": qb_item_id}, "name")
			self.items[qb_item_id] = name
		return self.items.get(qb_item_id)

	def get_address(self, qb_addr_id):
		"""Lazy load Address by QB ID"""
		if qb_addr_id not in self.addresses:
			name = frappe.db.get_value("Address", {"quickbooks_address_id": qb_addr_id}, "name")
			self.addresses[qb_addr_id] = name
		return self.addresses.get(qb_addr_id)

	def get_default_tax_account(self, company):
		"""Lazy load default Tax account for a company"""
		if not company:
			return None
		if company not in self.default_tax_accounts:
			self.default_tax_accounts[company] = frappe.db.get_value(
				"Account",
				{"company": company, "account_type": "Tax", "is_group": 0},
				"name",
				order_by="creation asc",
			)
		return self.default_tax_accounts.get(company)

	def get_item_uom_cached(self, item_code):
		"""Lazy load item UOM"""
		if not item_code:
			return None
		if item_code not in self.item_uoms:
			self.item_uoms[item_code] = frappe.db.get_value("Item", item_code, "stock_uom")
		return self.item_uoms.get(item_code)

	def get_uom_info_cached(self, uom):
		"""Lazy load UOM whole number info"""
		if not uom:
			return None
		if uom not in self.uoms:
			self.uoms[uom] = frappe.db.get_value("UOM", uom, "must_be_whole_number")
		return self.uoms.get(uom)

	def get_item_defaults_cached(self, item_code, company):
		"""Lazy load item defaults"""
		key = (item_code, company)
		if key not in self.item_defaults:
			from erpnext.stock.get_item_details import get_item_defaults

			self.item_defaults[key] = get_item_defaults(item_code, company)
		return self.item_defaults.get(key)

	def get_accounts_settings_cached(self):
		"""Lazy load Accounts Settings"""
		if self.accounts_settings is None:
			self.accounts_settings = frappe.get_single("Accounts Settings")
		return self.accounts_settings

	def get_default_company_cached(self):
		"""Lazy load default company"""
		if self.default_company is None:
			self.default_company = frappe.db.get_single_value("Global Defaults", "default_company")
		return self.default_company

	def get_stock_accounts(self):
		"""Lazy load and cache the set of stock accounts for the company"""
		if self.stock_accounts is None:
			try:
				from erpnext.accounts.utils import get_stock_accounts

				self.stock_accounts = set(get_stock_accounts(self.company))
			except (ImportError, AttributeError, Exception):
				self.stock_accounts = set()
		return self.stock_accounts

	def get_company_default(self, field_name):
		"""Get company default value (e.g., 'default_receivable_account')"""
		return self.company_defaults.get(field_name)

	def get_customer_currency(self, customer_name):
		"""Get customer's default currency"""
		return self.customer_currencies.get(customer_name)

	def get_supplier_currency(self, supplier_name):
		"""Get supplier's default currency"""
		return self.supplier_currencies.get(supplier_name)

	def get_fallback_account(self, account_type):
		"""Get cached fallback account for special account types"""
		if account_type in self.fallback_accounts_cache:
			return self.fallback_accounts_cache[account_type]

		# If not cached, find it via database query
		fallback_account = None

		if account_type == "Receivable":
			# Use Current Asset account (not Receivable, as it requires party)
			fallback_account = frappe.db.get_value(
				"Account",
				{"company": self.company, "account_type": "Current Asset", "is_group": 0},
				"name",
				order_by="name",
			)

		elif account_type == "Payable":
			# Use Current Liability account
			fallback_account = frappe.db.get_value(
				"Account",
				{"company": self.company, "account_type": "Current Liability", "is_group": 0},
				"name",
				order_by="name",
			)

		elif account_type in ["Stock", "Stock Adjustment"]:
			# Try Current Asset first
			fallback_account = frappe.db.get_value(
				"Account",
				{"company": self.company, "account_type": "Current Asset", "is_group": 0},
				"name",
				order_by="name",
			)
			if not fallback_account:
				# Fallback to Expense Account
				fallback_account = frappe.db.get_value(
					"Account",
					{"company": self.company, "account_type": "Expense Account", "is_group": 0},
					"name",
					order_by="name",
				)

		# Cache the result (even if None, to avoid re-querying)
		self.fallback_accounts_cache[account_type] = fallback_account
		return fallback_account

	def get_customer_by_name(self, customer_name):
		"""Get customer name by searching name field (cached)"""
		cache_key = ("Customer", customer_name)
		if cache_key in self.party_by_name_cache:
			return self.party_by_name_cache[cache_key]

		party_name = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
		self.party_by_name_cache[cache_key] = party_name
		return party_name

	def get_supplier_by_name(self, supplier_name):
		"""Get supplier name by searching name field (cached)"""
		cache_key = ("Supplier", supplier_name)
		if cache_key in self.party_by_name_cache:
			return self.party_by_name_cache[cache_key]

		party_name = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
		self.party_by_name_cache[cache_key] = party_name
		return party_name

	def get_mode_of_payment_from_account(self, account):
		"""Get Mode of Payment linked to a specific bank account (cached)"""
		if account in self.mode_of_payment_account_map:
			return self.mode_of_payment_account_map[account]

		# Look up in DB
		mop = frappe.db.sql(
			"""
            SELECT parent FROM `tabMode of Payment Account`
            WHERE default_account = %s AND company = %s
        """,
			(account, self.company),
			as_dict=True,
		)

		result = mop[0].parent if mop else None
		self.mode_of_payment_account_map[account] = result
		return result

	def get_non_taxable_template(self):
		"""Get or create non-taxable template (cached)"""
		if self.non_taxable_template:
			return self.non_taxable_template

		# Call global helper but cache result
		self.non_taxable_template = get_or_create_non_taxable_template(self.company)
		return self.non_taxable_template

	def get_item_code(self, qb_item_id):
		"""Get Item Code by QB ID (using cached mapping)"""
		return self.items.get(qb_item_id)


def get_cost_center_from_quickbooks_ref(qb_class_ref, company=None, module_name=None, cache=None):
	"""
	Get Cost Center from QuickBooks Class reference.

	Args:
	    qb_class_ref: QuickBooks ClassRef dict
	    company: Company name
	    module_name: Module name for logging

	Returns:
	    str: Cost Center name or None
	"""
	if not qb_class_ref or not isinstance(qb_class_ref, dict):
		return None

	class_id = qb_class_ref.get("value")
	if not class_id:
		return None

	# Get unique ID

	unique_class_id = make_unique_qb_id(class_id, company)

	cost_center = None
	if cache and hasattr(cache, "cost_centers"):
		cost_center = cache.cost_centers.get(unique_class_id)

	if not cost_center:
		cost_center = frappe.db.get_value("Cost Center", {"quickbooks_class_id": unique_class_id}, "name")
		# Update cache if found (though SyncCache is usually read-once at start,
		# this helps if we created it mid-sync and want to reuse)
		if cost_center and cache and hasattr(cache, "cost_centers"):
			cache.cost_centers[unique_class_id] = cost_center

	if module_name and cost_center:
		_dbg(module_name, "get_cost_center:found", {"class_id": class_id, "cost_center": cost_center})

	return cost_center


def get_payment_terms_template_from_quickbooks_ref(qb_term_ref, company=None, module_name=None, cache=None):
	"""
	Get Payment Terms Template from QuickBooks Term reference.

	Args:
	    qb_term_ref: QuickBooks SalesTermRef dict
	    company: Company name
	    module_name: Module name for logging

	Returns:
	    str: Payment Terms Template name or None
	"""
	if not qb_term_ref or not isinstance(qb_term_ref, dict):
		return None

	term_id = qb_term_ref.get("value")
	if not term_id:
		return None

	# Get unique ID

	unique_term_id = make_unique_qb_id(term_id, company)

	template = None
	if cache and hasattr(cache, "payment_terms_templates"):
		template = cache.payment_terms_templates.get(unique_term_id)

	if not template:
		template = frappe.db.get_value(
			"Payment Terms Template", {"quickbooks_term_id": unique_term_id}, "name"
		)
		if template and cache and hasattr(cache, "payment_terms_templates"):
			cache.payment_terms_templates[unique_term_id] = template

	if module_name and template:
		_dbg(module_name, "get_payment_terms_template:found", {"term_id": term_id, "template": template})

	return template


def get_item_uom(item_code, qty, cache=None):
	"""Get appropriate UOM for the item based on quantity

	Returns the item"s stock UOM
	"""
	if not item_code:
		return "Nos"

	if cache and hasattr(cache, "get_item_uom_cached"):
		stock_uom = cache.get_item_uom_cached(item_code)
	else:
		# Get item"s stock UOM
		stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")

	if not stock_uom:
		return "Nos"

	return stock_uom


def adjust_qty_and_rate_for_uom(qty, rate, item_code, uom, module_name=None, cache=None):
	"""Adjust quantity and rate if UOM requires whole numbers

	If the UOM requires whole numbers but qty is fractional,
	automatically uncheck the must_be_whole_number flag to allow fractional quantities.
	This preserves exact QuickBooks Qty and UnitPrice values.

	Returns: (adjusted_qty, adjusted_rate)
	"""
	# Check if UOM requires whole numbers
	if cache and hasattr(cache, "get_uom_info_cached"):
		must_be_whole = cache.get_uom_info_cached(uom)
	else:
		must_be_whole = frappe.db.get_value("UOM", uom, "must_be_whole_number")

	if must_be_whole and qty != int(qty):
		# Instead of recalculating, uncheck the must_be_whole_number flag
		# This allows fractional quantities from QuickBooks to be used as-is
		try:
			uom_doc = frappe.get_doc("UOM", uom)
			uom_doc.must_be_whole_number = 0
			uom_doc.save(ignore_permissions=True)
			frappe.db.commit()

			if module_name:
				from quickbooks_master_sync.quickbooks_master_sync.utils.logging import _dbg

				_dbg(
					module_name,
					"adjust_qty_and_rate_for_uom:uom_fixed",
					{
						"item_code": item_code,
						"uom": uom,
						"qty": qty,
						"rate": rate,
						"note": "Unchecked 'must_be_whole_number' to allow fractional qty from QuickBooks",
					},
				)
		except Exception as e:
			if module_name:
				from quickbooks_master_sync.quickbooks_master_sync.utils.logging import _dbg

				_dbg(
					module_name,
					"adjust_qty_and_rate_for_uom:uom_fix_failed",
					{"item_code": item_code, "uom": uom, "error": str(e)},
				)

	return (qty, rate)


def get_uom_conversion_factor(item_code, uom):
	"""Get conversion factor for item UOM

	Returns:
	    float: Conversion factor (defaults to 1.0)
	"""
	if not item_code or not uom:
		return 1.0

	# Check if UOM is Stock UOM
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	if stock_uom == uom:
		return 1.0

	# Get conversion factor from Item UOMs
	conversion_factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)

	return flt(conversion_factor) or 1.0


def get_or_create_non_taxable_template(company, module_name=None):
	"""
	Get or create a Non Taxable Item Tax Template for the company.
	If 'Non Taxable - {company_abbr}' exists, use it.
	Otherwise, check for a global 'Non Taxable' template.
	If neither exists, create one with 0% tax.

	Args:
	    company (str): Company name
	    module_name (str, optional): Module name for debug logging

	Returns:
	    str: Name of the Item Tax Template, or None on error
	"""
	if not company:
		return None

	company_abbr = frappe.db.get_value("Company", {"name": company}, "abbr")
	if not company_abbr:
		return None

	template_name = f"Non Taxable - {company_abbr}"

	# Check if company-specific template exists
	if frappe.db.exists("Item Tax Template", template_name):
		return template_name

	# Check for generic "Non Taxable" template
	if frappe.db.exists("Item Tax Template", "Non Taxable"):
		return "Non Taxable"

	# Create company-specific template
	try:
		t = frappe.new_doc("Item Tax Template")
		t.title = template_name
		# Create with 0% tax logic if needed, but for now just create the template
		# The template itself handles tax override logic in ERPNext

		t.insert(ignore_permissions=True)
		if module_name:
			_dbg(module_name, "get_or_create_non_taxable_template:created", {"name": t.name})
		return t.name
	except Exception as e:
		if module_name:
			_dbg(module_name, "get_or_create_non_taxable_template:error", {"error": str(e)})
		return None
