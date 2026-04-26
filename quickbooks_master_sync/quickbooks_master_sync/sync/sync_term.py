import frappe
from frappe import _

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_status
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import query_with_pagination, save_qb_data_to_json


def _dbg(event, payload=None):
	"""Debug logging helper for term sync"""
	_dbg_common("sync_term", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_term"):
	"""Wrapper for sync_term module"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		module_name=section,
	)


def sync_term(quickbooks_obj, qb_company=None):
	"""Sync QuickBooks Term entities to ERPNext Payment Terms Template

	Args:
	    quickbooks_obj: QuickBooks API client instance
	    qb_company: ERPNext company name (optional)

	Returns:
	    dict: Statistics with created, updated, skipped, failed counts
	"""

	# Get company
	if not qb_company:
		qb_company = _get_quickbooks_company(quickbooks_obj, section="sync_term")

	if not qb_company:
		_dbg("sync_term:no_company", {"message": "No company found, cannot sync terms"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	# Fetch all terms from QuickBooks
	term_query = "SELECT * FROM Term"
	term_list = query_with_pagination(
		quickbooks_obj,
		term_query,
		"Term",
		module_name="sync_term",
		data_name="term_list",
	)

	if not term_list:
		_dbg("sync_term:no_terms", {"message": "No terms found in QuickBooks"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	_dbg("sync_term:start", {"total_terms": len(term_list), "company": qb_company})

	# Process each term
	for qb_term in term_list:
		try:
			result = _sync_single_term(qb_term, qb_company)
			stats[result] += 1
		except Exception as e:
			stats["failed"] += 1
			_dbg(
				"sync_term:error",
				{
					"term_id": qb_term.get("Id"),
					"term_name": qb_term.get("Name"),
					"error": str(e),
					"error_type": type(e).__name__,
				},
			)
			qb_log_error(
				title=_("Failed to Sync QuickBooks Term"),
				status="Error",
				method="sync_term",
				message=_("Failed to sync term '{0}' (ID: {1}): {2}").format(
					qb_term.get("Name"), qb_term.get("Id"), str(e)
				),
				module="sync_term",
				request_data=qb_term,
			)

	frappe.db.commit()  # nosemgrep

	_dbg("sync_term:complete", stats)

	# Log summary
	summary_msg = _("Term sync completed. Created: {0}, Updated: {1}, Skipped: {2}, Failed: {3}").format(
		stats.get("created", 0),
		stats.get("updated", 0),
		stats.get("skipped", 0),
		stats.get("failed", 0),
	)

	if stats.get("failed", 0) > 0:
		qb_log_error(
			title=_("Term Sync Completed with Errors"),
			status="Error",
			method="sync_term",
			message=summary_msg,
			module="sync_term",
			request_data=stats,
		)
	else:
		qb_log_status(
			title=_("Term Sync Complete"),
			status="Success",
			method="sync_term",
			message=summary_msg,
			module="sync_term",
			request_data=stats,
		)

	return stats


def _sync_single_term(qb_term, qb_company):
	"""Sync a single QuickBooks Term to ERPNext Payment Terms Template

	Args:
	    qb_term: QuickBooks Term object
	    qb_company: ERPNext company name

	Returns:
	    str: Result status - "created", "updated", or "skipped"
	"""

	term_id = qb_term.get("Id")
	term_name = qb_term.get("Name")
	is_active = qb_term.get("Active", True)
	due_days = qb_term.get("DueDays", 0)
	discount_percent = qb_term.get("DiscountPercent", 0)
	discount_days = qb_term.get("DiscountDays", 0)

	# Make unique QB ID with company prefix
	unique_term_id = make_unique_qb_id(term_id, qb_company)

	_dbg(
		"sync_single_term:processing",
		{
			"term_id": term_id,
			"unique_term_id": unique_term_id,
			"term_name": term_name,
			"is_active": is_active,
			"due_days": due_days,
			"discount_percent": discount_percent,
			"discount_days": discount_days,
		},
	)

	# Check if payment terms template already exists
	existing_template = frappe.db.get_value(
		"Payment Terms Template", {"quickbooks_term_id": unique_term_id}, "name"
	)

	if existing_template:
		# Update existing template
		doc = frappe.get_doc("Payment Terms Template", existing_template)

		# Track if anything changed
		changed = False

		if doc.template_name != term_name:
			doc.template_name = term_name
			changed = True

		# Update payment terms details (credit days and discount)
		if doc.terms:
			# Update first term row
			term_row = doc.terms[0]
			if term_row.credit_days != due_days:
				term_row.credit_days = due_days
				changed = True
			if discount_percent > 0:
				if term_row.discount != discount_percent:
					term_row.discount = discount_percent
					changed = True
				if term_row.discount_validity_based_on != "Day(s) after invoice date":
					term_row.discount_validity_based_on = "Day(s) after invoice date"
					changed = True
				if term_row.discount_validity != discount_days:
					term_row.discount_validity = discount_days
					changed = True

		if changed:
			doc.save()
			_dbg(
				"sync_single_term:updated",
				{
					"term_id": term_id,
					"template": doc.name,
					"changes": "Updated template fields",
				},
			)
			return "updated"
		else:
			_dbg(
				"sync_single_term:skipped",
				{
					"term_id": term_id,
					"template": doc.name,
					"reason": "No changes needed",
				},
			)
			return "skipped"
	else:
		# Create new payment terms template
		doc = frappe.get_doc(
			{
				"doctype": "Payment Terms Template",
				"template_name": term_name,
				"quickbooks_term_id": unique_term_id,
				"terms": [
					{
						"doctype": "Payment Terms Template Detail",
						"invoice_portion": 100,
						"credit_days_based_on": "Day(s) after invoice date",
						"credit_days": due_days or 0,
						"discount_type": "Percentage" if discount_percent > 0 else None,
						"discount": discount_percent if discount_percent > 0 else 0,
						"discount_validity_based_on": (
							"Day(s) after invoice date" if discount_percent > 0 else None
						),
						"discount_validity": (discount_days if discount_percent > 0 else 0),
					}
				],
			}
		)
		doc.insert()

		_dbg(
			"sync_single_term:created",
			{
				"term_id": term_id,
				"template": doc.name,
				"due_days": due_days,
				"discount": discount_percent,
			},
		)

		return "created"
