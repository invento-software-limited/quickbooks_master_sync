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
	"""Debug logging helper for class sync"""
	_dbg_common("sync_class", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_class"):
	"""Wrapper for sync_class module"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		module_name=section,
	)


def sync_class(quickbooks_obj, qb_company=None):
	"""Sync QuickBooks Classes to ERPNext Cost Centers

	Args:
	    quickbooks_obj: QuickBooks API client instance
	    qb_company: ERPNext company name (optional)

	Returns:
	    dict: Statistics with created, updated, skipped, failed counts
	"""

	# Get company
	if not qb_company:
		qb_company = _get_quickbooks_company(quickbooks_obj, section="sync_class")

	if not qb_company:
		_dbg("sync_class:no_company", {"message": "No company found, cannot sync classes"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	# Fetch all classes from QuickBooks
	class_query = "SELECT * FROM Class"
	class_list = query_with_pagination(
		quickbooks_obj, class_query, "Class", module_name="sync_class", data_name="class_list"
	)

	if not class_list:
		_dbg("sync_class:no_classes", {"message": "No classes found in QuickBooks"})
		return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	# Sort classes by FullyQualifiedName to ensure parents are processed before children
	# This handles nested classes correctly regardless of depth
	class_list.sort(key=lambda x: x.get("FullyQualifiedName", ""))

	stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	_dbg("sync_class:start", {"total_classes": len(class_list), "company": qb_company})

	# Process classes in order
	for qb_class in class_list:
		try:
			result = _sync_single_class(qb_class, qb_company, quickbooks_obj)
			stats[result] += 1
		except Exception as e:
			stats["failed"] += 1
			_dbg(
				"sync_class:error",
				{
					"class_id": qb_class.get("Id"),
					"class_name": qb_class.get("Name"),
					"error": str(e),
					"error_type": type(e).__name__,
				},
			)
			qb_log_error(
				title=_("Failed to Sync QuickBooks Class"),
				status="Error",
				method="sync_class",
				message=_("Failed to sync class '{0}' (ID: {1}): {2}").format(
					qb_class.get("Name"), qb_class.get("Id"), str(e)
				),
				module="sync_class",
				request_data=qb_class,
			)

	frappe.db.commit()

	_dbg("sync_class:complete", stats)

	# Log summary
	summary_msg = _("Class sync completed. Created: {0}, Updated: {1}, Skipped: {2}, Failed: {3}").format(
		stats.get("created", 0), stats.get("updated", 0), stats.get("skipped", 0), stats.get("failed", 0)
	)

	if stats.get("failed", 0) > 0:
		qb_log_error(
			title=_("Class Sync Completed with Errors"),
			status="Error",
			method="sync_class",
			message=summary_msg,
			module="sync_class",
			request_data=stats,
		)
	else:
		qb_log_status(
			title=_("Class Sync Complete"),
			status="Success",
			method="sync_class",
			message=summary_msg,
			module="sync_class",
			request_data=stats,
		)

	return stats


def _sync_single_class(qb_class, qb_company, quickbooks_obj):
	"""Sync a single QuickBooks Class to ERPNext Cost Center

	Args:
	    qb_class: QuickBooks Class object
	    qb_company: ERPNext company name
	    quickbooks_obj: QuickBooks API client instance

	Returns:
	    str: Result status - "created", "updated", or "skipped"
	"""

	class_id = qb_class.get("Id")
	class_name = qb_class.get("Name")
	fully_qualified_name = qb_class.get("FullyQualifiedName", class_name)
	is_subclass = qb_class.get("SubClass", False)
	is_active = qb_class.get("Active", True)
	parent_ref = qb_class.get("ParentRef", {})

	# Make unique QB ID with company prefix
	unique_class_id = make_unique_qb_id(class_id, qb_company)

	_dbg(
		"sync_single_class:processing",
		{
			"class_id": class_id,
			"unique_class_id": unique_class_id,
			"class_name": class_name,
			"fully_qualified_name": fully_qualified_name,
			"is_subclass": is_subclass,
			"is_active": is_active,
		},
	)

	# Check if cost center already exists
	existing_cc = frappe.db.get_value(
		"Cost Center", {"quickbooks_class_id": unique_class_id, "company": qb_company}, "name"
	)

	# Determine parent cost center
	parent_cost_center = None
	if parent_ref and parent_ref.get("value"):
		parent_qb_id = parent_ref.get("value")
		unique_parent_id = make_unique_qb_id(parent_qb_id, qb_company)
		parent_cost_center = frappe.db.get_value(
			"Cost Center", {"quickbooks_class_id": unique_parent_id, "company": qb_company}, "name"
		)

		if not parent_cost_center:
			_dbg(
				"sync_single_class:parent_not_found",
				{
					"class_id": class_id,
					"parent_qb_id": parent_qb_id,
					"unique_parent_id": unique_parent_id,
					"message": "Parent class not found, will use company root",
				},
			)

	# If no parent found, use company's root cost center
	if not parent_cost_center:
		parent_cost_center = frappe.db.get_value(
			"Cost Center",
			{"company": qb_company, "is_group": 1, "parent_cost_center": ["is", "not set"]},
			"name",
		)

		# If still no parent, get the company's main cost center
		if not parent_cost_center:
			parent_cost_center = frappe.db.get_value(
				"Cost Center", {"company": qb_company, "cost_center_name": qb_company}, "name"
			)

	if existing_cc:
		# Update existing cost center
		doc = frappe.get_doc("Cost Center", existing_cc)

		# Track if anything changed
		changed = False

		if doc.cost_center_name != fully_qualified_name:
			doc.cost_center_name = fully_qualified_name
			changed = True

		# is_group: SubClass=False means it's a parent (is_group=True)
		# SubClass=True means it's a child (is_group=False)
		new_is_group = not is_subclass
		if doc.is_group != new_is_group:
			doc.is_group = new_is_group
			changed = True

		# disabled: Active=False means disabled=True
		new_disabled = not is_active
		if doc.disabled != new_disabled:
			doc.disabled = new_disabled
			changed = True

		if parent_cost_center and doc.parent_cost_center != parent_cost_center:
			doc.parent_cost_center = parent_cost_center
			changed = True

		if changed:
			doc.save()
			_dbg(
				"sync_single_class:updated",
				{"class_id": class_id, "cost_center": doc.name, "changes": "Updated cost center fields"},
			)
			return "updated"
		else:
			_dbg(
				"sync_single_class:skipped",
				{"class_id": class_id, "cost_center": doc.name, "reason": "No changes needed"},
			)
			return "skipped"
	else:
		# Create new cost center
		doc = frappe.get_doc(
			{
				"doctype": "Cost Center",
				"cost_center_name": fully_qualified_name,
				"company": qb_company,
				"parent_cost_center": parent_cost_center,
				"is_group": not is_subclass,  # Inverted: SubClass=False means is_group=True
				"disabled": not is_active,  # Inverted: Active=False means disabled=True
				"quickbooks_class_id": unique_class_id,
			}
		)
		doc.insert()

		_dbg(
			"sync_single_class:created",
			{
				"class_id": class_id,
				"cost_center": doc.name,
				"parent": parent_cost_center,
				"is_group": doc.is_group,
			},
		)

		return "created"
