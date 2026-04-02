import frappe


class QuickbooksError(frappe.ValidationError):
	pass


class QuickbooksSetupError(frappe.ValidationError):
	pass
