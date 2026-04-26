import frappe
from frappe import _

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_exception, qb_log_status
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

from .sync_utils import _dbg as _dbg_common
from .sync_utils import query_with_pagination, save_qb_data_to_json


def _dbg(event, payload=None):
	"""Debug logging helper for payment method sync"""
	_dbg_common("sync_payment_method", event, payload)


def sync_payment_method(quickbooks_obj):
	"""Fetch PaymentMethod data from QuickBooks and sync to ERPNext Mode of Payment.

	Payment Methods from QuickBooks are synced to ERPNext's Mode of Payment doctype.
	The QuickBooks payment method ID is stored in the quickbooks_payment_method_id field
	for mapping purposes. When creating payment entries, the Mode of Payment can be
	looked up by QuickBooks ID and used in the payment entry.
	"""

	payment_method_query = """SELECT Id, Name, Type, Active FROM PaymentMethod"""

	# Use pagination helper to fetch all payment methods
	get_qb_payment_methods = query_with_pagination(
		quickbooks_obj,
		payment_method_query,
		"PaymentMethod",
		module_name="sync_payment_method",
		data_name="payment_methods_list",
	)

	# Save raw QuickBooks payment method data to JSON file for debugging
	try:
		save_qb_data_to_json(
			data=get_qb_payment_methods,
			data_name="payment_methods_list",
			module_name="sync_payment_method",
			full_response={"QueryResponse": {"PaymentMethod": get_qb_payment_methods}},
		)
	except Exception as e:
		_dbg("sync_payment_method:debug_save_error", {"error": str(e)})
		# Don't fail the sync if debug file save fails

	stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

	# Generate unique ID immediately

	company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)

	# Initialize generic Import Tracker
	from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker

	tracker = ImportTracker(
		len(get_qb_payment_methods),
		"qb_payment_method_import.log",
		module_name="PAYMENT_METHOD",
		company=company,
	)

	for qb_payment_method in get_qb_payment_methods:
		try:
			qb_id = str(qb_payment_method.get("Id", ""))
			qb_name = qb_payment_method.get("Name", "")
			qb_payment_method.get("Type", "")
			qb_payment_method.get("Active", True)

			unique_qb_id = make_unique_qb_id(qb_id, company) if company else qb_id

			# Log start of processing
			tracker_id = f"{unique_qb_id} ({qb_name})"
			tracker.log_processing("QuickBooks", tracker_id)

			if not qb_id or not qb_name:
				stats["skipped"] += 1
				tracker.log_skip("QuickBooks", tracker_id, "invalid_data")
				_dbg(
					"sync_payment_method:skipped_invalid",
					{
						"payment_method": qb_payment_method,
					},
				)
				continue

			# Check if Mode of Payment already exists with this QuickBooks ID
			# Check if Mode of Payment already exists with this QuickBooks ID
			# unique_qb_id already created

			existing_mode = frappe.db.get_value(
				"Mode of Payment", {"quickbooks_payment_method_id": unique_qb_id}, "name", as_dict=True
			)

			if existing_mode:
				# Update existing Mode of Payment
				try:
					mode_doc = frappe.get_doc("Mode of Payment", existing_mode.name)
					# Update name if it changed
					if mode_doc.mode_of_payment != qb_name:
						mode_doc.mode_of_payment = qb_name
					# Ensure QuickBooks ID is set (use unique QB ID)
					if hasattr(mode_doc, "quickbooks_payment_method_id"):
						mode_doc.quickbooks_payment_method_id = unique_qb_id
					mode_doc.save()
					frappe.db.commit()  # nosemgrep
					stats["updated"] += 1
					_dbg(
						"sync_payment_method:updated",
						{
							"qb_id": qb_id,
							"name": qb_name,
							"mode_of_payment": existing_mode.name,
						},
					)
					tracker.log_success("QuickBooks", tracker_id, "UPDATED")
				except Exception as e:
					stats["failed"] += 1
					_dbg(
						"sync_payment_method:update_error",
						{
							"qb_id": qb_id,
							"error": str(e),
						},
					)
					qb_log_exception(
						method="sync_payment_method",
						err=e,
						request_data=qb_payment_method,
						module="sync_payment_method",
					)
					tracker.log_error("QuickBooks", tracker_id, str(e))
			else:
				# Check if Mode of Payment with same name exists (but different QB ID)
				existing_by_name = frappe.db.get_value(
					"Mode of Payment", {"mode_of_payment": qb_name}, "name", as_dict=True
				)

				if existing_by_name:
					# Update existing Mode of Payment with same name to add QB ID
					try:
						mode_doc = frappe.get_doc("Mode of Payment", existing_by_name.name)
						if hasattr(mode_doc, "quickbooks_payment_method_id"):
							mode_doc.quickbooks_payment_method_id = unique_qb_id
						mode_doc.save()
						frappe.db.commit()  # nosemgrep
						stats["updated"] += 1
						_dbg(
							"sync_payment_method:updated_by_name",
							{
								"qb_id": qb_id,
								"name": qb_name,
								"mode_of_payment": existing_by_name.name,
							},
						)
						tracker.log_success("QuickBooks", tracker_id, "UPDATED_BY_NAME")
					except Exception as e:
						stats["failed"] += 1
						_dbg(
							"sync_payment_method:update_by_name_error",
							{
								"qb_id": qb_id,
								"name": qb_name,
								"error": str(e),
							},
						)
						qb_log_exception(
							method="sync_payment_method",
							err=e,
							request_data=qb_payment_method,
							module="sync_payment_method",
						)
						tracker.log_error("QuickBooks", tracker_id, str(e))
				else:
					# Create new Mode of Payment
					try:
						mode_doc = frappe.new_doc("Mode of Payment")
						mode_doc.mode_of_payment = qb_name
						if hasattr(mode_doc, "quickbooks_payment_method_id"):
							mode_doc.quickbooks_payment_method_id = unique_qb_id
						mode_doc.insert()
						frappe.db.commit()  # nosemgrep
						stats["created"] += 1
						_dbg(
							"sync_payment_method:created",
							{
								"qb_id": qb_id,
								"name": qb_name,
								"mode_of_payment": mode_doc.name,
							},
						)
						tracker.log_success("QuickBooks", tracker_id, "CREATED")
					except Exception as e:
						stats["failed"] += 1
						_dbg(
							"sync_payment_method:create_error",
							{
								"qb_id": qb_id,
								"name": qb_name,
								"error": str(e),
							},
						)
						qb_log_exception(
							method="sync_payment_method",
							err=e,
							request_data=qb_payment_method,
							module="sync_payment_method",
						)
						tracker.log_error("QuickBooks", tracker_id, str(e))
		except Exception as e:
			stats["failed"] += 1
			_dbg(
				"sync_payment_method:error",
				{
					"error": str(e),
					"payment_method": qb_payment_method,
				},
			)
			qb_log_exception(
				method="sync_payment_method",
				err=e,
				request_data=qb_payment_method,
				module="sync_payment_method",
			)
			tracker.log_error(
				"QuickBooks", f"{qb_payment_method.get('Id')} ({qb_payment_method.get('Name')})", str(e)
			)

	# Log summary
	summary_msg = _(
		"Payment Method sync finished. "
		"Total: {0}, Created: {1}, Updated: {2}, Skipped: {3}, Failed: {4}. "
		"Payment methods are synced to Mode of Payment doctype."
	).format(
		len(get_qb_payment_methods) if get_qb_payment_methods else 0,
		stats.get("created", 0),
		stats.get("updated", 0),
		stats.get("skipped", 0),
		stats.get("failed", 0),
	)

	if stats.get("failed", 0) > 0:
		from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error

		qb_log_error(
			title=_("Payment Method Sync Completed with Errors"),
			status="Error",
			method="sync_payment_method",
			message=summary_msg,
			module="sync_payment_method",
			request_data=stats,
		)
	else:
		qb_log_status(
			title=_("Payment Method Sync Complete"),
			status="Success",
			method="sync_payment_method",
			message=summary_msg,
			module="sync_payment_method",
			request_data=stats,
		)

	# Log final summary
	tracker.log_summary()

	return stats
