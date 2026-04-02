import json

import frappe
from frappe import _

from quickbooks_master_sync.pyqb.quickbooks.batch import batch_create
from quickbooks_master_sync.pyqb.quickbooks.objects.customer import Customer
from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_exception

from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import (
	_resolve_country_name,
	get_account_from_quickbooks_ref,
	query_with_pagination,
	reset_company_cache,
)


def _dbg(event, payload=None):
	"""Debug logging helper for customer sync"""
	_dbg_common("sync_customers", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None):
	"""Wrapper for sync_customers module"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		use_cache=True,
		module_name="sync_customers",
	)


def _get_default_for_customer_field(fieldname):
	"""Return a sensible default for mandatory custom fields on Customer."""
	try:
		meta = frappe.get_meta("Customer")
		df = meta.get_field(fieldname)
		if not df:
			return None
		fieldtype = df.fieldtype
		options = (df.options or "").strip()
		if fieldtype == "Link":
			if options == "Company":
				# Use session default company
				company = (
					frappe.defaults.get_user_default("company")
					or frappe.db.get_single_value("Global Defaults", "default_company")
					or frappe.db.get_value("Company", {}, "name")
				)
				return company
			if options == "User":
				return getattr(frappe.session, "user", None) or "Administrator"
			if options == "Employee":
				emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
				return emp or None
			first = frappe.db.get_value(options, {}, "name") if options else None
			return first
		if fieldtype in ("Date", "Datetime"):
			return frappe.utils.nowdate()
		return df.default or ""
	except Exception:
		return None


def sync_customers(quickbooks_obj):
	"""Fetch Customer data from QuickBooks"""
	# Reset cache at start of sync
	reset_company_cache()

	_dbg("sync_customers:start", {"timestamp": frappe.utils.now()})
	# Get QuickBooks synced company once
	qb_company = _get_quickbooks_company(quickbooks_obj)
	if qb_company:
		_dbg("sync_customers:qb_company", {"company": qb_company})
	else:
		_dbg("sync_customers:no_company", {"warning": "No QuickBooks company found"})

	quickbooks_customer_list = []
	stats = {
		"created": 0,
		"updated": 0,
		"skipped": 0,
		"errors": 0,
		"failed_customers": [],
	}
	customer_query = """SELECT * FROM  Customer"""
	# Use pagination helper to fetch all customers
	get_qb_customer = query_with_pagination(
		quickbooks_obj,
		customer_query,
		"Customer",
		module_name="sync_customers",
		data_name="customers_list",
	)
	_dbg("sync_customers:fetched_all", {"count": len(get_qb_customer)})

	# NOTE: query_with_pagination() already saved the full QuickBooks response
	# to qb_BISB_customers_list_full_response.json. No need to save again.

	# Initialize generic Import Tracker
	tracker = ImportTracker(
		len(get_qb_customer),
		"qb_customer_import.log",
		module_name="CUSTOMER",
		company=qb_company,
	)

	sync_qb_customers(get_qb_customer, quickbooks_customer_list, stats, qb_company, tracker=tracker)
	_dbg(
		"sync_customers:done",
		{
			"created": stats["created"],
			"updated": stats["updated"],
			"skipped": stats.get("skipped", 0),
			"errors": stats.get("errors", 0),
			"total_processed": len(quickbooks_customer_list),
			"total_fetched": len(get_qb_customer),
		},
	)

	# Log summary if there were failures
	if stats.get("errors", 0) > 0:
		failed_customers = stats.get("failed_customers", [])
		error_msg = _(
			"Customer sync completed with {0} error(s). "
			"{1} customers failed to sync. "
			"Please check the Activity Log for details."
		).format(stats["errors"], stats["errors"])

		if failed_customers:
			failed_names = [cust.get("name", "Unknown") for cust in failed_customers[:10]]
			error_msg += _("\n\nFailed customers (first 10): {0}").format(", ".join(failed_names))

		qb_log_error(
			title=_("Customer Sync Completed with Errors"),
			status="Error",
			method="sync_customers",
			message=error_msg,
			module="sync_customers",
			request_data={"failed_customers": failed_customers[:10]},
		)


def sync_qb_customers(get_qb_customer, quickbooks_customer_list, stats=None, qb_company=None, tracker=None):
	if stats is None:
		stats = {
			"created": 0,
			"updated": 0,
			"skipped": 0,
			"errors": 0,
			"failed_customers": [],
		}

	# Initialize generic Import Tracker if not provided
	if not tracker:
		tracker = ImportTracker(
			len(get_qb_customer),
			"qb_customer_import.log",
			module_name="CUSTOMER",
			company=qb_company,
		)

	_dbg(
		"sync_qb_customers:start",
		{"total_customers": len(get_qb_customer), "qb_company": qb_company},
	)

	# Performance optimization: Batch commits every 20 customers instead of each one
	commit_interval = 20
	last_commit_count = 0

	for idx, qb_customer in enumerate(get_qb_customer):
		qb_id = qb_customer.get("Id")
		# Check if customer exists (use unique QB ID with company prefix)
		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		unique_qb_id = make_unique_qb_id(qb_id, qb_company)
		filters = {"quickbooks_cust_id": unique_qb_id}
		if qb_company:
			filters["custom_company"] = qb_company
		existing_name = frappe.db.get_value("Customer", filters, "name")

		# Extract customer display name for logging
		customer_display_name = (
			qb_customer.get("DisplayName")
			or qb_customer.get("CompanyName")
			or qb_customer.get("name")
			or "Unknown"
		)

		# Log start of processing for this item
		# Use unique_qb_id for consistent tracking
		# Also include display name for readability if needed, but ID is primary
		tracker_id = f"{unique_qb_id} ({customer_display_name})"
		tracker.log_processing("QuickBooks", tracker_id)

		if not existing_name:
			_dbg("sync_qb_customers:create", {"idx": idx, "Id": qb_id})
			try:
				result = create_customer(qb_customer, quickbooks_customer_list, qb_company)
				# Check if customer was actually created
				if result:
					stats["created"] += 1
					tracker.log_success("QuickBooks", tracker_id, "Created")
					_dbg(
						"sync_qb_customers:created",
						{"idx": idx, "Id": qb_id, "created": True},
					)
				else:
					stats["skipped"] += 1
					tracker.log_skip("QuickBooks", tracker_id, "creation_failed_or_skipped")
					_dbg(
						"sync_qb_customers:skipped",
						{
							"idx": idx,
							"Id": qb_id,
							"reason": "creation_failed_or_skipped",
						},
					)
			except Exception as e:
				stats["errors"] += 1
				tracker.log_error("QuickBooks", tracker_id, str(e))
				customer_name = qb_customer.get("DisplayName") or qb_customer.get("CompanyName") or "Unknown"
				stats["failed_customers"].append(
					{
						"qb_id": qb_id,
						"name": customer_name,
						"error": str(e),
						"action": "create",
					}
				)
				_dbg(
					"sync_qb_customers:create_error",
					{"idx": idx, "Id": qb_id, "name": customer_name, "error": str(e)},
				)
				qb_log_error(
					title=_("Failed to Create Customer"),
					status="Error",
					method="sync_qb_customers",
					message=_("Failed to create customer '{0}' (QuickBooks ID: {1}). " "Error: {2}").format(
						customer_name, qb_id, str(e)
					),
					module="sync_customers",
					request_data=qb_customer,
				)
		else:
			_dbg(
				"sync_qb_customers:update_exists",
				{"idx": idx, "Id": qb_id, "name": existing_name},
			)
			try:
				update_customer(qb_customer, existing_name, quickbooks_customer_list)
				stats["updated"] += 1
				tracker.log_success("QuickBooks", tracker_id, "Updated")
			except Exception as e:
				stats["errors"] += 1
				tracker.log_error("QuickBooks", tracker_id, str(e))
				customer_name = (
					qb_customer.get("DisplayName") or qb_customer.get("CompanyName") or existing_name
				)
				stats["failed_customers"].append(
					{
						"qb_id": qb_id,
						"name": customer_name,
						"error": str(e),
						"action": "update",
					}
				)
				_dbg(
					"sync_qb_customers:update_error",
					{"idx": idx, "Id": qb_id, "name": customer_name, "error": str(e)},
				)
				qb_log_error(
					title=_("Failed to Update Customer"),
					status="Error",
					method="sync_qb_customers",
					message=_("Failed to update customer '{0}' (QuickBooks ID: {1}). " "Error: {2}").format(
						customer_name, qb_id, str(e)
					),
					module="sync_customers",
					request_data=qb_customer,
				)

		# Batch commit every N customers for performance
		current_processed = stats["created"] + stats["updated"] + stats["skipped"] + stats["errors"]
		if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
			frappe.db.commit()
			last_commit_count = current_processed

	# Final commit for any remaining customers
	if stats["created"] > 0 or stats["updated"] > 0 or stats["errors"] > 0:
		frappe.db.commit()

	# Log summary
	tracker.log_summary()

	return stats


def create_customer(qb_customer, quickbooks_customer_list, qb_company=None):
	"""store Customer data in ERPNEXT

	Returns:
	    bool: True if customer was created successfully, False otherwise
	"""
	customer = None
	try:
		# Use qb_company parameter if provided,
		# otherwise fall back to session defaults
		resolved_company = qb_company
		if not resolved_company:
			resolved_company = (
				frappe.defaults.get_user_default("company")
				or frappe.db.get_single_value("Global Defaults", "default_company")
				or frappe.db.get_value("Company", {}, "name")
			)
		_dbg(
			"create_customer:resolved_company",
			{
				"resolved_company": resolved_company,
				"qb_company": qb_company,
				"from_qb_company": bool(qb_company),
			},
		)
		if not resolved_company:
			raise Exception("No Company found to assign to Customer")

		# Get QuickBooks customer ID
		qb_customer_id = str(qb_customer.get("Id", ""))
		if not qb_customer_id:
			_dbg(
				"create_customer:skip_no_qb_id",
				{"customer": qb_customer.get("DisplayName", "Unknown")},
			)
			return False  # Skip if no QB ID

		# FIRST: Check if customer with this QuickBooks ID already exists (use unique QB ID)
		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		unique_qb_id = make_unique_qb_id(qb_customer_id, resolved_company)
		filters = {"quickbooks_cust_id": unique_qb_id}
		if resolved_company:
			filters["custom_company"] = resolved_company
		existing_by_qb_id = frappe.db.get_value("Customer", filters, "name")
		if existing_by_qb_id:
			_dbg(
				"create_customer:exists_by_qb_id",
				{
					"quickbooks_cust_id": qb_customer_id,
					"existing": existing_by_qb_id,
					"action": "updating",
				},
			)
			# Update existing customer with latest QuickBooks data
			update_customer(qb_customer, existing_by_qb_id, quickbooks_customer_list)
			return True  # Successfully updated

		customer_name = (
			str(qb_customer.get("DisplayName"))
			if qb_customer.get("DisplayName")
			else str(qb_customer.get("name"))
		)

		# Check if customer is deleted in QuickBooks (name contains "(deleted)")
		is_deleted = "(deleted)" in customer_name.lower() if customer_name else False
		# Ensure "(deleted)" suffix is present if customer is deleted
		if is_deleted and not customer_name.lower().endswith("(deleted)"):
			# Remove any existing "(deleted)" from middle and add at end
			customer_name = customer_name.replace(" (deleted)", "").replace("(deleted)", "").strip()
			customer_name = f"{customer_name} (deleted)"

		# SECOND: Check for duplicate customer_name WITHIN SAME COMPANY (only if no QB ID match found)
		# This handles ERPNext default customers that don't have QB ID yet
		# CRITICAL: Always filter by company to prevent cross-company data sharing
		name_filters = {"customer_name": customer_name}
		if resolved_company:
			name_filters["custom_company"] = resolved_company
		existing_customer = frappe.db.get_value("Customer", name_filters, "name")
		if existing_customer:
			_dbg(
				"create_customer:duplicate_name",
				{
					"customer_name": customer_name,
					"existing": existing_customer,
					"company": resolved_company,
				},
			)
			# If duplicate exists but has no quickbooks_cust_id, update it
			existing_qb_id = frappe.db.get_value("Customer", existing_customer, "quickbooks_cust_id")
			if not existing_qb_id:
				_dbg(
					"create_customer:duplicate_no_qb_id",
					{
						"customer_name": customer_name,
						"existing": existing_customer,
						"company": resolved_company,
						"action": "updating",
					},
				)
				update_customer(qb_customer, existing_customer, quickbooks_customer_list)
				return True  # Successfully updated
			else:
				# Customer with same name exists but has different QB ID
				# Create new customer with unique name
				_dbg(
					"create_customer:name_conflict",
					{
						"customer_name": customer_name,
						"existing": existing_customer,
						"existing_qb_id": existing_qb_id,
						"new_qb_id": qb_customer_id,
						"company": resolved_company,
						"note": "Customer with same name exists with different QB ID - will create with unique name",
					},
				)
				# Append QB ID to name to make it unique
				customer_name = f"{customer_name} (QB-{qb_customer_id})"

		# Extract email from PrimaryEmailAddr if available
		if qb_customer.get("PrimaryEmailAddr"):
			if isinstance(qb_customer.get("PrimaryEmailAddr"), dict):
				qb_customer["PrimaryEmailAddr"].get("Address", "")
			else:
				str(qb_customer.get("PrimaryEmailAddr"))

		# Extract phone numbers
		fax_number = None
		if qb_customer.get("PrimaryPhone"):
			if isinstance(qb_customer.get("PrimaryPhone"), dict):
				qb_customer["PrimaryPhone"].get("FreeFormNumber", "")
			else:
				str(qb_customer.get("PrimaryPhone"))
		if qb_customer.get("Mobile"):
			if isinstance(qb_customer.get("Mobile"), dict):
				qb_customer["Mobile"].get("FreeFormNumber", "")
			else:
				str(qb_customer.get("Mobile"))
		# Extract Fax for BIN/VAT Registration No.
		if qb_customer.get("Fax"):
			if isinstance(qb_customer.get("Fax"), dict):
				fax_number = qb_customer["Fax"].get("FreeFormNumber", "")
			else:
				fax_number = str(qb_customer.get("Fax"))

		# Determine customer_type based on Job field
		# If Job=True, it's a job/project, otherwise Individual or Company
		customer_type = _("Individual")
		if qb_customer.get("Job"):
			# Job customers might be treated differently
			customer_type = _("Company")  # or could be "Individual" based on business logic
		elif qb_customer.get("CompanyName"):
			customer_type = _("Company")

		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		qb_cust_id_raw = qb_customer.get("Id") or qb_customer.get("value")
		payload = {
			"doctype": "Customer",
			"quickbooks_cust_id": make_unique_qb_id(qb_cust_id_raw, resolved_company),
			"customer_name": customer_name,
			"customer_type": customer_type,
			"customer_group": _("Commercial"),
			"default_currency": (
				qb_customer["CurrencyRef"].get("value", "") if qb_customer.get("CurrencyRef") else ""
			),
			"territory": _("All Territories"),
			# Set both standard and custom company fields
			"company": resolved_company,
			"custom_company": resolved_company,
			"custom_onboarding_date": _get_default_for_customer_field("custom_onboarding_date"),
			"account_manager": _get_default_for_customer_field("account_manager"),
		}

		# Map Disabled from QuickBooks Active
		# IMPORTANT: For deleted customers, don't disable - just add "(deleted)" suffix to name
		# This allows transactions to be created for deleted customers
		if qb_customer.get("Active") is not None:
			try:
				meta = frappe.get_meta("Customer")
				if meta.has_field("disabled"):
					# Only disable if customer is inactive AND not deleted
					# Deleted customers should remain enabled to allow transaction creation
					if is_deleted:
						# Don't disable deleted customers - keep them enabled
						payload["disabled"] = 0
						_dbg(
							"create_customer:deleted_customer_not_disabled",
							{
								"customer_name": customer_name,
								"note": "Deleted customer kept enabled to allow transaction creation",
							},
						)
					else:
						# IMPORTANT: Keep all customers enabled in ERPNext regardless of QuickBooks Active status
						# ERPNext blocks transactions with disabled customers, so we keep them all active
						payload["disabled"] = 0
			except Exception:
				pass

		# Map Fax to custom_binvat_registration_no
		# (BIN/VAT Registration No.)
		if fax_number:
			try:
				meta = frappe.get_meta("Customer")
				if meta.has_field("custom_binvat_registration_no"):
					payload["custom_binvat_registration_no"] = fax_number
			except Exception:
				pass

		# Map DefaultTaxCodeRef to tax_id (e TIN No/Tax ID)
		if qb_customer.get("DefaultTaxCodeRef"):
			tax_code_value = None
			tax_ref = qb_customer.get("DefaultTaxCodeRef")
			if isinstance(tax_ref, dict):
				tax_code_value = tax_ref.get("value", "")
			else:
				tax_code_value = str(tax_ref)
			if tax_code_value:
				try:
					meta = frappe.get_meta("Customer")
					if meta.has_field("tax_id"):
						payload["tax_id"] = tax_code_value
				except Exception:
					pass

		# Map SalesTermRef to payment_terms_template
		# (Default Payment Terms Template)
		if qb_customer.get("SalesTermRef"):
			term_ref = qb_customer.get("SalesTermRef")
			term_name = None
			if isinstance(term_ref, dict):
				term_name = term_ref.get("name", "")
			if term_name:
				try:
					meta = frappe.get_meta("Customer")
					if meta.has_field("payment_terms_template"):
						payment_term = frappe.db.get_value(
							"Payment Terms Template",
							{"name": term_name},
							"name",
						)
						if payment_term:
							payload["payment_terms_template"] = payment_term
				except Exception:
					pass

		# DON'T add email_id or mobile_no to customer document
		# This would trigger ERPNext's automatic contact creation which fails
		# with incomplete data. Instead, we create contacts manually with
		# create_customer_contact() which has proper validation.
		# if email_id:
		#     try:
		#         meta = frappe.get_meta("Customer")
		#         if meta.has_field("email_id"):
		#             payload["email_id"] = email_id
		#     except Exception:
		#         pass
		#
		# if primary_phone:
		#     try:
		#         meta = frappe.get_meta("Customer")
		#         if meta.has_field("mobile_no"):
		#             payload["mobile_no"] = mobile_phone or primary_phone
		#         elif meta.has_field("phone"):
		#             payload["phone"] = primary_phone
		#     except Exception:
		#         pass

		_dbg(
			"create_customer:payload",
			{
				k: payload.get(k)
				for k in [
					"quickbooks_cust_id",
					"customer_name",
					"company",
					"custom_company",
				]
			},
		)

		# Create customer document
		# No need for skip_contact_creation flag since we don't set email_id/mobile_no
		# which are required to trigger automatic contact creation
		customer = frappe.get_doc(payload)
		try:
			customer.insert()
		except frappe.DuplicateEntryError as e:
			# Customer with this auto-generated ID already exists
			# Extract the duplicate name from the error message
			import re

			error_str = str(e)
			_dbg(
				"create_customer:duplicate_entry_caught",
				{
					"customer_name": customer_name,
					"error": error_str,
					"action": "attempting_recovery",
				},
			)

			# Try to extract the duplicate customer name from error
			# Error format: ('Customer', 'ATL/CUST/2025/00016', ...)
			duplicate_name = None
			match = re.search(r"'([^']+/CUST/[^']+)'", error_str)
			if match:
				duplicate_name = match.group(1)

			# If we found the duplicate name, check if it needs QuickBooks ID
			if duplicate_name:
				_dbg(
					"create_customer:found_duplicate_name",
					{"duplicate_name": duplicate_name},
				)
				# Check if this customer has a quickbooks_cust_id
				existing_qb_id = frappe.db.get_value("Customer", duplicate_name, "quickbooks_cust_id")

				if not existing_qb_id:
					# Update this customer with QuickBooks data
					_dbg(
						"create_customer:updating_duplicate_no_qb_id",
						{"duplicate_name": duplicate_name},
					)
					update_customer(qb_customer, duplicate_name, quickbooks_customer_list)
					return True  # Successfully updated
				else:
					# Customer already has QB ID, this is a real duplicate
					_dbg(
						"create_customer:skip_has_qb_id",
						{
							"duplicate_name": duplicate_name,
							"existing_qb_id": existing_qb_id,
						},
					)
					return False  # Skip

			# Fallback: try to find by customer_name
			existing_by_name = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
			if existing_by_name:
				_dbg(
					"create_customer:fallback_found_by_name",
					{"existing_by_name": existing_by_name},
				)
				update_customer(qb_customer, existing_by_name, quickbooks_customer_list)
				return True  # Successfully updated instead

			# If we still can't handle it, skip this customer
			_dbg(
				"create_customer:unhandled_duplicate",
				{"error": error_str, "skipping": True},
			)
			return False  # Skip this customer to continue sync

		# Map ARAccountRef to account (Child Table) if available
		# PRIORITY 1: Use AccountRef from QuickBooks (exact match)
		if qb_customer.get("ARAccountRef"):
			try:
				account_ref = qb_customer.get("ARAccountRef")
				if account_ref:
					# Use get_account_from_quickbooks_ref to prioritize AccountRef
					erpnext_account = get_account_from_quickbooks_ref(
						account_ref,
						company=resolved_company,
						# account_type and root_type not needed - QB ID is unique identifier
						module_name="sync_customers",
						strict_qb_id_only=True,
					)
					if erpnext_account:
						# Add to account child table if it exists
						meta = frappe.get_meta("Customer")
						if meta.has_field("accounts"):
							customer.append(
								"accounts",
								{
									"company": resolved_company,
									"account": erpnext_account,
								},
							)
							customer.save()
							_dbg(
								"create_customer:account_added",
								{
									"account": erpnext_account,
									"qb_account_ref": account_ref,
								},
							)
			except Exception as e:
				_dbg(
					"create_customer:account_mapping_error",
					{"error": str(e), "qb_account_ref": account_ref},
				)

		# Create billing address if available
		if customer and qb_customer.get("BillAddr"):
			_dbg("create_customer:create_billing_address", {"customer": customer.name})
			create_customer_address(customer, qb_customer.get("BillAddr"), "Billing")
			# Set customer_primary_address if this is the first address
			try:
				meta = frappe.get_meta("Customer")
				if meta.has_field("customer_primary_address"):
					addresses = frappe.get_all(
						"Dynamic Link",
						filters={
							"link_doctype": "Customer",
							"link_name": customer.name,
							"parenttype": "Address",
						},
						fields=["parent"],
						limit=1,
					)
					if addresses:
						customer.customer_primary_address = addresses[0].parent
						customer.save()
			except Exception:
				pass

		# Create shipping address if available
		if customer and qb_customer.get("ShipAddr"):
			_dbg("create_customer:create_shipping_address", {"customer": customer.name})
			create_customer_address(customer, qb_customer.get("ShipAddr"), "Shipping")

		# Create contact if we have proper contact data
		if customer:
			create_customer_contact(customer, qb_customer)

		# Note: Commit will be batched in sync_qb_customers() for better performance
		quickbooks_customer_list.append(customer.quickbooks_cust_id)
		_dbg(
			"create_customer:success",
			{
				"customer": customer.name,
				"quickbooks_cust_id": customer.quickbooks_cust_id,
			},
		)
		return True  # Successfully created

	except Exception as e:
		_dbg("create_customer:error", {"error": str(e)})
		qb_log_exception(
			method="create_customer",
			err=e,
			request_data=qb_customer,
			module="sync_customers",
		)
		return False  # Failed to create


def update_customer(qb_customer, existing_name, quickbooks_customer_list):
	"""Update existing Customer with latest data from QuickBooks"""
	try:
		customer = frappe.get_doc("Customer", existing_name)

		# Update customer_name if changed in QuickBooks
		qb_display_name = (
			str(qb_customer.get("DisplayName"))
			if qb_customer.get("DisplayName")
			else str(qb_customer.get("name"))
		)
		if customer.customer_name != qb_display_name:
			old_name = customer.customer_name
			customer.customer_name = qb_display_name
			_dbg(
				"update_customer:name_changed",
				{"old": old_name, "new": qb_display_name},
			)

		# Update default currency if available
		if qb_customer.get("CurrencyRef"):
			new_currency = qb_customer["CurrencyRef"].get("value", "")
			if new_currency and customer.default_currency != new_currency:
				customer.default_currency = new_currency
				_dbg("update_customer:currency_changed", {"currency": new_currency})

		# Update disabled from Active (inverse mapping)
		# IMPORTANT: For deleted customers, don't disable - just ensure "(deleted)" suffix in name
		if qb_customer.get("Active") is not None:
			try:
				meta = frappe.get_meta("Customer")
				if meta.has_field("disabled"):
					# Check if customer is deleted in QuickBooks
					is_deleted = "(deleted)" in qb_display_name.lower() if qb_display_name else False

					if is_deleted:
						# Don't disable deleted customers - keep them enabled
						if getattr(customer, "disabled", None) != 0:
							customer.disabled = 0
							_dbg(
								"update_customer:deleted_customer_enabled",
								{
									"customer_name": qb_display_name,
									"note": "Deleted customer kept enabled to allow transaction creation",
								},
							)
						# Ensure "(deleted)" suffix is in customer name
						if not customer.customer_name.lower().endswith("(deleted)"):
							customer.customer_name = qb_display_name
					else:
						# IMPORTANT: Keep all customers enabled in ERPNext regardless of QuickBooks Active status
						# ERPNext blocks transactions with disabled customers, so we keep them all active
						if getattr(customer, "disabled", None) != 0:
							customer.disabled = 0
			except Exception:
				pass

		# Update Fax to custom_binvat_registration_no
		if qb_customer.get("Fax"):
			fax_number = None
			if isinstance(qb_customer.get("Fax"), dict):
				fax_number = qb_customer["Fax"].get("FreeFormNumber", "")
			else:
				fax_number = str(qb_customer.get("Fax"))
			if fax_number:
				try:
					meta = frappe.get_meta("Customer")
					if meta.has_field("custom_binvat_registration_no"):
						if getattr(customer, "custom_binvat_registration_no", None) != fax_number:
							customer.custom_binvat_registration_no = fax_number
							_dbg(
								"update_customer:binvat_changed",
								{"binvat": fax_number},
							)
				except Exception:
					pass

		# Update DefaultTaxCodeRef to tax_id
		if qb_customer.get("DefaultTaxCodeRef"):
			tax_code_value = None
			if isinstance(qb_customer.get("DefaultTaxCodeRef"), dict):
				tax_code_value = qb_customer["DefaultTaxCodeRef"].get("value", "")
			else:
				tax_code_value = str(qb_customer.get("DefaultTaxCodeRef"))
			if tax_code_value:
				try:
					meta = frappe.get_meta("Customer")
					if meta.has_field("tax_id"):
						if getattr(customer, "tax_id", None) != tax_code_value:
							customer.tax_id = tax_code_value
							_dbg(
								"update_customer:tax_id_changed",
								{"tax_id": tax_code_value},
							)
				except Exception:
					pass

		# Update SalesTermRef to payment_terms_template
		if qb_customer.get("SalesTermRef"):
			term_ref = qb_customer.get("SalesTermRef")
			term_name = None
			if isinstance(term_ref, dict):
				term_name = term_ref.get("name", "")
			if term_name:
				try:
					meta = frappe.get_meta("Customer")
					if meta.has_field("payment_terms_template"):
						payment_term = frappe.db.get_value(
							"Payment Terms Template",
							{"name": term_name},
							"name",
						)
						if payment_term:
							if getattr(customer, "payment_terms_template", None) != payment_term:
								customer.payment_terms_template = payment_term
								_dbg(
									"update_customer:payment_terms_changed",
									{"payment_terms": payment_term},
								)
				except Exception:
					pass

		# Update customer_type based on Job field
		if qb_customer.get("Job") is not None:
			is_job = qb_customer.get("Job")
			new_customer_type = _("Company") if is_job else _("Individual")
			if qb_customer.get("CompanyName") and not is_job:
				new_customer_type = _("Company")
			if customer.customer_type != new_customer_type:
				customer.customer_type = new_customer_type
				_dbg(
					"update_customer:customer_type_changed",
					{"customer_type": new_customer_type},
				)

		# DON'T update email_id or mobile_no on customer document
		# This would trigger ERPNext's automatic contact creation which fails
		# with incomplete data. Instead, we create/update contacts manually with
		# create_customer_contact() which has proper validation.
		#
		# The code below is commented out to prevent automatic contact creation:
		# if qb_customer.get("PrimaryEmailAddr"):
		#     email_id = None
		#     if isinstance(qb_customer.get("PrimaryEmailAddr"), dict):
		#         email_id = qb_customer["PrimaryEmailAddr"].get("Address", "")
		#     else:
		#         email_id = str(qb_customer.get("PrimaryEmailAddr"))
		#     if email_id:
		#         try:
		#             meta = frappe.get_meta("Customer")
		#             if (
		#                 meta.has_field("email_id")
		#                 and getattr(customer, "email_id", None) != email_id
		#             ):
		#                 customer.email_id = email_id
		#                 _dbg("update_customer:email_changed", {"email": email_id})
		#         except Exception:
		#             pass
		#
		# if qb_customer.get("PrimaryPhone"):
		#     phone = None
		#     if isinstance(qb_customer.get("PrimaryPhone"), dict):
		#         phone = qb_customer["PrimaryPhone"].get("FreeFormNumber", "")
		#     else:
		#         phone = str(qb_customer.get("PrimaryPhone"))
		#     if phone:
		#         try:
		#             meta = frappe.get_meta("Customer")
		#             if meta.has_field("mobile_no"):
		#                 if getattr(customer, "mobile_no", None) != phone:
		#                     customer.mobile_no = phone
		#                     _dbg("update_customer:phone_changed", {"phone": phone})
		#             elif meta.has_field("phone"):
		#                 if getattr(customer, "phone", None) != phone:
		#                     customer.phone = phone
		#                     _dbg("update_customer:phone_changed", {"phone": phone})
		#         except Exception:
		#             pass

		# Update billing address if BillAddr changed
		if qb_customer.get("BillAddr"):
			update_customer_address(customer, qb_customer.get("BillAddr"), "Billing")
			# Update customer_primary_address if field exists
			try:
				meta = frappe.get_meta("Customer")
				if meta.has_field("customer_primary_address"):
					addresses = frappe.get_all(
						"Dynamic Link",
						filters={
							"link_doctype": "Customer",
							"link_name": customer.name,
							"parenttype": "Address",
						},
						fields=["parent"],
						limit=1,
					)
					if addresses:
						customer.customer_primary_address = addresses[0].parent
			except Exception:
				pass

		# Update shipping address if ShipAddr changed
		if qb_customer.get("ShipAddr"):
			update_customer_address(customer, qb_customer.get("ShipAddr"), "Shipping")

		# Save customer - use db_update to bypass version checking for sync operations
		# This prevents TimestampMismatchError when hooks modify the document
		# db_update() updates the database directly without document-level version checks
		# No need for skip_contact_creation flag since we don't set email_id/mobile_no
		try:
			# First try normal save with ignore_version
			customer.save(ignore_version=True)
		except frappe.exceptions.TimestampMismatchError:
			# If timestamp mismatch, use db_update to bypass version checking
			_dbg(
				"update_customer:using_db_update",
				{"customer": customer.name},
			)
			# db_update() updates the database directly, bypassing document-level checks
			customer.db_update()
		except ModuleNotFoundError as e:
			if "frappe.utils.modules" in str(e):
				# Clear cache and retry once
				import sys

				# Clear any cached modules that might have the old import
				modules_to_clear = [
					k for k in sys.modules.keys() if "crm.api" in k or "frappe.utils.modules" in k
				]
				for mod in modules_to_clear:
					if mod in sys.modules:
						del sys.modules[mod]
				# Retry save
				customer.save(ignore_version=True)
			else:
				raise

		# Create contact if it doesn't exist and we have proper contact data
		create_customer_contact(customer, qb_customer)

		# Note: Commit will be batched in sync_qb_customers() for better performance
		quickbooks_customer_list.append(customer.quickbooks_cust_id)
		_dbg("update_customer:success", {"customer": customer.name})

	except Exception as e:
		_dbg("update_customer:error", {"error": str(e)})
		qb_log_exception(
			method="update_customer",
			err=e,
			request_data=qb_customer,
			module="sync_customers",
		)

	return quickbooks_customer_list


def update_customer_address(customer, address, address_type="Billing"):
	"""Update existing address or create if not found"""
	try:
		# Try to find existing address by quickbooks_address_id only
		# Address in ERPNext uses Dynamic Link child table, not direct customer field
		if not address.get("Id"):
			_dbg("update_customer_address:no_id", {"address": address})
			return

		# Use company-prefixed unique ID like sync_account.py does
		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		unique_address_id = make_unique_qb_id(str(address.get("Id")), customer.custom_company)

		existing_addr = frappe.db.get_value(
			"Address",
			{"quickbooks_address_id": unique_address_id},
			"name",
		)

		if existing_addr:
			adoc = frappe.get_doc("Address", existing_addr)

			# Update address fields
			if address.get("Line1"):
				adoc.address_line1 = address.get("Line1")
			else:
				# Ensure line1 is never empty
				if not adoc.address_line1:
					adoc.address_line1 = "N/A"

			# Handle additional address lines
			address_lines = []
			for line_num in range(2, 6):
				line_key = f"Line{line_num}"
				if address.get(line_key):
					address_lines.append(address.get(line_key))
			if address_lines:
				adoc.address_line2 = "\n".join(address_lines)

			# Update city with default if empty
			if address.get("City"):
				adoc.city = address.get("City")
			else:
				if not adoc.city:
					adoc.city = "N/A"

			if address.get("CountrySubDivisionCode"):
				adoc.state = address.get("CountrySubDivisionCode")

			# Update pincode with default if empty
			if address.get("PostalCode"):
				adoc.pincode = address.get("PostalCode")
			else:
				if not adoc.pincode:
					adoc.pincode = "000000"
			# Fix: Use Country field, not CountrySubDivisionCode for country lookup
			if address.get("Country"):
				country_name = _resolve_country_name(address.get("Country"))
				if country_name:
					adoc.country = country_name
			# Update address_type if provided
			if address_type:
				adoc.address_type = address_type

			adoc.save()
			_dbg("update_customer_address:updated", {"address": adoc.name})
		else:
			# Create new address if not found
			_dbg("update_customer_address:create_new", {"qb_id": address.get("Id")})
			create_customer_address(customer, address, address_type)

	except Exception as e:
		_dbg("update_customer_address:error", {"error": str(e)})
		qb_log_exception(
			method="update_customer_address",
			err=e,
			request_data=address,
			module="sync_customers",
		)


def create_customer_address(customer, address, address_type="Billing"):
	address_title, resolved_address_type = get_address_title_and_type(customer.customer_name, address_type)
	try:
		# Build address lines
		address_line1 = address.get("Line1", "")
		address_lines = []
		for line_num in range(2, 6):
			line_key = f"Line{line_num}"
			if address.get(line_key):
				address_lines.append(address.get(line_key))

		# Resolve country name from Country field (not CountrySubDivisionCode)
		country_name = None
		if address.get("Country"):
			country_name = _resolve_country_name(address.get("Country"))

		# Build address payload with Dynamic Link
		# Provide defaults for mandatory fields if missing
		city = address.get("City") or "N/A"
		pincode = address.get("PostalCode") or "000000"

		# Use company-prefixed unique ID like sync_account.py does
		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		unique_address_id = (
			make_unique_qb_id(str(address.get("Id")), customer.company) if address.get("Id") else None
		)

		address_payload = {
			"doctype": "Address",
			"quickbooks_address_id": unique_address_id,
			"address_title": address_title,
			"address_type": resolved_address_type,
			"address_line1": address_line1 or "N/A",  # Ensure not empty
			"city": city,
			"state": address.get("CountrySubDivisionCode", ""),
			"pincode": pincode,
			"country": country_name or "Bangladesh",  # Default country
			"links": [{"link_doctype": "Customer", "link_name": customer.name}],
		}

		# Add address_line2 if there are additional lines
		if address_lines:
			address_payload["address_line2"] = "\n".join(address_lines)

		doc = frappe.get_doc(address_payload).insert()
		_dbg(
			"create_customer_address:success",
			{
				"address": doc.name,
				"customer": customer.name,
				"type": resolved_address_type,
			},
		)

	except Exception as e:
		_dbg("create_customer_address:error", {"error": str(e)})
		qb_log_exception(
			method="create_customer_address",
			err=e,
			request_data=address,
			module="sync_customers",
		)
		raise e


def create_customer_contact(customer, qb_customer):
	"""Create or update a primary contact for the customer if we have proper contact data"""
	try:
		# Extract contact information from QuickBooks
		first_name = None
		last_name = None

		# Try to get first_name and last_name from GivenName/FamilyName
		if qb_customer.get("GivenName"):
			first_name = str(qb_customer.get("GivenName"))
		if qb_customer.get("FamilyName"):
			last_name = str(qb_customer.get("FamilyName"))

		# If no proper name data, skip contact creation
		if not first_name:
			_dbg(
				"create_customer_contact:no_first_name",
				{"customer": customer.name, "qb_id": qb_customer.get("Id")},
			)
			return

		# Extract email
		email_id = None
		if qb_customer.get("PrimaryEmailAddr"):
			if isinstance(qb_customer.get("PrimaryEmailAddr"), dict):
				email_id = qb_customer["PrimaryEmailAddr"].get("Address", "")
			else:
				email_id = str(qb_customer.get("PrimaryEmailAddr"))

		# Extract phone numbers
		phone = None
		mobile_no = None
		if qb_customer.get("PrimaryPhone"):
			if isinstance(qb_customer.get("PrimaryPhone"), dict):
				phone = qb_customer["PrimaryPhone"].get("FreeFormNumber", "")
			else:
				phone = str(qb_customer.get("PrimaryPhone"))
		if qb_customer.get("Mobile"):
			if isinstance(qb_customer.get("Mobile"), dict):
				mobile_no = qb_customer["Mobile"].get("FreeFormNumber", "")
			else:
				mobile_no = str(qb_customer.get("Mobile"))

		# Check if contact already exists for this customer
		existing_contact = frappe.db.get_value(
			"Dynamic Link",
			{
				"link_doctype": "Customer",
				"link_name": customer.name,
				"parenttype": "Contact",
			},
			"parent",
		)

		if existing_contact:
			# Update existing contact
			_dbg(
				"create_customer_contact:updating_existing",
				{"customer": customer.name, "contact": existing_contact},
			)
			contact = frappe.get_doc("Contact", existing_contact)

			# Update name fields
			if first_name and contact.first_name != first_name:
				contact.first_name = first_name
			if last_name and contact.last_name != last_name:
				contact.last_name = last_name

			# Update email if available
			if email_id:
				existing_email = False
				for email_row in contact.email_ids:
					if email_row.email_id == email_id:
						existing_email = True
						break
				if not existing_email:
					# Clear existing emails and add new one
					contact.email_ids = []
					contact.append("email_ids", {"email_id": email_id, "is_primary": 1})

			# Update phone numbers if available
			if phone or mobile_no:
				contact.phone_nos = []
				if mobile_no:
					contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
				if phone and phone != mobile_no:
					contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1})

			contact.save(ignore_permissions=True)
			_dbg(
				"create_customer_contact:updated",
				{"customer": customer.name, "contact": contact.name},
			)
			return

		# Create new contact
		contact_payload = {
			"doctype": "Contact",
			"first_name": first_name,
			"last_name": last_name,
			"links": [{"link_doctype": "Customer", "link_name": customer.name}],
		}

		# Add email if available
		if email_id:
			contact_payload["email_ids"] = [{"email_id": email_id, "is_primary": 1}]

		# Add phone if available
		if phone or mobile_no:
			contact_payload["phone_nos"] = []
			if mobile_no:
				contact_payload["phone_nos"].append({"phone": mobile_no, "is_primary_mobile_no": 1})
			if phone and phone != mobile_no:
				contact_payload["phone_nos"].append({"phone": phone, "is_primary_phone": 1})

		contact = frappe.get_doc(contact_payload)
		contact.insert(ignore_permissions=True)

		_dbg(
			"create_customer_contact:success",
			{
				"customer": customer.name,
				"contact": contact.name,
				"first_name": first_name,
				"last_name": last_name,
			},
		)

	except Exception as e:
		# Don't fail the whole sync if contact creation fails
		_dbg(
			"create_customer_contact:error",
			{"customer": customer.name, "error": str(e)},
		)


def get_address_title_and_type(customer_name, address_type="Billing"):
	"""Get address title and type, handling duplicates."""
	resolved_address_type = _("Billing") if address_type == "Billing" else _("Shipping")
	address_title = customer_name.strip()

	# Check if address with this title and type already exists
	# Note: We check by title and type only, since link_doctype is in a child table
	# The quickbooks_address_id check in update_customer_address handles uniqueness
	existing = frappe.db.get_value(
		"Address",
		{
			"address_title": address_title,
			"address_type": resolved_address_type,
		},
		"name",
	)

	# If exists, make title unique by appending type
	if existing:
		address_title = f"{address_title}-{resolved_address_type}"

	return address_title, resolved_address_type


"""	Sync Customer Records From ERPNext to QuickBooks """


def sync_erp_customers():
	"""Update quickbooks_cust_id on Customer from QuickBooks responses."""
	# Ensure a Company context exists before proceeding
	resolved_company = (
		frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)
	_dbg("sync_erp_customers:resolved_company", {"resolved_company": resolved_company})
	if not resolved_company:
		raise Exception("No Company found. Set a default Company before syncing.")

	response_from_quickbooks = sync_erp_customers_to_quickbooks()
	if response_from_quickbooks:
		try:
			for response_obj in response_from_quickbooks.successes:
				if response_obj:
					# Fix SQL injection: use parameterized query
					frappe.db.sql(
						"""
                        UPDATE tabCustomer
                        SET quickbooks_cust_id = %s
                        WHERE customer_name = %s
                        """,
						(response_obj.Id, response_obj.DisplayName),
					)
					frappe.db.commit()
				else:
					raise _("Does not get any response from quickbooks")
		except Exception as e:
			qb_log_exception(
				method="sync_erp_customers",
				err=e,
				request_data=locals().get("response_obj"),
				module="sync_customers",
			)


def sync_erp_customers_to_quickbooks():
	"""Sync ERPNext Customer to QuickBooks"""
	Customer_list = []
	for erp_cust in erp_customer_data():
		try:
			if erp_cust:
				create_erp_customer_to_quickbooks(erp_cust, Customer_list)
			else:
				raise _("Customer does not exist in ERPNext")
		except Exception as e:
			qb_log_exception(
				method="sync_erp_customers_to_quickbooks",
				err=e,
				request_data=erp_cust,
				module="sync_customers",
			)
	results = batch_create(Customer_list)
	return results


def erp_customer_data():
	erp_customer = frappe.db.sql(
		("select `customer_name` from `tabCustomer` " "WHERE  quickbooks_cust_id IS NULL"),
		as_dict=1,
	)
	return erp_customer


def create_erp_customer_to_quickbooks(erp_cust, Customer_list):
	customer_obj = Customer()
	customer_obj.FullyQualifiedName = erp_cust.customer_name
	customer_obj.DisplayName = erp_cust.customer_name
	customer_obj.save()
	Customer_list.append(customer_obj)
	return Customer_list


def get_all_customers_json(source="quickbooks", quickbooks_obj=None, include_addresses=True):
	"""
	Get all customers as JSON.

	Args:
	    source (str): "erpnext" to get from ERPNext, "quickbooks" to get from QuickBooks
	    quickbooks_obj: QuickBooks client instance (required if source="quickbooks")
	    include_addresses (bool): Whether to include address details

	Returns:
	    str: JSON string of all customers
	"""
	if source == "erpnext":
		return get_erpnext_customers_json(include_addresses=include_addresses)
	elif source == "quickbooks":
		if not quickbooks_obj:
			raise Exception("quickbooks_obj is required when source='quickbooks'")
		return get_quickbooks_customers_json(quickbooks_obj, include_addresses=include_addresses)
	else:
		raise Exception("source must be 'erpnext' or 'quickbooks'")


def get_erpnext_customers_json(include_addresses=True):
	"""Get all ERPNext customers as JSON."""
	try:
		# Get Customer meta to check which fields exist
		meta = frappe.get_meta("Customer")
		available_fields = [f.fieldname for f in meta.fields]

		# Build list of standard fields that exist
		standard_fields = [
			"name",
			"customer_name",
			"customer_type",
			"customer_group",
			"territory",
			"default_currency",
			"quickbooks_cust_id",
			"email_id",
			"mobile_no",
			"phone",
		]

		# Only include fields that actually exist
		fields_to_query = [field for field in standard_fields if field in available_fields]

		# Also check for company field (might be standard or custom)
		if "company" in available_fields:
			fields_to_query.append("company")

		customers = frappe.get_all("Customer", fields=fields_to_query, order_by="customer_name")

		result = []
		for customer in customers:
			customer_dict = customer.copy()

			# Get full document to access all fields including custom ones
			try:
				doc = frappe.get_doc("Customer", customer.name)
				# Add any missing standard fields that exist
				for field in ["company", "email_id", "mobile_no", "phone"]:
					if field in available_fields and field not in customer_dict:
						if hasattr(doc, field):
							customer_dict[field] = getattr(doc, field, None)

				# Add custom fields
				for field in meta.fields:
					if field.fieldname.startswith("custom_"):
						if hasattr(doc, field.fieldname):
							customer_dict[field.fieldname] = getattr(doc, field.fieldname, None)
			except Exception:
				pass

			# Include addresses if requested
			if include_addresses:
				addresses = frappe.get_all(
					"Dynamic Link",
					filters={
						"link_doctype": "Customer",
						"link_name": customer.name,
						"parenttype": "Address",
					},
					fields=["parent"],
				)
				address_list = []
				for addr_link in addresses:
					try:
						addr_doc = frappe.get_doc("Address", addr_link.parent)
						address_dict = {
							"name": addr_doc.name,
							"address_title": addr_doc.address_title,
							"address_type": addr_doc.address_type,
							"address_line1": addr_doc.address_line1,
							"address_line2": addr_doc.address_line2,
							"city": addr_doc.city,
							"state": addr_doc.state,
							"pincode": addr_doc.pincode,
							"country": addr_doc.country,
							"email_id": addr_doc.email_id,
							"quickbooks_address_id": getattr(addr_doc, "quickbooks_address_id", None),
						}
						address_list.append(address_dict)
					except Exception:
						pass
				customer_dict["addresses"] = address_list

			result.append(customer_dict)

		return json.dumps(result, indent=2, default=str)

	except Exception as e:
		_dbg("get_erpnext_customers_json:error", {"error": str(e)})
		raise


def get_quickbooks_customers_json(quickbooks_obj, include_addresses=True):
	"""Get all QuickBooks customers as JSON."""
	try:
		_dbg("get_quickbooks_customers_json:start")
		customer_query = """SELECT * FROM Customer"""
		qb_customer = quickbooks_obj.query(customer_query)
		get_qb_customer = qb_customer["QueryResponse"].get("Customer", [])

		if not isinstance(get_qb_customer, list):
			get_qb_customer = [get_qb_customer]

		result = []
		for qb_cust in get_qb_customer:
			customer_dict = {
				"Id": qb_cust.get("Id"),
				"SyncToken": qb_cust.get("SyncToken"),
				"DisplayName": qb_cust.get("DisplayName"),
				"FullyQualifiedName": qb_cust.get("FullyQualifiedName"),
				"CompanyName": qb_cust.get("CompanyName"),
				"GivenName": qb_cust.get("GivenName"),
				"FamilyName": qb_cust.get("FamilyName"),
				"Active": qb_cust.get("Active"),
				"Balance": qb_cust.get("Balance"),
				"BalanceWithJobs": qb_cust.get("BalanceWithJobs"),
			}

			# Add currency if available
			if qb_cust.get("CurrencyRef"):
				customer_dict["CurrencyRef"] = {
					"value": qb_cust["CurrencyRef"].get("value"),
					"name": qb_cust["CurrencyRef"].get("name"),
				}

			# Add email if available
			if qb_cust.get("PrimaryEmailAddr"):
				if isinstance(qb_cust.get("PrimaryEmailAddr"), dict):
					customer_dict["PrimaryEmailAddr"] = {
						"Address": qb_cust["PrimaryEmailAddr"].get("Address")
					}
				else:
					customer_dict["PrimaryEmailAddr"] = qb_cust.get("PrimaryEmailAddr")

			# Add phone numbers if available
			for phone_field in ["PrimaryPhone", "Mobile", "AlternatePhone", "Fax"]:
				if qb_cust.get(phone_field):
					if isinstance(qb_cust.get(phone_field), dict):
						customer_dict[phone_field] = {
							"FreeFormNumber": qb_cust[phone_field].get("FreeFormNumber")
						}
					else:
						customer_dict[phone_field] = qb_cust.get(phone_field)

			# Include addresses if requested
			if include_addresses:
				addresses = {}
				for addr_type in ["BillAddr", "ShipAddr"]:
					if qb_cust.get(addr_type):
						addr = qb_cust[addr_type]
						addresses[addr_type] = {
							"Id": addr.get("Id"),
							"Line1": addr.get("Line1"),
							"Line2": addr.get("Line2"),
							"Line3": addr.get("Line3"),
							"Line4": addr.get("Line4"),
							"Line5": addr.get("Line5"),
							"City": addr.get("City"),
							"CountrySubDivisionCode": addr.get("CountrySubDivisionCode"),
							"Country": addr.get("Country"),
							"PostalCode": addr.get("PostalCode"),
						}
				customer_dict["addresses"] = addresses

			result.append(customer_dict)

		_dbg("get_quickbooks_customers_json:done", {"count": len(result)})
		return json.dumps(result, indent=2, default=str)

	except Exception as e:
		_dbg("get_quickbooks_customers_json:error", {"error": str(e)})
		raise


@frappe.whitelist()
def get_customers_json_api(source="erpnext", include_addresses=True):
	"""
	API endpoint to get all customers as JSON.
	Can be called via REST API or from Python.

	Args:
	    source (str): "erpnext" or "quickbooks"
	    include_addresses (bool): Whether to include address details

	Returns:
	    dict: JSON response with customers data
	"""
	try:
		if source == "quickbooks":
			# Get QuickBooks client
			from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

			quickbooks_settings = frappe.get_doc("Quickbooks Settings")
			quickbooks_obj = _create_quickbooks_client(quickbooks_settings)
			json_data = get_quickbooks_customers_json(quickbooks_obj, include_addresses=include_addresses)
		else:
			json_data = get_erpnext_customers_json(include_addresses=include_addresses)

		return {
			"success": True,
			"source": source,
			"count": len(json.loads(json_data)),
			"data": json.loads(json_data),
		}
	except Exception as e:
		frappe.log_error(f"Error getting customers JSON: {e!s}", "get_customers_json_api")
		return {
			"success": False,
			"error": str(e),
			"source": source,
		}
