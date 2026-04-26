import frappe
from frappe import _

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_status
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

from .sync_utils import (
	_dbg as _dbg_common,
)
from .sync_utils import (
	_get_quickbooks_company as _get_quickbooks_company_base,
)
from .sync_utils import (
	query_with_pagination,
	save_qb_data_to_json,
)


def _dbg(event, payload=None):
	"""Debug logging helper for taxagency sync"""
	_dbg_common("sync_taxagency", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_taxagency"):
	"""Wrapper for sync_taxagency module"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		module_name=section,
	)


def _ensure_tax_agency_supplier_group():
	"""Ensure Tax Agency supplier group exists"""
	if not frappe.db.exists("Supplier Group", "Tax Agency"):
		_dbg("ensure_tax_agency_supplier_group:creating", {"group_name": "Tax Agency"})

		# Get parent supplier group
		parent_group = frappe.db.get_value("Supplier Group", {"is_group": 1}, "name", order_by="lft")

		if not parent_group:
			parent_group = "All Supplier Groups"

		try:
			frappe.get_doc(
				{
					"doctype": "Supplier Group",
					"supplier_group_name": "Tax Agency",
					"is_group": 0,
					"parent_supplier_group": parent_group,
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()  # nosemgrep

			_dbg(
				"ensure_tax_agency_supplier_group:created",
				{"group_name": "Tax Agency", "parent": parent_group},
			)
		except Exception as e:
			_dbg("ensure_tax_agency_supplier_group:error", {"error": str(e)})
			# If creation fails, just use default group
			pass


def sync_taxagency(quickbooks_obj, qb_company=None):
	"""Sync QuickBooks TaxAgency entities to ERPNext Suppliers

	Args:
	    quickbooks_obj: QuickBooks API client instance
	    qb_company: ERPNext company name (optional)

	Returns:
	    dict: Statistics with created, updated, skipped, failed counts
	"""

	# Get company
	if not qb_company:
		qb_company = _get_quickbooks_company(quickbooks_obj, section="sync_taxagency")

	if not qb_company:
		_dbg("sync_taxagency:no_company", {"message": "No company found, cannot sync tax agencies"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	# Ensure Tax Agency supplier group exists
	_ensure_tax_agency_supplier_group()

	# Fetch all tax agencies from QuickBooks
	taxagency_query = "SELECT * FROM TaxAgency"
	taxagency_list = query_with_pagination(
		quickbooks_obj, taxagency_query, "TaxAgency", module_name="sync_taxagency", data_name="taxagency_list"
	)

	if not taxagency_list:
		_dbg("sync_taxagency:no_taxagencies", {"message": "No tax agencies found in QuickBooks"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	_dbg("sync_taxagency:start", {"total_taxagencies": len(taxagency_list), "company": qb_company})

	# Process each tax agency
	for qb_taxagency in taxagency_list:
		try:
			result = _sync_single_taxagency(qb_taxagency, qb_company)
			stats[result] += 1
		except Exception as e:
			stats["failed"] += 1
			_dbg(
				"sync_taxagency:error",
				{
					"taxagency_id": qb_taxagency.get("Id"),
					"taxagency_name": qb_taxagency.get("DisplayName"),
					"error": str(e),
					"error_type": type(e).__name__,
				},
			)
			qb_log_error(
				title=_("Failed to Sync QuickBooks Tax Agency"),
				status="Error",
				method="sync_taxagency",
				message=_("Failed to sync tax agency '{0}' (ID: {1}): {2}").format(
					qb_taxagency.get("DisplayName"), qb_taxagency.get("Id"), str(e)
				),
				module="sync_taxagency",
				request_data=qb_taxagency,
			)

	frappe.db.commit()  # nosemgrep

	_dbg("sync_taxagency:complete", stats)

	# Log summary
	summary_msg = _(
		"Tax agency sync completed. Created: {0}, Updated: {1}, Skipped: {2}, Failed: {3}"
	).format(
		stats.get("created", 0), stats.get("updated", 0), stats.get("skipped", 0), stats.get("failed", 0)
	)

	if stats.get("failed", 0) > 0:
		qb_log_error(
			title=_("Tax Agency Sync Completed with Errors"),
			status="Error",
			method="sync_taxagency",
			message=summary_msg,
			module="sync_taxagency",
			request_data=stats,
		)
	else:
		qb_log_status(
			title=_("Tax Agency Sync Complete"),
			status="Success",
			method="sync_taxagency",
			message=summary_msg,
			module="sync_taxagency",
			request_data=stats,
		)

	return stats


def _sync_single_taxagency(qb_taxagency, qb_company):
	"""Sync a single QuickBooks TaxAgency to ERPNext Supplier

	Args:
	    qb_taxagency: QuickBooks TaxAgency object
	    qb_company: ERPNext company name

	Returns:
	    str: Result status - "created", "updated", or "skipped"
	"""

	taxagency_id = qb_taxagency.get("Id")
	display_name = qb_taxagency.get("DisplayName")
	is_active = qb_taxagency.get("Active", True)
	tax_reg_num = qb_taxagency.get("TaxRegistrationNumber")

	# Make unique QB ID with company prefix
	unique_taxagency_id = make_unique_qb_id(taxagency_id, qb_company)

	_dbg(
		"sync_single_taxagency:processing",
		{
			"taxagency_id": taxagency_id,
			"unique_taxagency_id": unique_taxagency_id,
			"display_name": display_name,
			"is_active": is_active,
			"tax_reg_num": tax_reg_num,
		},
	)

	# Check if supplier already exists
	existing_supplier = frappe.db.get_value(
		"Supplier", {"quickbooks_taxagency_id": unique_taxagency_id}, "name"
	)

	# Determine supplier group - use Tax Agency if it exists, otherwise use default
	supplier_group = "Tax Agency"
	if not frappe.db.exists("Supplier Group", supplier_group):
		# Fallback to first available supplier group
		supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		if not supplier_group:
			supplier_group = "All Supplier Groups"

	if existing_supplier:
		# Update existing supplier
		doc = frappe.get_doc("Supplier", existing_supplier)

		# Track if anything changed
		changed = False

		if doc.supplier_name != display_name:
			doc.supplier_name = display_name
			changed = True

		# disabled: Active=False means disabled=True
		new_disabled = not is_active
		if doc.disabled != new_disabled:
			doc.disabled = new_disabled
			changed = True

		if tax_reg_num and doc.tax_id != tax_reg_num:
			doc.tax_id = tax_reg_num
			changed = True

		if changed:
			doc.save()
			_dbg(
				"sync_single_taxagency:updated",
				{"taxagency_id": taxagency_id, "supplier": doc.name, "changes": "Updated supplier fields"},
			)
			return "updated"
		else:
			_dbg(
				"sync_single_taxagency:skipped",
				{"taxagency_id": taxagency_id, "supplier": doc.name, "reason": "No changes needed"},
			)
			return "skipped"
	else:
		# Create new supplier
		doc = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": display_name,
				"supplier_group": supplier_group,
				"supplier_type": "Company",
				"disabled": not is_active,  # Inverted: Active=False means disabled=True
				"tax_id": tax_reg_num or "",
				"quickbooks_taxagency_id": unique_taxagency_id,
			}
		)
		doc.insert()

		_dbg(
			"sync_single_taxagency:created",
			{"taxagency_id": taxagency_id, "supplier": doc.name, "supplier_group": supplier_group},
		)

		return "created"
