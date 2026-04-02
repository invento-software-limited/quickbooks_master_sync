# Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import json

import frappe

from .exceptions import QuickbooksSetupError


def disable_quickbooks_sync_on_exception():
	frappe.db.rollback()
	frappe.db.set_single_value("Quickbooks Settings", "enable_quickbooks_online", 0)
	frappe.db.commit()


def make_quickbooks_log(
	title="Sync Log",
	status="Queued",
	method="sync_quickbooks",
	message=None,
	exception=False,
	name=None,
	request_data=None,
	company=None,
):
	if request_data is None:
		request_data = {}
	if not name:
		# Try to get company from request_data or defaults
		if not company:
			company = request_data.get("company") or frappe.defaults.get_user_default("company")

		# Look for existing queued log with same company
		filters = {"status": "Queued"}
		if company:
			filters["company"] = company
		name = frappe.db.get_value("Quickbooks Log", filters)

		if name:
			"""if name not provided by log calling method then fetch existing queued state log"""
			log = frappe.get_doc("Quickbooks Log", name)

		else:
			"""if queued job is not found create a new one."""
			log = frappe.get_doc({"doctype": "Quickbooks Log"}).insert(ignore_permissions=True)

		if exception:
			frappe.db.rollback()
			log = frappe.get_doc({"doctype": "Quickbooks Log"}).insert(ignore_permissions=True)

		log.message = message if message else frappe.get_traceback()
		# Safely truncate title to fit database column (max 140 chars)
		# Strip HTML tags to avoid encoding issues and get plain text length
		import re

		plain_title = re.sub(r"<[^>]+>", "", str(title)) if title else ""
		log.title = plain_title[0:140] if len(plain_title) > 140 else plain_title
		log.method = method
		log.status = status
		log.request_data = json.dumps(request_data)
		# Set company if provided
		if company:
			log.company = company
		elif not log.company:
			# Fallback to user default company if not set
			log.company = frappe.defaults.get_user_default("company")

		# Try to save the log, handle potential concurrency issues (TimestampMismatchError)
		# especially common when multiple background chunks finish at the same time
		max_retries = 3
		for i in range(max_retries):
			try:
				log.save(ignore_permissions=True)
				frappe.db.commit()
				break
			except frappe.TimestampMismatchError:
				if i < max_retries - 1:
					# Reload the document to get the latest timestamp and try again
					log = frappe.get_doc("Quickbooks Log", log.name)
					# Re-apply changes
					log.message = message if message else frappe.get_traceback()
					log.title = plain_title[0:140] if len(plain_title) > 140 else plain_title
					log.method = method
					log.status = status
					log.request_data = json.dumps(request_data)
					if company:
						log.company = company
				else:
					# If all retries fail, insert as a new log record to avoid losing data
					new_log = frappe.get_doc(
						{
							"doctype": "Quickbooks Log",
							"title": plain_title[0:140] if len(plain_title) > 140 else plain_title,
							"method": method,
							"status": status,
							"message": message if message else frappe.get_traceback(),
							"request_data": json.dumps(request_data),
							"company": company or frappe.defaults.get_user_default("company"),
						}
					)
					new_log.insert(ignore_permissions=True)
					frappe.db.commit()
			except Exception as e:
				# For other errors, log to console but don't crash the sync process
				print(f"Failed to save Quickbooks Log: {e!s}")
				break
