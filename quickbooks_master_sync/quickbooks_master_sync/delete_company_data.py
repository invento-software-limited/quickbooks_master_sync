"""
Delete Company Data and Related Entries for QuickBooks Migration

This module provides functionality to clean up all data synced from QuickBooks
for a specific company. It deletes all related transactions and master data
in the correct order to maintain referential integrity.

Usage:
    from quickbooks_master_sync.quickbooks_master_sync.delete_company_data import delete_company_data

    # Delete all data for a company
    result = delete_company_data(company_name="ABC Company", dry_run=True)

    # To actually delete (use with caution!)
    result = delete_company_data(company_name="ABC Company", dry_run=False)
"""

import frappe
from frappe import _
from frappe.utils import cint

from .utils.logging import qb_log_error, qb_log_status


def _dbg(event, payload=None):
	"""Debug logging helper for company data deletion"""
	from .sync.sync_utils import _dbg as _dbg_common

	_dbg_common("delete_company_data", event, payload)


@frappe.whitelist()
def delete_company_data(
	company_name: str | None = None,
	dry_run: bool | int | str = True,
	delete_company: bool | int | str = False,
	clear_stock: bool | int | str = False,
):
	"""
	Delete all QuickBooks synced data for a specific company.

	Args:
	    company_name (str): Name of the company to delete data for
	    dry_run (bool): If True, only report what would be deleted without actually deleting
	    delete_company (bool): If True, also delete the Company doctype itself
	    clear_stock (bool): If True, clear stock from warehouses before deletion

	Returns:
	    dict: Statistics about deleted/would-be-deleted records

	Raises:
	    frappe.ValidationError: If company doesn't exist or other validation errors
	"""
	# Convert parameters to boolean if passed as string from web request
	dry_run = cint(dry_run) if isinstance(dry_run, str) else bool(dry_run)
	delete_company = cint(delete_company) if isinstance(delete_company, str) else bool(delete_company)
	clear_stock = cint(clear_stock) if isinstance(clear_stock, str) else bool(clear_stock)

	_dbg(
		"delete_company_data:start",
		{
			"company": company_name,
			"dry_run": dry_run,
			"delete_company": delete_company,
			"clear_stock": clear_stock,
		},
	)

	# Validate company exists
	if not company_name:
		frappe.throw(_("Company name is required"))

	if not frappe.db.exists("Company", company_name):
		frappe.throw(_("Company '{0}' does not exist").format(company_name))

	stats = {
		"company": company_name,
		"dry_run": dry_run,
		"deleted": {},
		"errors": [],
		"total_records": 0,
		"warnings": [],
	}

	try:
		# Clear stock if requested (before deleting warehouses)
		if clear_stock and not dry_run:
			_dbg("clear_stock:start", {"company": company_name})
			_clear_warehouse_stock(company_name, stats)
			_dbg("clear_stock:complete", {"company": company_name})

		# Delete in order: Transactions first, then Masters, then Company

		# 1. Delete GL Entries and Ledger Entries first
		_dbg("step1_gl_entries:start", {"company": company_name, "dry_run": dry_run})

		_dbg("delete_doctype:start", {"doctype": "Stock Ledger Entry", "company": company_name})
		_delete_stock_ledger_entries(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Stock Ledger Entry", "count": stats["deleted"].get("Stock Ledger Entry", 0)},
		)

		_dbg("delete_doctype:start", {"doctype": "Stock Reconciliation", "company": company_name})
		_delete_stock_reconciliations(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Stock Reconciliation", "count": stats["deleted"].get("Stock Reconciliation", 0)},
		)

		_dbg("delete_doctype:start", {"doctype": "Payment Ledger Entry", "company": company_name})
		_delete_payment_ledger_entries(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Payment Ledger Entry", "count": stats["deleted"].get("Payment Ledger Entry", 0)},
		)

		# Commit after Stock Ledger Entries and Stock Reconciliations
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_ledger_entries", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Journal Entry", "company": company_name})
		_delete_journal_entries(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Journal Entry", "count": stats["deleted"].get("Journal Entry", 0)},
		)

		# Commit after GL entries to ensure they're saved
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_journal_entries", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Payment Entry", "company": company_name})
		_delete_payment_entries(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Payment Entry", "count": stats["deleted"].get("Payment Entry", 0)},
		)

		# Commit after Payment Ledger Entries and Journal Entries
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_payment_entries", {"company": company_name})

		_dbg("step1_gl_entries:complete", {"company": company_name})

		# 2. Delete Invoices and Transaction Documents
		_dbg("step2_invoices:start", {"company": company_name, "dry_run": dry_run})

		_dbg("delete_doctype:start", {"doctype": "Sales Invoice", "company": company_name})
		_delete_sales_invoices(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Sales Invoice", "count": stats["deleted"].get("Sales Invoice", 0)},
		)
		# Commit after Sales Invoices
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_sales_invoices", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Purchase Invoice", "company": company_name})
		_delete_purchase_invoices(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Purchase Invoice", "count": stats["deleted"].get("Purchase Invoice", 0)},
		)
		# Commit after Purchase Invoices
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_purchase_invoices", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Sales Order", "company": company_name})
		_delete_sales_orders(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Sales Order", "count": stats["deleted"].get("Sales Order", 0)},
		)

		# Commit after Sales Orders
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_sales_orders", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Purchase Order", "company": company_name})
		_delete_purchase_orders(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Purchase Order", "count": stats["deleted"].get("Purchase Order", 0)},
		)
		# Commit after Purchase Orders
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_purchase_orders", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Quotation", "company": company_name})
		_delete_quotations(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete", {"doctype": "Quotation", "count": stats["deleted"].get("Quotation", 0)}
		)
		# Commit after Quotations
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_quotations", {"company": company_name})

		_dbg("delete_doctype:start", {"doctype": "Delivery Note", "company": company_name})
		_delete_delivery_notes(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Delivery Note", "count": stats["deleted"].get("Delivery Note", 0)},
		)

		_dbg("delete_doctype:start", {"doctype": "Purchase Receipt", "company": company_name})
		_delete_purchase_receipts(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Purchase Receipt", "count": stats["deleted"].get("Purchase Receipt", 0)},
		)

		# Commit after all invoices/transactions to ensure they're fully deleted
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_all_transactions", {"company": company_name})

		_dbg("step2_invoices:complete", {"company": company_name})

		# 3. Delete Master Documents (linked to company)
		# _delete_accounts(company_name, stats, dry_run)
		# Note: Warehouses and Cost Centers are company default data, not QuickBooks specific
		# _delete_warehouses(company_name, stats, dry_run)
		# _delete_cost_centers(company_name, stats, dry_run)

		# 4. Delete Master Documents (with quickbooks_*_id fields)
		_dbg("step3_master_data:start", {"company": company_name, "dry_run": dry_run})

		_dbg("delete_doctype:start", {"doctype": "Item", "company": company_name})
		_delete_items(company_name, stats, dry_run)
		_dbg("delete_doctype:complete", {"doctype": "Item", "count": stats["deleted"].get("Item", 0)})

		_dbg("delete_doctype:start", {"doctype": "Customer", "company": company_name})
		_delete_customers(company_name, stats, dry_run)
		_dbg("delete_doctype:complete", {"doctype": "Customer", "count": stats["deleted"].get("Customer", 0)})

		_dbg("delete_doctype:start", {"doctype": "Supplier", "company": company_name})
		_delete_suppliers(company_name, stats, dry_run)
		_dbg("delete_doctype:complete", {"doctype": "Supplier", "count": stats["deleted"].get("Supplier", 0)})

		_dbg("delete_doctype:start", {"doctype": "Employee", "company": company_name})
		_delete_employees(company_name, stats, dry_run)
		_dbg("delete_doctype:complete", {"doctype": "Employee", "count": stats["deleted"].get("Employee", 0)})

		# Commit after master data deletions
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_master_data", {"company": company_name})

		_dbg("step3_master_data:complete", {"company": company_name})

		# 5. Delete related child doctypes (Addresses and Contacts) AFTER all transactions
		_dbg("step4_child_doctypes:start", {"company": company_name, "dry_run": dry_run})

		_dbg("delete_doctype:start", {"doctype": "Address", "company": company_name})
		_delete_addresses(company_name, stats, dry_run)
		address_count = stats["deleted"].get("Address", 0)
		address_disabled = stats["deleted"].get("Address (Disabled)", 0)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Address", "count": address_count, "disabled": address_disabled},
		)

		_dbg("delete_doctype:start", {"doctype": "Contact", "company": company_name})
		_delete_contacts(company_name, stats, dry_run)
		contact_count = stats["deleted"].get("Contact", 0)
		contact_disabled = stats["deleted"].get("Contact (Disabled)", 0)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Contact", "count": contact_count, "disabled": contact_disabled},
		)

		# Commit after child doctype deletions
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:after_child_doctypes", {"company": company_name})

		_dbg("step4_child_doctypes:complete", {"company": company_name})

		# 6. Delete QuickBooks specific data
		_dbg("step5_qb_data:start", {"company": company_name, "dry_run": dry_run})

		_dbg("delete_doctype:start", {"doctype": "Quickbooks Log", "company": company_name})
		_delete_quickbooks_logs(company_name, stats, dry_run)
		_dbg(
			"delete_doctype:complete",
			{"doctype": "Quickbooks Log", "count": stats["deleted"].get("Quickbooks Log", 0)},
		)

		_dbg("delete_debug_files:start", {"company": company_name})
		_delete_debug_log_files(company_name, stats, dry_run)
		_dbg("delete_debug_files:complete", {"count": stats["deleted"].get("Debug Log Files", 0)})
		# Note: Tax Accounts are company default data, not QuickBooks specific
		# _delete_tax_accounts(company_name, stats, dry_run)

		_dbg("step5_qb_data:complete", {"company": company_name})

		# 7. Finally delete the Company itself if requested
		if delete_company:
			_dbg("delete_doctype:start", {"doctype": "Company", "company": company_name})
			_delete_company_doc(company_name, stats, dry_run)
			_dbg(
				"delete_doctype:complete", {"doctype": "Company", "count": stats["deleted"].get("Company", 0)}
			)

		# Commit all deletions before logging to avoid hitting write limit
		if not dry_run:
			frappe.db.commit()  # nosemgrep
			_dbg("commit:final", {"company": company_name})

		# Calculate total - handle case where some values might be lists instead of integers
		total = 0
		for value in stats["deleted"].values():
			if isinstance(value, list | tuple):
				total += len(value)
			elif isinstance(value, int | float):
				total += int(value)
			else:
				# Try to convert to int, default to 0 if not possible
				try:
					total += int(value)
				except (ValueError, TypeError):
					total += 0
		stats["total_records"] = total

		# Log success
		if dry_run:
			qb_log_status(
				title="Company Data Deletion - Dry Run Complete",
				status="Success",
				method="delete_company_data",
				message=_("Dry run completed. Would delete {0} records for company '{1}'").format(
					stats["total_records"], company_name
				),
				module="delete_company_data",
			)
		else:
			qb_log_status(
				title="Company Data Deletion Complete",
				status="Success",
				method="delete_company_data",
				message=_("Successfully deleted {0} records for company '{1}'").format(
					stats["total_records"], company_name
				),
				module="delete_company_data",
			)

			# Commit the log entry
			frappe.db.commit()  # nosemgrep

		_dbg("delete_company_data:complete", stats)

		return stats

	except Exception as e:
		frappe.db.rollback()
		error_msg = str(e)
		stats["errors"].append(error_msg)

		qb_log_error(
			title="Company Data Deletion Error",
			status="Error",
			method="delete_company_data",
			message=_("Error deleting company data: {0}\n\nTraceback:\n{1}").format(
				error_msg, frappe.get_traceback()
			),
			module="delete_company_data",
		)

		_dbg("delete_company_data:error", {"error": error_msg})
		raise


def _clear_warehouse_stock(company_name, stats):
	"""Clear stock from all warehouses for the company using Stock Reconciliation"""
	try:
		# Get all warehouses for the company
		warehouses = frappe.get_all("Warehouse", filters={"company": company_name}, pluck="name")

		if not warehouses:
			return

		# Get all items with stock in these warehouses
		items_with_stock = frappe.db.sql(
			"""
            SELECT DISTINCT b.item_code, b.warehouse
            FROM `tabBin` b
            WHERE b.warehouse IN %s
            AND (b.actual_qty != 0 OR b.reserved_qty != 0 OR b.ordered_qty != 0)
        """,
			(warehouses,),
			as_dict=True,
		)

		if not items_with_stock:
			_dbg("clear_warehouse_stock", {"message": "No stock to clear"})
			return

		# Create a Stock Reconciliation to zero out all stock
		sr = frappe.new_doc("Stock Reconciliation")
		sr.company = company_name
		sr.purpose = "Stock Reconciliation"
		sr.posting_date = frappe.utils.today()
		sr.posting_time = frappe.utils.nowtime()

		for item in items_with_stock:
			sr.append(
				"items",
				{"item_code": item.item_code, "warehouse": item.warehouse, "qty": 0, "valuation_rate": 0},
			)

		sr.flags.ignore_permissions = True
		sr.flags.ignore_mandatory = True
		sr.insert()
		sr.submit()

		stats["warnings"].append(
			f"Created Stock Reconciliation {sr.name} to clear stock from {len(items_with_stock)} items"
		)

		_dbg(
			"clear_warehouse_stock:created",
			{"stock_reconciliation": sr.name, "items_cleared": len(items_with_stock)},
		)

	except Exception as e:
		error_msg = f"Failed to clear warehouse stock: {e!s}"
		stats["errors"].append(error_msg)
		_dbg("clear_warehouse_stock:error", {"error": str(e)})


def _delete_stock_ledger_entries(company_name, stats, dry_run):
	"""Delete Stock Ledger Entries for the company"""
	doctype = "Stock Ledger Entry"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_payment_ledger_entries(company_name, stats, dry_run):
	"""Delete Payment Ledger Entries for the company"""
	doctype = "Payment Ledger Entry"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_stock_reconciliations(company_name, stats, dry_run):
	"""Delete Stock Reconciliations for the company"""
	doctype = "Stock Reconciliation"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_journal_entries(company_name, stats, dry_run):
	"""Delete Journal Entries for the company"""
	doctype = "Journal Entry"
	deleted_count = 0
	error_count = 0
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted - skip validation errors for missing parties
					if doc.docstatus == 1:
						try:
							doc.flags.ignore_validate = True
							doc.flags.ignore_mandatory = True
							doc.flags.ignore_links = True  # Ignore party validation
							doc.cancel()
						except Exception as cancel_error:
							# If cancel fails due to missing party/invalid reference, continue with delete anyway
							error_msg = str(cancel_error)
							if "Could not find Party" in error_msg or "Party" in error_msg:
								_dbg(
									f"delete_{doctype}:cancel_skip_party_error",
									{
										"name": name,
										"error": error_msg,
										"note": "Skipping cancel validation for missing party, will force delete",
									},
								)
							else:
								raise  # Re-raise if it's a different error

					# Force delete even if validation fails (for missing parties)
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					deleted_count += 1
				except Exception as e:
					error_count += 1
					error_msg = str(e)
					_dbg(f"delete_{doctype}:error", {"name": name, "error": error_msg})
					# Continue with next record even if this one fails

		stats["deleted"][doctype] = deleted_count
		if error_count > 0:
			stats["warnings"].append(
				f"{doctype}: {error_count} entries had errors during deletion (may have missing party references)"
			)
		_dbg(
			f"delete_{doctype}",
			{"count": count, "deleted": deleted_count, "errors": error_count, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_payment_entries(company_name, stats, dry_run):
	"""Delete Payment Entries for the company"""
	doctype = "Payment Entry"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_sales_invoices(company_name, stats, dry_run):
	"""Delete Sales Invoices for the company"""
	doctype = "Sales Invoice"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_purchase_invoices(company_name, stats, dry_run):
	"""Delete Purchase Invoices for the company"""
	doctype = "Purchase Invoice"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_sales_orders(company_name, stats, dry_run):
	"""Delete Sales Orders for the company"""
	doctype = "Sales Order"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_purchase_orders(company_name, stats, dry_run):
	"""Delete Purchase Orders for the company"""
	doctype = "Purchase Order"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_quotations(company_name, stats, dry_run):
	"""Delete Quotations for the company"""
	doctype = "Quotation"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_delivery_notes(company_name, stats, dry_run):
	"""Delete Delivery Notes for the company"""
	doctype = "Delivery Note"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_purchase_receipts(company_name, stats, dry_run):
	"""Delete Purchase Receipts for the company"""
	doctype = "Purchase Receipt"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					doc = frappe.get_doc(doctype, name)
					# Cancel if submitted
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_accounts(company_name, stats, dry_run):
	"""Delete Accounts for the company (excluding default/system accounts)"""
	doctype = "Account"
	try:
		# Only delete accounts that were synced from QuickBooks
		# Get accounts in order: leaf nodes first (is_group=0), then parent nodes
		leaf_accounts = frappe.get_all(
			doctype,
			filters={"company": company_name, "quickbooks_account_id": ["!=", ""], "is_group": 0},
			pluck="name",
		)

		group_accounts = frappe.get_all(
			doctype,
			filters={"company": company_name, "quickbooks_account_id": ["!=", ""], "is_group": 1},
			pluck="name",
			order_by="lft desc",  # Delete deepest nodes first
		)

		all_accounts = leaf_accounts + group_accounts
		count = len(all_accounts)
		deleted_count = 0
		skipped_count = 0

		if not dry_run and count > 0:
			# Delete leaf accounts first, then group accounts
			for name in all_accounts:
				try:
					# Check if account has transactions
					has_transactions = frappe.db.sql(
						"""
                        SELECT COUNT(*) as count
                        FROM `tabGL Entry`
                        WHERE account = %s
                        LIMIT 1
                    """,
						(name,),
					)[0][0]

					if has_transactions > 0:
						_dbg(
							f"delete_{doctype}:skip_has_transactions",
							{"name": name, "transaction_count": has_transactions},
						)
						skipped_count += 1
						continue

					# Check if account has child nodes
					has_children = frappe.db.count(doctype, {"parent_account": name})

					if has_children > 0:
						_dbg(
							f"delete_{doctype}:skip_has_children", {"name": name, "child_count": has_children}
						)
						skipped_count += 1
						continue

					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					deleted_count += 1
				except Exception as e:
					error_msg = str(e)
					_dbg(f"delete_{doctype}:error", {"name": name, "error": error_msg})
					if "child nodes" in error_msg.lower() or "transaction" in error_msg.lower():
						skipped_count += 1
					else:
						stats["errors"].append(f"{doctype} ({name}): {error_msg}")

		stats["deleted"][doctype] = count if dry_run else deleted_count
		if skipped_count > 0:
			stats["deleted"][f"{doctype} (Skipped - Has Transactions/Children)"] = skipped_count
		_dbg(
			f"delete_{doctype}",
			{"total": count, "deleted": deleted_count, "skipped": skipped_count, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_warehouses(company_name, stats, dry_run):
	"""Delete Warehouses for the company"""
	doctype = "Warehouse"
	try:
		# Get warehouses - leaf nodes first, then parents
		leaf_warehouses = frappe.get_all(
			doctype, filters={"company": company_name, "is_group": 0}, pluck="name"
		)

		group_warehouses = frappe.get_all(
			doctype,
			filters={"company": company_name, "is_group": 1},
			pluck="name",
			order_by="lft desc",  # Delete deepest nodes first
		)

		all_warehouses = leaf_warehouses + group_warehouses
		count = len(all_warehouses)
		deleted_count = 0
		skipped_count = 0

		if not dry_run and count > 0:
			for name in all_warehouses:
				try:
					# Check if warehouse has stock
					has_stock = frappe.db.sql(
						"""
                        SELECT COUNT(*) as count
                        FROM `tabBin`
                        WHERE warehouse = %s
                        AND (actual_qty != 0 OR reserved_qty != 0 OR ordered_qty != 0)
                        LIMIT 1
                    """,
						(name,),
					)[0][0]

					if has_stock > 0:
						_dbg(f"delete_{doctype}:skip_has_stock", {"name": name})
						skipped_count += 1
						continue

					# Check for child warehouses
					has_children = frappe.db.count(doctype, {"parent_warehouse": name})
					if has_children > 0:
						_dbg(
							f"delete_{doctype}:skip_has_children", {"name": name, "child_count": has_children}
						)
						skipped_count += 1
						continue

					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					deleted_count += 1
				except Exception as e:
					error_msg = str(e)
					_dbg(f"delete_{doctype}:error", {"name": name, "error": error_msg})
					if "quantity exists" in error_msg.lower() or "child warehouse" in error_msg.lower():
						skipped_count += 1
					else:
						stats["errors"].append(f"{doctype} ({name}): {error_msg}")

		stats["deleted"][doctype] = count if dry_run else deleted_count
		if skipped_count > 0:
			stats["deleted"][f"{doctype} (Skipped - Has Stock/Children)"] = skipped_count
		_dbg(
			f"delete_{doctype}",
			{"total": count, "deleted": deleted_count, "skipped": skipped_count, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_cost_centers(company_name, stats, dry_run):
	"""Delete Cost Centers for the company"""
	doctype = "Cost Center"
	try:
		records = frappe.get_all(doctype, filters={"company": company_name}, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_items(company_name, stats, dry_run):
	"""Delete Items synced from QuickBooks for the specified company"""
	doctype = "Item"
	try:
		# Only delete items that were synced from QuickBooks for this company
		# Items have custom_company field to distinguish between companies
		filters = {"quickbooks_item_id": ["!=", ""]}
		# Add company filter if custom_company field exists
		if frappe.get_meta(doctype).has_field("custom_company"):
			filters["custom_company"] = company_name
		records = frappe.get_all(doctype, filters=filters, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_customers(company_name, stats, dry_run):
	"""Delete Customers synced from QuickBooks for the specified company"""
	doctype = "Customer"
	try:
		# Only delete customers that were synced from QuickBooks for this company
		# Customers have custom_company field to distinguish between companies
		filters = {"quickbooks_cust_id": ["!=", ""]}
		# Add company filter if custom_company field exists
		if frappe.get_meta(doctype).has_field("custom_company"):
			filters["custom_company"] = company_name
		records = frappe.get_all(doctype, filters=filters, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_suppliers(company_name, stats, dry_run):
	"""Delete Suppliers synced from QuickBooks for the specified company"""
	doctype = "Supplier"
	try:
		# Only delete suppliers that were synced from QuickBooks for this company
		# Suppliers have custom_company field to distinguish between companies
		filters = {"quickbooks_supp_id": ["!=", ""]}
		# Add company filter if custom_company field exists
		if frappe.get_meta(doctype).has_field("custom_company"):
			filters["custom_company"] = company_name
		records = frappe.get_all(doctype, filters=filters, pluck="name")
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_employees(company_name, stats, dry_run):
	"""Delete Employees synced from QuickBooks"""
	doctype = "Employee"
	try:
		# Only delete employees that were synced from QuickBooks for this company
		records = frappe.get_all(
			doctype, filters={"company": company_name, "quickbooks_emp_id": ["!=", ""]}, pluck="name"
		)
		count = len(records)

		if not dry_run and count > 0:
			for name in records:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				except Exception as e:
					_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_addresses(company_name, stats, dry_run):
	"""Delete Addresses linked to deleted Customers/Suppliers.
	If addresses cannot be deleted (linked to transactions), disable them instead.
	"""
	doctype = "Address"
	try:
		# Find addresses linked to customers/suppliers from QuickBooks
		# This is a bit tricky - we need to find addresses linked via Dynamic Link
		addresses = set()

		# Get addresses linked to QB customers for this company
		customer_filters = {"quickbooks_cust_id": ["!=", ""]}
		if frappe.get_meta("Customer").has_field("custom_company"):
			customer_filters["custom_company"] = company_name
		customers = frappe.get_all("Customer", filters=customer_filters, pluck="name")
		for customer in customers:
			addr_links = frappe.get_all(
				"Dynamic Link",
				filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
				pluck="parent",
			)
			addresses.update(addr_links)

		# Get addresses linked to QB suppliers for this company
		supplier_filters = {"quickbooks_supp_id": ["!=", ""]}
		if frappe.get_meta("Supplier").has_field("custom_company"):
			supplier_filters["custom_company"] = company_name
		suppliers = frappe.get_all("Supplier", filters=supplier_filters, pluck="name")
		for supplier in suppliers:
			addr_links = frappe.get_all(
				"Dynamic Link",
				filters={"link_doctype": "Supplier", "link_name": supplier, "parenttype": "Address"},
				pluck="parent",
			)
			addresses.update(addr_links)

		# Get addresses linked to the company
		company_addresses = frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Company", "link_name": company_name, "parenttype": "Address"},
			pluck="parent",
		)
		addresses.update(company_addresses)

		# Also get addresses that are directly linked to Sales Invoices/Purchase Invoices for this company
		# These might not be linked through customers/suppliers but still need to be handled
		si_addresses = frappe.db.sql(
			"""
            SELECT DISTINCT shipping_address_name, billing_address_name
            FROM `tabSales Invoice`
            WHERE company = %s
            AND (shipping_address_name IS NOT NULL OR billing_address_name IS NOT NULL)
        """,
			(company_name,),
			as_dict=True,
		)

		for row in si_addresses:
			if row.get("shipping_address_name"):
				addresses.add(row["shipping_address_name"])
			if row.get("billing_address_name"):
				addresses.add(row["billing_address_name"])

		pi_addresses = frappe.db.sql(
			"""
            SELECT DISTINCT shipping_address, billing_address
            FROM `tabPurchase Invoice`
            WHERE company = %s
            AND (shipping_address IS NOT NULL OR billing_address IS NOT NULL)
        """,
			(company_name,),
			as_dict=True,
		)

		for row in pi_addresses:
			if row.get("shipping_address"):
				addresses.add(row["shipping_address"])
			if row.get("billing_address"):
				addresses.add(row["billing_address"])

		count = len(addresses)
		deleted_count = 0
		disabled_count = 0

		if not dry_run and count > 0:
			for name in addresses:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					deleted_count += 1
				except Exception as e:
					error_msg = str(e)
					_dbg(f"delete_{doctype}:error", {"name": name, "error": error_msg})

					# Check if address cannot be deleted because it's linked to transactions
					# ERPNext error messages include: "linked with", "Cannot delete", "linked to"
					error_lower = error_msg.lower()
					is_linked_error = (
						"linked with" in error_lower
						or "cannot delete" in error_lower
						or "linked to" in error_lower
						or "cannot be deleted" in error_lower
						or "is linked" in error_lower
					)

					if is_linked_error:
						try:
							addr_doc = frappe.get_doc(doctype, name)
							addr_doc.disabled = 1
							addr_doc.save(ignore_permissions=True)
							disabled_count += 1
							_dbg(
								f"delete_{doctype}:disabled",
								{"name": name, "reason": "Linked to transactions - disabled instead"},
							)
						except Exception as disable_err:
							_dbg(f"delete_{doctype}:disable_error", {"name": name, "error": str(disable_err)})
							# Don't add to errors if we successfully disabled it
							if disabled_count == 0 or name not in [a for a in addresses if a == name]:
								stats["errors"].append(
									f"{doctype} ({name}): Could not delete or disable: {error_msg}"
								)
					else:
						# Different type of error - add to errors list
						stats["errors"].append(f"{doctype} ({name}): {error_msg}")

		stats["deleted"][doctype] = deleted_count
		if disabled_count > 0:
			stats["deleted"][f"{doctype} (Disabled)"] = disabled_count
		_dbg(
			f"delete_{doctype}",
			{"count": count, "deleted": deleted_count, "disabled": disabled_count, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_contacts(company_name, stats, dry_run):
	"""Delete Contacts linked to deleted Customers/Suppliers.
	If contacts cannot be deleted (linked to transactions), disable them instead.
	"""
	doctype = "Contact"
	try:
		# Find contacts linked to customers/suppliers from QuickBooks
		contacts = set()

		# Get contacts linked to QB customers for this company
		customer_filters = {"quickbooks_cust_id": ["!=", ""]}
		if frappe.get_meta("Customer").has_field("custom_company"):
			customer_filters["custom_company"] = company_name
		customers = frappe.get_all("Customer", filters=customer_filters, pluck="name")
		for customer in customers:
			contact_links = frappe.get_all(
				"Dynamic Link",
				filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
				pluck="parent",
			)
			contacts.update(contact_links)

		# Get contacts linked to QB suppliers for this company
		supplier_filters = {"quickbooks_supp_id": ["!=", ""]}
		if frappe.get_meta("Supplier").has_field("custom_company"):
			supplier_filters["custom_company"] = company_name
		suppliers = frappe.get_all("Supplier", filters=supplier_filters, pluck="name")
		for supplier in suppliers:
			contact_links = frappe.get_all(
				"Dynamic Link",
				filters={"link_doctype": "Supplier", "link_name": supplier, "parenttype": "Contact"},
				pluck="parent",
			)
			contacts.update(contact_links)

		count = len(contacts)
		deleted_count = 0
		disabled_count = 0

		if not dry_run and count > 0:
			for name in contacts:
				try:
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					deleted_count += 1
				except Exception as e:
					error_msg = str(e)
					_dbg(f"delete_{doctype}:error", {"name": name, "error": error_msg})

					# Check if contact cannot be deleted because it's linked to transactions
					error_lower = error_msg.lower()
					is_linked_error = (
						"linked with" in error_lower
						or "cannot delete" in error_lower
						or "linked to" in error_lower
						or "cannot be deleted" in error_lower
						or "is linked" in error_lower
					)

					if is_linked_error:
						try:
							contact_doc = frappe.get_doc(doctype, name)
							contact_doc.disabled = 1
							contact_doc.save(ignore_permissions=True)
							disabled_count += 1
							_dbg(
								f"delete_{doctype}:disabled",
								{"name": name, "reason": "Linked to transactions - disabled instead"},
							)
						except Exception as disable_err:
							_dbg(f"delete_{doctype}:disable_error", {"name": name, "error": str(disable_err)})
							stats["errors"].append(
								f"{doctype} ({name}): Could not delete or disable: {error_msg}"
							)
					else:
						# Different type of error - add to errors list
						stats["errors"].append(f"{doctype} ({name}): {error_msg}")

		stats["deleted"][doctype] = deleted_count
		if disabled_count > 0:
			stats["deleted"][f"{doctype} (Disabled)"] = disabled_count
		_dbg(
			f"delete_{doctype}",
			{"count": count, "deleted": deleted_count, "disabled": disabled_count, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_quickbooks_logs(company_name, stats, dry_run):
	"""Delete QuickBooks Log entries related to the company"""
	doctype = "Quickbooks Log"
	try:
		# Get company abbreviation for filtering
		company_abbr = frappe.db.get_value("Company", company_name, "abbr")
		if not company_abbr:
			# If no abbreviation, still try to match by company name
			company_abbr = None

		# Get all Quickbooks Log entries in batches to avoid memory issues
		matching_logs = []
		company_lower = company_name.lower()
		abbr_lower = company_abbr.lower() if company_abbr else ""

		# Process in batches to avoid loading all logs at once
		batch_size = 1000
		start = 0

		while True:
			try:
				# Get batch of logs
				all_logs = frappe.get_all(
					doctype,
					fields=["name", "message", "request_data", "method"],
					limit_start=start,
					limit_page_length=batch_size,
					order_by="creation desc",
				)

				if not all_logs:
					break

				# Filter logs that mention this company or company abbreviation
				for log in all_logs:
					# Check if log mentions company name or abbreviation
					message = (log.get("message") or "").lower()
					request_data = (log.get("request_data") or "").lower()
					method = (log.get("method") or "").lower()

					# Check if company name or abbreviation appears in log content
					if (
						company_lower in message
						or company_lower in request_data
						or (abbr_lower and (abbr_lower in message or abbr_lower in request_data))
						or company_lower in method
					):
						matching_logs.append(log["name"])

				# If we got fewer than batch_size, we're done
				if len(all_logs) < batch_size:
					break

				start += batch_size

			except Exception as batch_error:
				# If batch processing fails, log error but continue
				error_msg = f"Error processing batch at start={start}: {batch_error!s}"
				_dbg(f"delete_{doctype}:batch_error", {"error": error_msg, "start": start})
				stats["errors"].append(error_msg)
				break

		count = len(matching_logs)

		if not dry_run and count > 0:
			# Delete in batches to avoid overwhelming the database
			delete_batch_size = 100
			for i in range(0, count, delete_batch_size):
				batch = matching_logs[i : i + delete_batch_size]
				for name in batch:
					try:
						frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
					except Exception as e:
						_dbg(f"delete_{doctype}:error", {"name": name, "error": str(e)})
						stats["errors"].append(f"{doctype} ({name}): {e!s}")

				# Commit after each batch
				try:
					frappe.db.commit()  # nosemgrep
				except Exception as commit_error:
					_dbg(f"delete_{doctype}:commit_error", {"error": str(commit_error)})
					# Continue even if commit fails - individual deletes may have succeeded

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		error_msg = str(e)
		stats["errors"].append(f"{doctype}: {error_msg}")
		_dbg(f"delete_{doctype}:error", {"error": error_msg})


def _delete_debug_log_files(company_name, stats, dry_run):
	"""Delete debug log files that contain company abbreviation in filename"""
	import json
	import os

	from frappe.utils import get_site_path

	try:
		# Get company abbreviation
		company_abbr = frappe.db.get_value("Company", company_name, "abbr")
		if not company_abbr:
			_dbg("delete_debug_log_files:no_abbr", {"company": company_name})
			stats["deleted"]["Debug Log Files"] = 0
			return

		# Get path to private files directory
		site_path = get_site_path()
		private_files_path = os.path.join(site_path, "private", "files")

		if not os.path.exists(private_files_path):
			stats["deleted"]["Debug Log Files"] = 0
			return

		# Find all debug log files with company abbreviation
		deleted_files = []
		deleted_count = 0

		# Pattern 1: qb_debug_logs_{company_abbr}_*.json
		# Pattern 2: qb_{company_abbr}_*_full_response.json
		abbr_lower = company_abbr.lower()
		company_lower = company_name.lower()

		for filename in os.listdir(private_files_path):
			if not filename.endswith(".json"):
				continue

			filename_lower = filename.lower()
			# Prevent directory traversal
			safe_filename = os.path.basename(filename)
			file_path = os.path.abspath(os.path.join(private_files_path, safe_filename))

			# Verify path is within private_files_path
			if not file_path.startswith(os.path.abspath(private_files_path)):
				continue

			should_delete = False

			# Check if filename contains company abbreviation
			# Format: qb_debug_logs_{company_abbr}_{module}_full_response.json
			# Format: qb_{company_abbr}_{data_name}_full_response.json
			if filename_lower.startswith("qb_") and abbr_lower in filename_lower:
				# Filename contains company abbreviation - delete it
				should_delete = True
			else:
				# Filename doesn't contain abbreviation - check file content
				try:
					with open(file_path, encoding="utf-8") as f:  # nosemgrep
						content = json.load(f)

						# If it's a list of log entries, check if any entry has this company_abbr
						if isinstance(content, list):
							has_company_data = any(
								entry.get("company_abbr", "").lower() == abbr_lower
								for entry in content
								if isinstance(entry, dict)
							)
							if has_company_data:
								should_delete = True
						# If it's a dict, check for company references
						elif isinstance(content, dict):
							# Check if content mentions company
							content_str = json.dumps(content).lower()
							if abbr_lower in content_str or company_lower in content_str:
								should_delete = True
				except (OSError, json.JSONDecodeError):
					# If file is not valid JSON or can't be read, skip it
					# (we only delete based on filename match, not content match)
					pass

			if should_delete:
				deleted_files.append(filename)

		count = len(deleted_files)

		if not dry_run and count > 0:
			for filename in deleted_files:
				try:
					file_path = os.path.join(private_files_path, filename)
					os.remove(file_path)
					deleted_count += 1
					_dbg("delete_debug_log_files:deleted", {"filename": filename})
				except Exception as e:
					_dbg("delete_debug_log_files:error", {"filename": filename, "error": str(e)})
					stats["errors"].append(f"Debug Log File ({filename}): {e!s}")

		stats["deleted"]["Debug Log Files"] = count if dry_run else deleted_count
		if deleted_files:
			stats["deleted"]["Debug Log Files (List)"] = deleted_files[:10]  # Store first 10 for reference
		_dbg(
			"delete_debug_log_files",
			{"count": count, "deleted": deleted_count, "company_abbr": company_abbr, "dry_run": dry_run},
		)
	except Exception as e:
		stats["errors"].append(f"Debug Log Files: {e!s}")
		_dbg("delete_debug_log_files:error", {"error": str(e)})


def _delete_tax_accounts(company_name, stats, dry_run):
	"""Delete QuickBooks Tax Account child table entries"""
	doctype = "Quickbooks Tax Account"
	try:
		# This is a child table in QuickBooks Settings
		# We'll delete entries related to accounts from this company
		qs = frappe.get_doc("Quickbooks Settings")

		if not hasattr(qs, "quickbooks_tax_account"):
			stats["deleted"][doctype] = 0
			return

		# Get accounts from this company
		company_accounts = frappe.get_all("Account", filters={"company": company_name}, pluck="name")

		# Filter tax accounts that reference these accounts
		to_remove = []
		for idx, tax_account in enumerate(qs.quickbooks_tax_account):
			if tax_account.account in company_accounts:
				to_remove.append(idx)

		count = len(to_remove)

		if not dry_run and count > 0:
			# Remove in reverse order to avoid index issues
			for idx in reversed(to_remove):
				qs.quickbooks_tax_account.pop(idx)
			qs.save(ignore_permissions=True)

		stats["deleted"][doctype] = count
		_dbg(f"delete_{doctype}", {"count": count, "dry_run": dry_run})
	except Exception as e:
		stats["errors"].append(f"{doctype}: {e!s}")


def _delete_company_doc(company_name, stats, dry_run):
	"""Delete the Company document itself"""
	doctype = "Company"
	try:
		if not dry_run:
			# Check for remaining child nodes
			remaining_accounts = frappe.db.count("Account", {"company": company_name})
			remaining_warehouses = frappe.db.count("Warehouse", {"company": company_name})
			remaining_cost_centers = frappe.db.count("Cost Center", {"company": company_name})

			if remaining_accounts > 0 or remaining_warehouses > 0 or remaining_cost_centers > 0:
				error_msg = (
					f"Cannot delete Company '{company_name}' as it still has child nodes:\n"
					f"  - Accounts: {remaining_accounts}\n"
					f"  - Warehouses: {remaining_warehouses}\n"
					f"  - Cost Centers: {remaining_cost_centers}\n"
					"These could not be deleted because they have transactions or stock. "
					"You must manually delete or clear these records before deleting the company."
				)
				stats["errors"].append(error_msg)
				stats["deleted"][doctype] = 0
				_dbg(
					f"delete_{doctype}:skip_has_children",
					{
						"company": company_name,
						"accounts": remaining_accounts,
						"warehouses": remaining_warehouses,
						"cost_centers": remaining_cost_centers,
					},
				)
				return

			frappe.delete_doc(doctype, company_name, force=True, ignore_permissions=True)
			stats["deleted"][doctype] = 1
		else:
			# Dry run - just count
			stats["deleted"][doctype] = 1

		_dbg(f"delete_{doctype}", {"company": company_name, "dry_run": dry_run})
	except Exception as e:
		error_msg = str(e)
		stats["errors"].append(f"{doctype}: {error_msg}")
		stats["deleted"][doctype] = 0
		_dbg(f"delete_{doctype}:error", {"company": company_name, "error": error_msg})


@frappe.whitelist()
def get_companies_with_quickbooks_data():
	"""
	Get list of companies that have QuickBooks synced data.

	Returns:
	    list: List of company names with QB data
	"""
	companies = []

	# Find companies that have QuickBooks accounts
	qb_companies = frappe.db.sql(
		"""
        SELECT DISTINCT company
        FROM `tabAccount`
        WHERE quickbooks_account_id IS NOT NULL
        AND quickbooks_account_id != ''
    """,
		as_dict=True,
	)

	for row in qb_companies:
		if row.company:
			companies.append(row.company)

	# Also check for companies from QuickBooks Settings (synced company)
	try:
		qs = frappe.get_doc("Quickbooks Settings")
		if qs.enable_quickbooks_online and qs.realm_id:
			# Try to find the synced company name
			default_company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
				"Global Defaults", "default_company"
			)
			if default_company and default_company not in companies:
				companies.append(default_company)
	except Exception:
		pass

	return sorted(list(set(companies)))
