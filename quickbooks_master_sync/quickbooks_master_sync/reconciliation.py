import json
import os

import frappe
from frappe import _
from frappe.utils import flt, get_site_path

from .utils.qb_id_utils import make_unique_qb_id


def get_company_from_filename(filename):
	"""
	Extract company abbreviation from filename.
	Format: qb_{Company}_{Type}...json
	"""
	if not filename:
		return None

	parts = filename.replace(".json", "").split("_")

	potential_company = None
	if len(parts) >= 2:
		# qb_{Company}_{Type}...
		potential_company = parts[1]
		common_names = [
			"debug",
			"log",
			"account",
			"customer",
			"supplier",
			"item",
			"product",
		]
		if potential_company.lower() in common_names:
			potential_company = None

	company_abbr = None
	if potential_company:
		# Try to look up company abbreviation by name
		company_abbr = frappe.db.get_value("Company", {"name": potential_company}, "abbr")

		# If not found, fallback to heuristic (assuming it's already an abbreviation)
		if not company_abbr:
			if potential_company.isupper() and len(potential_company) <= 5:
				company_abbr = potential_company

	return company_abbr


@frappe.whitelist()
def reconcile_debug_file_content(filename: str):
	"""
	Reconcile a QuickBooks debug file against ERPNext data.
	"""
	content = get_file_content(filename)
	if not content:
		return {"success": False, "error": "File not found or empty"}

	# Determine entity type and records
	entity_type, records = parse_qb_content(content)
	if not entity_type:
		return {
			"success": False,
			"error": "Could not determine entity type or no records found",
		}

	# Extract company from filename for exact ID matching
	company_abbr = get_company_from_filename(filename)

	results = {
		"entity_type": entity_type,
		"total_records": len(records),
		"missing_in_erpnext": [],
		"amount_mismatches": [],
		"matched": [],
	}

	# Process based on entity type
	if entity_type == "Invoice":
		reconcile_invoices(records, results, entity_type, company_abbr)
	elif entity_type == "CreditMemo":
		reconcile_credit_memos(records, results, company_abbr)
	elif entity_type == "Customer":
		reconcile_customers(records, results, company_abbr)
	elif entity_type == "Bill":
		reconcile_bills(records, results, company_abbr)
	elif entity_type == "RefundReceipt":
		reconcile_refund_receipts(records, results, company_abbr)
	elif entity_type == "CreditCardPayment":
		reconcile_credit_card_payments(records, results, company_abbr)
	elif entity_type == "Vendor":
		reconcile_suppliers(records, results, company_abbr)
	elif entity_type == "Item":
		reconcile_items(records, results, company_abbr)
	elif entity_type == "Transfer":
		reconcile_transfers(records, results, company_abbr)
	elif entity_type == "Deposit":
		reconcile_deposits(records, results, company_abbr)
	elif entity_type == "JournalEntry":
		reconcile_journal_entries(records, results, company_abbr)
	elif entity_type == "BillPayment":
		reconcile_bill_payments(records, results, company_abbr)
	elif entity_type == "Payment":
		reconcile_payments(records, results, company_abbr)
	elif entity_type == "VendorCredit":
		reconcile_vendor_credits(records, results, company_abbr)
	elif entity_type == "Asset" or entity_type == "Purchase":
		reconcile_fixed_assets(records, results, company_abbr)
	elif entity_type == "SalesReceipt":
		reconcile_sales_receipts(records, results, company_abbr)
	elif entity_type == "Account":
		reconcile_accounts(records, results, company_abbr)
	else:
		return {
			"success": False,
			"error": f"Reconciliation for {entity_type} not yet implemented",
		}

	return {"success": True, "results": results}


def get_file_content(filename: str):
	if not filename:
		return None

	# Prevent directory traversal by only using the basename
	safe_filename = os.path.basename(filename)

	site_path = get_site_path()
	# Construct absolute path and verify it stays within the intended directory
	base_path = os.path.abspath(os.path.join(site_path, "private", "files"))
	file_path = os.path.abspath(os.path.join(base_path, safe_filename))

	if not file_path.startswith(base_path):
		frappe.throw(_("Invalid file access attempt: {0}").format(filename))

	if not os.path.exists(file_path):
		return None

	with open(file_path, encoding="utf-8") as f:  # nosemgrep
		return json.load(f)


def parse_qb_content(content):
	# Handle pagination wrapper format (e.g. from fetch_all_records/export)
	# Case 1: List of wrappers (original assumption)
	if isinstance(content, list) and content and isinstance(content[0], dict) and "pages" in content[0]:
		all_records = []
		entity_name = None

		for wrapper in content:
			if "pages" in wrapper:
				for page in wrapper.get("pages", []):
					response = page.get("response", {})
					if "QueryResponse" in response:
						qr = response["QueryResponse"]
						for key, data in qr.items():
							if key in ["maxResults", "startPosition", "totalCount"]:
								continue

							if not entity_name:
								entity_name = key

							if isinstance(data, list):
								all_records.extend(data)
							elif isinstance(data, dict):
								all_records.append(data)

		if entity_name:
			return entity_name, all_records

	# Case 2: Dictionary wrapper (user provided format)
	if isinstance(content, dict) and "pages" in content:
		all_records = []
		entity_name = None

		for page in content.get("pages", []):
			response = page.get("response", {})
			if "QueryResponse" in response:
				qr = response["QueryResponse"]
				for key, data in qr.items():
					if key in ["maxResults", "startPosition", "totalCount"]:
						continue

					if not entity_name:
						entity_name = key

					if isinstance(data, list):
						all_records.extend(data)
					elif isinstance(data, dict):
						all_records.append(data)

		if entity_name:
			return entity_name, all_records

	# Handle QueryResponse format
	if isinstance(content, dict) and "QueryResponse" in content:
		qr = content["QueryResponse"]
		if not qr:
			return None, []

		# exclude meta keys
		for key in qr:
			if key not in ["maxResults", "startPosition", "totalCount"]:
				data = qr[key]
				if isinstance(data, list):
					return key, data
				elif isinstance(data, dict):  # Single record
					return key, [data]

	# Handle list format (e.g. from debug_logs or direct dumps)
	if isinstance(content, list):
		if not content:
			return None, []
		# Check first item to guess type
		first = content[0]
		if "DocNumber" in first and "Line" in first:
			# Likely Invoice or CreditMemo check TxnType logic if needed, or assume based on file name?
			# But usually these lists are homogeneous.
			# Let's try to detect from content.
			if "CreditMemo" in str(first):  # weak check
				return "CreditMemo", content
			return "Invoice", content  # Default to Invoice for transaction-like objects
		if "GivenName" in first or "DisplayName" in first:
			return "Customer", content

	return None, []


def reconcile_invoices(records, results, entity_type, company_abbr=None):
	"""Reconcile Invoice against Sales Invoice or Purchase Invoice"""
	doctype = "Sales Invoice" if entity_type == "Invoice" else "Purchase Invoice"
	id_field = "quickbooks_invoce_id" if entity_type == "Invoice" else "quickbooks_purchase_invoice_id"

	for record in records:
		qb_id = record.get("Id")
		doc_number = record.get("DocNumber")
		total_amt = flt(record.get("TotalAmt"))
		txn_date = record.get("TxnDate")

		filters = {}
		exists = None
		if company_abbr:
			# Exact Match: ABBR-ID
			filters[id_field] = f"{company_abbr}-{qb_id}"
			exists = frappe.db.get_value(doctype, filters, ["name", "grand_total"], as_dict=True)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			diff = abs(flt(exists.grand_total) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
						"doctype": doctype,
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": doctype,
					}
				)
	return results


def reconcile_customers(records, results, company_abbr=None):
	"""Reconcile Customer against Customer"""
	for record in records:
		qb_id = record.get("Id")
		name = record.get("DisplayName")

		exists = None
		if company_abbr:
			# Exact Match: ABBR-ID
			# Note: Customer uses quickbooks_cust_id
			exists = frappe.db.get_value(
				"Customer", {"quickbooks_cust_id": f"{company_abbr}-{qb_id}"}, "name"
			)

		if not exists:
			results["missing_in_erpnext"].append({"qb_id": qb_id, "name": name})
		else:
			results["matched"].append(
				{"qb_id": qb_id, "name": name, "erp_name": exists, "doctype": "Customer"}
			)
	return results


def reconcile_bills(records, results, company_abbr=None):
	"""Reconcile Bill/VendorCredit against Purchase Invoice"""
	for record in records:
		qb_id = record.get("Id")
		doc_number = record.get("DocNumber")
		total_amt = flt(record.get("TotalAmt"))
		txn_date = record.get("TxnDate")

		# Check Purchase Invoice
		exists = None
		if company_abbr:
			# Exact Match: ABBR-BILL-ID
			exists = frappe.db.get_value(
				"Purchase Invoice",
				{"quickbooks_purchase_invoice_id": f"{company_abbr}-BILL-{qb_id}"},
				["name", "grand_total"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			diff = abs(flt(exists.grand_total) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
						"doctype": "Purchase Invoice",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Purchase Invoice",
					}
				)
	return results


def reconcile_refund_receipts(records, results, company_abbr=None):
	"""Reconcile RefundReceipt against Sales Invoice (Return)"""
	for record in records:
		qb_id = record.get("Id")
		doc_number = record.get("DocNumber")
		total_amt = flt(record.get("TotalAmt"))
		txn_date = record.get("TxnDate")

		# ID logic: REFUND-{id} -> ABBR-REFUND-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Sales Invoice",
				{"quickbooks_invoce_id": f"{company_abbr}-REFUND-{qb_id}"},
				["name", "grand_total"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			# Sales Invoice Return grand_total should be compared in absolute terms
			diff = abs(abs(flt(exists.grand_total)) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
						"doctype": "Sales Invoice",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Sales Invoice",
					}
				)
	return results


def reconcile_credit_card_payments(records, results, company_abbr=None):
	"""Reconcile CreditCardPayment against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("Amount"))
		txn_date = record.get("TxnDate")

		# ID logic: CCP-{id} -> ABBR-CCP-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-CCP-{qb_id}"},
				["name", "total_debit"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{"qb_id": qb_id, "txn_date": txn_date, "total_amt": total_amt}
			)
		else:
			diff = abs(flt(exists.total_debit) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.total_debit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Journal Entry",
					}
				)
	return results


def reconcile_credit_memos(records, results, company_abbr=None):
	"""Reconcile CreditMemo against Sales Invoice (Return)"""
	for record in records:
		qb_id = record.get("Id")
		doc_number = record.get("DocNumber")
		total_amt = flt(record.get("TotalAmt"))
		txn_date = record.get("TxnDate")

		# Check Sales Invoice
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Sales Invoice",
				{"quickbooks_invoce_id": f"{company_abbr}-CM-{qb_id}"},
				["name", "grand_total"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			diff = abs(abs(flt(exists.grand_total)) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
					}
				)
	return results


def reconcile_suppliers(records, results, company_abbr=None):
	"""Reconcile Vendor against Supplier"""
	for record in records:
		qb_id = record.get("Id")
		name = record.get("DisplayName")

		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Supplier", {"quickbooks_supp_id": f"{company_abbr}-{qb_id}"}, "name"
			)

		if not exists:
			results["missing_in_erpnext"].append({"qb_id": qb_id, "name": name})
		else:
			results["matched"].append(
				{"qb_id": qb_id, "name": name, "erp_name": exists, "doctype": "Supplier"}
			)
	return results


def reconcile_items(records, results, company_abbr=None):
	"""Reconcile Item against Item or Asset"""
	for record in records:
		qb_id = record.get("Id")
		name = record.get("Name")

		# Check Item
		exists = None
		if company_abbr:
			exists = frappe.db.get_value("Item", {"quickbooks_item_id": f"{company_abbr}-{qb_id}"}, "name")

		if not exists:
			# Check Asset if not found in Item (since fixed assets are items in QB)
			exists_asset = None
			if company_abbr:
				exists_asset = frappe.db.get_value(
					"Asset", {"quickbooks_asset_id": f"{company_abbr}-{qb_id}"}, "name"
				)

			if exists_asset:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"name": name,
						"erp_name": exists_asset,
						"type": "Asset",
						"doctype": "Asset",
					}
				)
			else:
				results["missing_in_erpnext"].append({"qb_id": qb_id, "name": name})
		else:
			results["matched"].append(
				{"qb_id": qb_id, "name": name, "erp_name": exists, "type": "Item", "doctype": "Item"}
			)
	return results


def reconcile_fixed_assets(records, results, company_abbr=None):
	"""Reconcile Fixed Asset (Item) against Asset or Purchase against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")

		# Check if this is a Transaction (Purchase) or Item (Asset)
		if "TxnDate" in record:
			# Transaction -> Check Journal Entry
			doc_number = record.get("DocNumber")
			total_amt = flt(record.get("TotalAmt"))
			txn_date = record.get("TxnDate")

			exists = None
			if company_abbr:
				# Try PURCHASE prefix first
				exists = frappe.db.get_value(
					"Journal Entry",
					{"quickbooks_journal_entry_id": f"{company_abbr}-PURCHASE-{qb_id}"},
					["name", "total_credit"],
					as_dict=True,
				)

				# If not found, try ASSET prefix
				if not exists:
					exists = frappe.db.get_value(
						"Journal Entry",
						{"quickbooks_journal_entry_id": f"{company_abbr}-ASSET-{qb_id}"},
						["name", "total_credit"],
						as_dict=True,
					)

			if not exists:
				results["missing_in_erpnext"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": None,
						"doctype": "Journal Entry",
					}
				)
			else:
				# Check amount match (Total Credit is usually the total payment amount)
				diff = abs(flt(exists.total_credit) - total_amt)
				if diff > 1.0:
					results["amount_mismatches"].append(
						{
							"qb_id": qb_id,
							"doc_number": doc_number,
							"txn_date": txn_date,
							"erp_name": exists.name,
							"qb_amount": total_amt,
							"erp_amount": exists.total_credit,
							"difference": diff,
							"doctype": "Journal Entry",
						}
					)
				else:
					results["matched"].append(
						{
							"qb_id": qb_id,
							"doc_number": doc_number,
							"txn_date": txn_date,
							"total_amt": total_amt,
							"erp_name": exists.name,
							"doctype": "Journal Entry",
						}
					)

		else:
			# Item/Asset -> Check Asset
			name = record.get("Name")

			exists = None
			if company_abbr:
				exists = frappe.db.get_value(
					"Asset", {"quickbooks_asset_id": f"{company_abbr}-{qb_id}"}, "name"
				)

			if not exists:
				results["missing_in_erpnext"].append({"qb_id": qb_id, "name": name})
			else:
				results["matched"].append({"qb_id": qb_id, "name": name, "erp_name": exists})
	return results


def reconcile_transfers(records, results, company_abbr=None):
	"""Reconcile Transfer against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		amount = flt(record.get("Amount"))
		txn_date = record.get("TxnDate")

		# ID logic: TR-{id} -> ABBR-TR-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-TR-{qb_id}"},
				["name", "total_debit"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append({"qb_id": qb_id, "txn_date": txn_date, "total_amt": amount})
		else:
			diff = abs(flt(exists.total_debit) - amount)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": amount,
						"erp_amount": exists.total_debit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"total_amt": amount,
						"erp_name": exists.name,
						"doctype": "Journal Entry",
					}
				)
	return results


def reconcile_deposits(records, results, company_abbr=None):
	"""Reconcile Deposit against Payment Entry or Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		txn_date = record.get("TxnDate")

		# Check Journal Entry if not found in PE
		# JE uses ABBR-DEP-{ID} (with hyphen)
		exists_je = None
		if company_abbr:
			exists_je = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-DEP-{qb_id}"},
				["name", "total_debit"],
				as_dict=True,
			)

		if exists_je:
			diff = abs(flt(exists_je.total_debit) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"erp_name": exists_je.name,
						"qb_amount": total_amt,
						"erp_amount": exists_je.total_debit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists_je.name,
						"doctype": "Journal Entry",
					}
				)
			continue

		# If neither found
		results["missing_in_erpnext"].append({"qb_id": qb_id, "txn_date": txn_date, "total_amt": total_amt})

	return results


def reconcile_journal_entries(records, results, company_abbr=None):
	"""Reconcile JournalEntry against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		if total_amt == 0 and "Line" in record:
			# Sum debits
			for line in record["Line"]:
				if line.get("DetailType") == "JournalEntryLineDetail":
					jd = line.get("JournalEntryLineDetail", {})
					if jd.get("PostingType") == "Debit":
						total_amt += flt(line.get("Amount"))

		doc_number = record.get("DocNumber")
		txn_date = record.get("TxnDate")

		# ID logic: JE{id} -> ABBR-JE{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-JE-{qb_id}"},
				["name", "total_debit"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			diff = abs(flt(exists.total_debit) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.total_debit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Journal Entry",
					}
				)
	return results


def reconcile_bill_payments(records, results, company_abbr=None):
	"""Reconcile BillPayment against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		doc_number = record.get("DocNumber")
		txn_date = record.get("TxnDate")

		# ID logic: {id} -> ABBR-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-BP-{qb_id}"},
				["name", "total_credit"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			# Bill Payment usually credits Bank/CC.
			diff = abs(flt(exists.total_credit) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.total_credit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Journal Entry",
					}
				)
	return results


def reconcile_payments(records, results, company_abbr=None):
	"""Reconcile Payment (Sales) against Journal Entry"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		ref_num = record.get("PaymentRefNum")
		txn_date = record.get("TxnDate")

		# ID logic: {id} -> ABBR-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Journal Entry",
				{"quickbooks_journal_entry_id": f"{company_abbr}-PAY-{qb_id}"},
				["name", "total_debit"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"ref_num": ref_num,
					"doc_num": ref_num,  # Map ref_num to doc_num for UI
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			# Payment debits Bank/Undeposited Funds.
			diff = abs(flt(exists.total_debit) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"ref_num": ref_num,
						"doc_num": ref_num,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.total_debit,
						"difference": diff,
						"doctype": "Journal Entry",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"ref_num": ref_num,
						"doc_num": ref_num,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Journal Entry",
					}
				)
	return results


def reconcile_vendor_credits(records, results, company_abbr=None):
	"""Reconcile VendorCredit against Purchase Invoice (Return)"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		doc_number = record.get("DocNumber")
		txn_date = record.get("TxnDate")

		# ID logic: {id} -> ABBR-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Purchase Invoice",
				{"quickbooks_purchase_invoice_id": f"{company_abbr}-VC-{qb_id}"},
				["name", "grand_total"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
				}
			)
		else:
			# Vendor Credit is negative PI (Return)
			diff = abs(abs(flt(exists.grand_total)) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
						"doctype": "Purchase Invoice",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Purchase Invoice",
					}
				)
	return results


def reconcile_sales_receipts(records, results, company_abbr=None):
	"""Reconcile SalesReceipt against Sales Invoice (POS/Paid)"""
	for record in records:
		qb_id = record.get("Id")
		total_amt = flt(record.get("TotalAmt"))
		doc_number = record.get("DocNumber")
		txn_date = record.get("TxnDate")

		# ID logic: {id} -> ABBR-RECEIPT-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Sales Invoice",
				{"quickbooks_invoce_id": f"{company_abbr}-RECEIPT-{qb_id}"},
				["name", "grand_total"],
				as_dict=True,
			)

		if not exists:
			results["missing_in_erpnext"].append(
				{
					"qb_id": qb_id,
					"doc_number": doc_number,
					"txn_date": txn_date,
					"total_amt": total_amt,
					"erp_name": None,
					"doctype": "Sales Invoice",
				}
			)
		else:
			# Sales Receipt maps to Sales Invoice grand_total
			diff = abs(flt(exists.grand_total) - total_amt)
			if diff > 1.0:
				results["amount_mismatches"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"erp_name": exists.name,
						"qb_amount": total_amt,
						"erp_amount": exists.grand_total,
						"difference": diff,
						"doctype": "Sales Invoice",
					}
				)
			else:
				results["matched"].append(
					{
						"qb_id": qb_id,
						"doc_number": doc_number,
						"txn_date": txn_date,
						"total_amt": total_amt,
						"erp_name": exists.name,
						"doctype": "Sales Invoice",
					}
				)
	return results


def reconcile_accounts(records, results, company_abbr=None):
	"""Reconcile Account against Account"""
	for record in records:
		qb_id = record.get("Id")
		name = record.get("Name")

		# ID logic: {id} -> ABBR-{id}
		exists = None
		if company_abbr:
			exists = frappe.db.get_value(
				"Account", {"quickbooks_account_id": f"{company_abbr}-{qb_id}"}, "name"
			)

		if not exists:
			results["missing_in_erpnext"].append({"qb_id": qb_id, "name": name})
		else:
			results["matched"].append(
				{"qb_id": qb_id, "name": name, "erp_name": exists, "doctype": "Account"}
			)
	return results
