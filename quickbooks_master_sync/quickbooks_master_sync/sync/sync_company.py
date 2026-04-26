import frappe
from frappe import _
from frappe.utils import cint

from quickbooks_master_sync.pyqb.quickbooks import QuickBooks
from quickbooks_master_sync.quickbooks_master_sync.utils.exceptions import QuickbooksError
from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_status
from quickbooks_master_sync.quickbooks_master_sync.utils.utils import disable_quickbooks_sync_on_exception

from .sync_taxcode import sync_tax_code, sync_tax_rate
from .sync_utils import _dbg as _dbg_common
from .sync_utils import _resolve_country_name, save_qb_data_to_json


def _dbg(event, payload=None):
	"""Debug logging helper for company sync"""
	_dbg_common("sync_company", event, payload)


def _set_if_field(doc, fieldname, value):
	if value is None:
		return
	try:
		if frappe.get_meta(doc.doctype).has_field(fieldname):
			setattr(doc, fieldname, value)
	except Exception:
		pass


def _derive_abbr(name):
	"""
	Derive company abbreviation, always ensuring exactly 4 characters.
	If abbreviation from first letters is less than 4, use first 4 characters of company name.

	Args:
	    name: Company name

	Returns:
	    str: Exactly 4-character uppercase abbreviation
	"""
	if not name:
		return "COMP"  # Default fallback

	# Clean the name: remove special characters, keep only alphanumeric and spaces
	import re

	clean_name = re.sub(r"[^a-zA-Z0-9\s]", "", name)

	# Generate abbreviation from first letters of each word
	parts = [p for p in clean_name.split() if p]
	abbr = "".join([p[0].upper() for p in parts if p[0].isalnum()])

	# If abbreviation is less than 4 characters, use first 4 alphanumeric characters of company name
	if len(abbr) < 4:
		# Get first 4 alphanumeric characters from company name
		name_chars = "".join([c.upper() for c in clean_name if c.isalnum()])[:4]
		if len(name_chars) >= 4:
			abbr = name_chars
		else:
			# If still less than 4, pad with first character or use default
			if name_chars:
				abbr = (name_chars + name_chars[0] * (4 - len(name_chars)))[:4]
			else:
				abbr = "COMP"
	elif len(abbr) > 4:
		# If more than 4, truncate to 4
		abbr = abbr[:4]

	# Final validation: ensure exactly 4 characters
	if len(abbr) < 4:
		# Last resort: use first 4 characters of original name, uppercase
		abbr = (name[:4].upper() + "X" * (4 - len(name[:4])))[:4] if len(name) < 4 else name[:4].upper()

	return abbr


def _derive_unique_abbr(name):
	"""
	Derive a unique company abbreviation based on `_derive_abbr`.
	If the derived abbreviation already exists, append a numeric suffix
	to ensure uniqueness.
	"""
	base_abbr = _derive_abbr(name)
	unique_abbr = base_abbr

	# Check if abbreviation exists
	if not frappe.db.exists("Company", {"abbr": unique_abbr}):
		return unique_abbr

	# Try creating a unique abbreviation by replacing the last char with a number
	# Try 1-9 first
	for i in range(1, 10):
		# Taking first 3 chars + number
		candidate = f"{base_abbr[:3]}{i}"
		if not frappe.db.exists("Company", {"abbr": candidate}):
			return candidate

	# Try 10-99
	for i in range(10, 100):
		# Taking first 2 chars + number
		candidate = f"{base_abbr[:2]}{i}"
		if not frappe.db.exists("Company", {"abbr": candidate}):
			return candidate

	# Fallback to random string if all else fails (unlikely)
	import random
	import string

	random_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
	candidate = f"{base_abbr[:1]}{random_suffix}"
	return candidate


def _resolve_currency(ci):
	"""
	Resolve currency for the company.
	First tries explicit Currency field, then maps Country code,
	and finally falls back to system defaults.
	"""
	# Comprehensive Country to Currency mappings for QBO regions and beyond
	# Mapping ISO 3166-1 alpha-2 (Country) to ISO 4217 (Currency)
	country_currency_map = {
		"AF": "AFN",
		"AL": "ALL",
		"DZ": "DZD",
		"AS": "USD",
		"AD": "EUR",
		"AO": "AOA",
		"AI": "XCD",
		"AG": "XCD",
		"AR": "ARS",
		"AM": "AMD",
		"AW": "AWG",
		"AU": "AUD",
		"AT": "EUR",
		"AZ": "AZN",
		"BS": "BSD",
		"BH": "BHD",
		"BD": "BDT",
		"BB": "BBD",
		"BY": "BYN",
		"BE": "EUR",
		"BZ": "BZD",
		"BJ": "XOF",
		"BM": "BMD",
		"BT": "BTN",
		"BO": "BOB",
		"BA": "BAM",
		"BW": "BWP",
		"BR": "BRL",
		"BN": "BND",
		"BG": "BGN",
		"BF": "XOF",
		"BI": "BIF",
		"KH": "KHR",
		"CM": "XAF",
		"CA": "CAD",
		"CV": "CVE",
		"KY": "KYD",
		"CF": "XAF",
		"TD": "XAF",
		"CL": "CLP",
		"CN": "CNY",
		"CO": "COP",
		"KM": "KMF",
		"CG": "XAF",
		"CD": "CDF",
		"CR": "CRC",
		"HR": "HRK",
		"CU": "CUP",
		"CY": "EUR",
		"CZ": "CZK",
		"DK": "DKK",
		"DJ": "DJF",
		"DM": "XCD",
		"DO": "DOP",
		"EC": "USD",
		"EG": "EGP",
		"SV": "USD",
		"GQ": "XAF",
		"ER": "ERN",
		"EE": "EUR",
		"ET": "ETB",
		"FK": "FKP",
		"FJ": "FJD",
		"FI": "EUR",
		"FR": "EUR",
		"GA": "XAF",
		"GM": "GMD",
		"GE": "GEL",
		"DE": "EUR",
		"GH": "GHS",
		"GI": "GIP",
		"GR": "EUR",
		"GD": "XCD",
		"GT": "GTQ",
		"GN": "GNF",
		"GW": "XOF",
		"GY": "GYD",
		"HT": "HTG",
		"HN": "HNL",
		"HK": "HKD",
		"HU": "HUF",
		"IS": "ISK",
		"IN": "INR",
		"ID": "IDR",
		"IR": "IRR",
		"IQ": "IQD",
		"IE": "EUR",
		"IL": "ILS",
		"IT": "EUR",
		"JM": "JMD",
		"JP": "JPY",
		"JO": "JOD",
		"KZ": "KZT",
		"KE": "KES",
		"KI": "AUD",
		"KP": "KPW",
		"KR": "KRW",
		"KW": "KWD",
		"KG": "KGS",
		"LA": "LAK",
		"LV": "EUR",
		"LB": "LBP",
		"LS": "LSL",
		"LR": "LRD",
		"LY": "LYD",
		"LI": "CHF",
		"LT": "EUR",
		"LU": "EUR",
		"MO": "MOP",
		"MK": "MKD",
		"MG": "MGA",
		"MW": "MWK",
		"MY": "MYR",
		"MV": "MVR",
		"ML": "XOF",
		"MT": "EUR",
		"MH": "USD",
		"MR": "MRO",
		"MU": "MUR",
		"MX": "MXN",
		"FM": "USD",
		"MD": "MDL",
		"MC": "EUR",
		"MN": "MNT",
		"ME": "EUR",
		"MS": "XCD",
		"MA": "MAD",
		"MZ": "MZN",
		"MM": "MMK",
		"NA": "NAD",
		"NR": "AUD",
		"NP": "NPR",
		"NL": "EUR",
		"NZ": "NZD",
		"NI": "NIO",
		"NE": "XOF",
		"NG": "NGN",
		"NO": "NOK",
		"OM": "OMR",
		"PK": "PKR",
		"PW": "USD",
		"PA": "PAB",
		"PG": "PGK",
		"PY": "PYG",
		"PE": "PEN",
		"PH": "PHP",
		"PL": "PLN",
		"PT": "EUR",
		"QA": "QAR",
		"RO": "RON",
		"RU": "RUB",
		"RW": "RWF",
		"KN": "XCD",
		"LC": "XCD",
		"VC": "XCD",
		"WS": "WST",
		"SM": "EUR",
		"ST": "STD",
		"SA": "SAR",
		"SN": "XOF",
		"RS": "RSD",
		"SC": "SCR",
		"SL": "SLL",
		"SG": "SGD",
		"SK": "EUR",
		"SI": "EUR",
		"SB": "SBD",
		"SO": "SOS",
		"ZA": "ZAR",
		"ES": "EUR",
		"LK": "LKR",
		"SD": "SDG",
		"SR": "SRD",
		"SZ": "SZL",
		"SE": "SEK",
		"CH": "CHF",
		"SY": "SYP",
		"TW": "TWD",
		"TJ": "TJS",
		"TZ": "TZS",
		"TH": "THB",
		"TG": "XOF",
		"TO": "TOP",
		"TT": "TTD",
		"TN": "TND",
		"TR": "TRY",
		"TM": "TMT",
		"TV": "AUD",
		"UG": "UGX",
		"UA": "UAH",
		"AE": "AED",
		"GB": "GBP",
		"UK": "GBP",
		"US": "USD",
		"UY": "UYU",
		"UZ": "UZS",
		"VU": "VUV",
		"VE": "VEF",
		"VN": "VND",
		"YE": "YER",
		"ZM": "ZMW",
		"ZW": "ZWL",
	}

	country_code = ci.get("Country")
	mapped_currency = country_currency_map.get(country_code) if country_code else None

	return (
		ci.get("Currency")
		or mapped_currency
		or frappe.db.get_single_value("Global Defaults", "default_currency")
		or frappe.db.get_value("Currency", {}, "name")
	)


def _get_default_for_company_field(fieldname):
	"""Return a sensible default for mandatory custom fields on Company."""
	try:
		meta = frappe.get_meta("Company")
		df = meta.get_field(fieldname)
		if not df:
			return None
		fieldtype = df.fieldtype
		options = (df.options or "").strip()
		if fieldtype == "Link":
			if options == "Account":
				# Try to find a default account based on field name hints
				field_lower = fieldname.lower()
				if "revenue" in field_lower or "income" in field_lower:
					# Try to find a revenue/income account
					account = frappe.db.get_value(
						"Account",
						{"account_type": "Income", "is_group": 0},
						"name",
						order_by="name",
					)
					if account:
						return account
				elif "payable" in field_lower or "liability" in field_lower:
					# Try to find a payable/liability account
					account = frappe.db.get_value(
						"Account",
						{"account_type": "Payable", "is_group": 0},
						"name",
						order_by="name",
					)
					if account:
						return account
				elif "asset" in field_lower:
					# Try to find an asset account
					account = frappe.db.get_value(
						"Account",
						{"account_type": "Asset", "is_group": 0},
						"name",
						order_by="name",
					)
					if account:
						return account
				# Fallback: get first account of any type (leaf accounts first)
				account = frappe.db.get_value("Account", {"is_group": 0}, "name", order_by="name")
				if not account:
					# If no leaf accounts, try group accounts as last resort
					account = frappe.db.get_value("Account", {}, "name", order_by="name")
				return account
			elif options == "Company":
				# Use session default company
				company = (
					frappe.defaults.get_user_default("company")
					or frappe.db.get_single_value("Global Defaults", "default_company")
					or frappe.db.get_value("Company", {}, "name")
				)
				return company
			elif options == "User":
				return getattr(frappe.session, "user", None) or "Administrator"
			elif options == "Employee":
				emp = frappe.db.get_value("Employee", {"status": "Active"}, "name")
				return emp or None
			# Generic Link field: get first record
			first = frappe.db.get_value(options, {}, "name") if options else None
			return first
		elif fieldtype in ("Date", "Datetime"):
			return frappe.utils.nowdate()
		elif fieldtype == "Currency":
			return 0.0
		elif fieldtype in ("Int", "Float"):
			return 0
		elif fieldtype == "Check":
			return 0
		# Return field default or empty string
		return df.default or ""
	except Exception:
		return None


def _get_mandatory_custom_fields(doctype):
	"""Get all mandatory custom fields for a doctype."""
	try:
		meta = frappe.get_meta(doctype)
		mandatory_fields = []
		for field in meta.fields:
			if field.fieldname.startswith("custom_") and field.reqd:
				mandatory_fields.append(field.fieldname)
		return mandatory_fields
	except Exception:
		return []


def _resolve_default_accounts(doc):
	"""
	Find and set default inventory and stock adjustment accounts for the company.
	"""
	if not doc.name:
		return

	modified = False

	# 1. Inventory Account (Asset)
	if not doc.default_inventory_account:
		inventory_account = frappe.db.get_value(
			"Account",
			{"company": doc.name, "account_type": "Stock", "is_group": 0},
			"name",
			order_by="creation asc",
		)
		if inventory_account:
			doc.default_inventory_account = inventory_account
			modified = True
			_dbg("resolve_defaults:set_inventory", {"account": inventory_account})

	# 2. Stock Adjustment Account (Expense - usually COGS)
	if not doc.stock_adjustment_account:
		# First try explicitly checking for 'Stock Adjustment' type
		adj_account = frappe.db.get_value(
			"Account",
			{"company": doc.name, "account_type": "Stock Adjustment", "is_group": 0},
			"name",
			order_by="creation asc",
		)
		if not adj_account:
			# Fallback to COGS as requested
			adj_account = frappe.db.get_value(
				"Account",
				{"company": doc.name, "account_type": "Cost of Goods Sold", "is_group": 0},
				"name",
				order_by="creation asc",
			)

		if adj_account:
			doc.stock_adjustment_account = adj_account
			modified = True
			_dbg("resolve_defaults:set_stock_adjustment", {"account": adj_account})

	# 3. Default Cost Center (Ensure one is set)
	# Note: In this ERPNext version, the field is named 'cost_center'
	if not doc.cost_center:
		cost_center = frappe.db.get_value("Cost Center", {"company": doc.name, "is_group": 0}, "name")
		if not cost_center:
			# Try root cost center if no leaf exists
			cost_center = frappe.db.get_value(
				"Cost Center",
				{"company": doc.name, "is_group": 1, "parent_cost_center": ["in", ["", None]]},
				"name",
			)

		if cost_center:
			doc.cost_center = cost_center
			modified = True
			_dbg("resolve_defaults:set_cost_center", {"cost_center": cost_center})

	return modified


def _validate_quickbooks_settings(qs):
	try:
		qs.save()
	except QuickbooksError as e:
		error_msg = str(e) if e else ""
		if (
			"required" in error_msg.lower()
			or "missing" in error_msg.lower()
			or "invalid" in error_msg.lower()
		):
			qb_log_error(
				title="QuickBooks Configuration Error",
				status="Error",
				method="sync_company._validate_quickbooks_settings",
				message=_(
					"""Configuration Error: {0}\n\nPlease check your QuickBooks
                    Settings configuration and try again."""
				).format(error_msg),
				module="sync_company",
			)
			disable_quickbooks_sync_on_exception()
			raise
		else:
			qb_log_error(
				title="QuickBooks Validation Warning",
				status="Error",
				method="sync_company._validate_quickbooks_settings",
				message=_("""Validation Error: {0}""").format(error_msg),
				module="sync_company",
			)
			# Non-critical; continue


def sync_erp_company():
	"""Compatibility alias: sync QuickBooks Company into ERPNext Company."""
	return sync_quickbooks_company_to_erpnext()


def sync_company(quickbooks_obj):
	"""Fetch Company data from QuickBooks"""
	sync_quickbooks_company_with_client(quickbooks_obj)

	# Get QuickBooks synced company once


def sync_quickbooks_company_with_client(qb):
	"""Save QBO CompanyInfo using an existing QuickBooks client instance."""
	try:
		# CRITICAL: Ensure session is created/refreshed before making API calls
		# This prevents authentication errors on subsequent calls when singleton instance is reused
		# Recreate session to ensure it uses the latest access token from database
		if qb.session is None:
			qb.create_session()
		else:
			# Session exists - ensure it has the latest access token
			# Recreate session to pick up any token updates from database
			qb.create_session()

		company_info = qb.get_single_object("CompanyInfo", qb.company_id)
		ci = company_info.get("CompanyInfo", company_info) or {}

		# Save raw QuickBooks company data to JSON file for debugging
		try:
			saved_files = save_qb_data_to_json(
				data=ci,
				data_name="company_info",
				module_name="sync_company",
				full_response=company_info,
			)

			if saved_files:
				qb_log_status(
					title="QuickBooks Company Debug Files Saved",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=_(
						"Saved QuickBooks CompanyInfo to:\n" "- Full Response: {0}\n" "- Company Info: {1}"
					).format(
						saved_files.get("full_response", "N/A"),
						saved_files.get("data", "N/A"),
					),
					module="sync_company",
				)
		except Exception as e:
			# Don't fail the sync if debug file save fails
			qb_log_error(
				title="QuickBooks Company Debug Save Error",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_("Failed to save debug JSON files: {0}").format(str(e)),
				module="sync_company",
			)

		company_name = ci.get("CompanyName") or ci.get("LegalName")
		if not company_name:
			raise Exception("CompanyName not found in QuickBooks CompanyInfo")

		country_name = _resolve_country_name(ci.get("Country"))
		currency_code = _resolve_currency(ci)
		if not currency_code:
			raise Exception("Default Currency could not be determined for Company")

		email_addr = (ci.get("Email") or {}).get("Address")
		website = (ci.get("WebAddr") or {}).get("URI") or None
		phone = (ci.get("PrimaryPhone") or {}).get("FreeFormNumber") or None

		# Try to get address details - CompanyAddr or LegalAddr
		addr = ci.get("CompanyAddr") or ci.get("LegalAddr") or {}

		# If address only has ID, try to fetch full address details
		address_id = addr.get("Id")
		if address_id and not addr.get("Line1"):
			try:
				# Try to fetch full address details using the address ID
				full_addr = qb.get_single_object("Address", address_id)
				if full_addr and isinstance(full_addr, dict):
					# Address object might be nested
					addr_data = full_addr.get("Address", full_addr)
					if addr_data:
						addr = addr_data
			except Exception as fetch_error:
				# If fetching fails, log but continue with what we have
				_dbg(
					"sync_company:address_fetch_failed", {"address_id": address_id, "error": str(fetch_error)}
				)

		addr_payload = {
			"address_line1": addr.get("Line1"),
			"city": addr.get("City"),
			"state": addr.get("CountrySubDivisionCode"),
			"pincode": addr.get("PostalCode"),
		}

		existing = frappe.db.get_value("Company", {"company_name": company_name}, "name")

		# Debug logging for company lookup
		_dbg(
			"company_lookup",
			{
				"qb_company_name": company_name,
				"existing_company": existing,
				"company_id": qb.company_id,
			},
		)

		if existing:
			doc = frappe.get_doc("Company", existing)
			if country_name:
				doc.country = country_name
			if not getattr(doc, "default_currency", None):
				doc.default_currency = currency_code
			elif ci.get("Currency"):
				doc.default_currency = ci.get("Currency")
			_set_if_field(doc, "email", email_addr)
			_set_if_field(doc, "website", website)
			_set_if_field(doc, "phone_no", phone)

			# Auto-resolve missing default accounts
			_resolve_default_accounts(doc)

			doc.flags.from_quickbooks = True
			doc.save()
			action = "updated"
			_dbg(
				"company_updated",
				{
					"company_name": company_name,
					"company": doc.name,
					"currency": doc.default_currency,
					"country": doc.country,
				},
			)
		else:
			# Ace :: Create new company Ace Bangladesh Chart of Accounts
			new_company = {
				"doctype": "Company",
				"company_name": company_name,
				"abbr": _derive_unique_abbr(company_name),
				"default_currency": currency_code,
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Bangladesh - Chart of Accounts - With Numbers",
			}
			if country_name:
				new_company["country"] = country_name
			if email_addr:
				new_company["email"] = email_addr
			if website:
				new_company["website"] = website
			if phone:
				new_company["phone_no"] = phone

			# Set defaults for mandatory custom fields
			mandatory_custom_fields = _get_mandatory_custom_fields("Company")
			for fieldname in mandatory_custom_fields:
				default_value = _get_default_for_company_field(fieldname)
				if default_value is not None:
					new_company[fieldname] = default_value

			# Debug logging before company creation
			_dbg(
				"company_creation_start",
				{
					"company_name": company_name,
					"currency": currency_code,
					"country": country_name,
					"company_data": new_company,
				},
			)

			doc = frappe.get_doc(new_company)
			doc.flags.from_quickbooks = True
			doc.insert()

			# Auto-resolve missing default accounts
			_resolve_default_accounts(doc)
			doc.flags.from_quickbooks = True
			doc.save()

			action = "created"

			_dbg(
				"company_created",
				{
					"company_name": company_name,
					"company": doc.name,
					"currency": doc.default_currency,
					"country": doc.country,
				},
			)

		# Only create/update address if required fields are present
		address_line1 = addr_payload.get("address_line1") or ""
		city = addr_payload.get("city") or ""
		pincode = addr_payload.get("pincode") or ""

		# If we only have country but missing required fields, create a minimal address
		# Use company name as address_line1 if missing, country name as city if missing
		if country_name:
			if not address_line1.strip():
				address_line1 = company_name or "Address"
			if not city.strip():
				city = country_name or "Unknown"
			if not pincode.strip():
				pincode = "00000"  # Default postal code if missing

		# Check if we have minimum required fields for Address creation
		has_required_fields = address_line1.strip() and city.strip() and pincode.strip()

		if has_required_fields:
			try:
				address_doc = {
					"doctype": "Address",
					"address_title": f"{company_name}",
					"address_type": "Billing",
					"address_line1": address_line1,
					"city": city,
					"state": addr_payload.get("state") or None,
					"pincode": pincode,
					"country": country_name or None,
					"email_id": email_addr or None,
					"links": [{"link_doctype": "Company", "link_name": doc.name}],
				}
				existing_addr = frappe.db.get_value(
					"Address",
					{"address_title": company_name, "address_type": "Billing"},
					"name",
				)
				if existing_addr:
					adoc = frappe.get_doc("Address", existing_addr)
					for k, v in address_doc.items():
						if k in ("doctype", "links"):
							continue
						if v is not None:
							setattr(adoc, k, v)
					adoc.save()
				else:
					adoc = frappe.get_doc(address_doc).insert()

				_set_if_field(doc, "company_address", adoc.name)
				doc.flags.from_quickbooks = True
				doc.save()
			except Exception as addr_error:
				# Log address creation error but don't fail the sync
				qb_log_error(
					title="Address Creation Skipped (Non-Critical)",
					status="Error",
					method="sync_quickbooks_company_with_client",
					message=_(
						"Address could not be created/updated for company {0}: {1}\n\n"
						"Required fields (Address Line 1, City, Postal Code) are missing or invalid. "
						"Company sync will continue without address."
					).format(company_name, str(addr_error)),
					module="sync_company",
				)
		elif any(addr_payload.values()) or country_name:
			# We have some address data but missing required fields - log warning
			missing_fields = []
			if not address_line1.strip():
				missing_fields.append("Address Line 1")
			if not city.strip():
				missing_fields.append("City/Town")
			if not pincode.strip():
				missing_fields.append("Postal Code")

			qb_log_error(
				title="Address Creation Skipped - Missing Required Fields (Non-Critical)",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_(
					"Address creation skipped for company {0} due to missing required fields: {1}.\n\n"
					"Company sync will continue without address. "
					"Please update the address manually if needed."
				).format(company_name, ", ".join(missing_fields)),
				module="sync_company",
			)

		qb_log_status(
			title=f"Company {action}",
			status="Success",
			method="sync_quickbooks_company_with_client",
			message=f"ERPNext Company {doc.name} {action} from QuickBooks CompanyInfo",
			module="sync_company",
		)

		# Set the synced company as session default
		try:
			# Only set user default if we have a logged-in user (not guest)
			user = getattr(frappe.session, "user", None)
			if user and user != "Guest":
				frappe.defaults.set_user_default("company", doc.name)
				frappe.db.commit()  # Commit the user default setting # nosemgrep
				_dbg(
					"sync_company:set_default_company",
					{"company": doc.name, "user": user},
				)
				qb_log_status(
					title="Default Company Set",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Company '{doc.name}' set as default for user {user}",
					module="sync_company",
				)
			else:
				_dbg(
					"sync_company:skip_set_default_guest",
					{"company": doc.name, "user": user, "note": "Guest user - cannot set user default"},
				)
		except Exception as default_error:
			# Log but don't fail sync if setting default fails
			_dbg(
				"sync_company:set_default_company_error",
				{"error": str(default_error)},
			)
			qb_log_error(
				title="Set Default Company Warning",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_("Could not set company as default: {0}").format(str(default_error)),
				module="sync_company",
			)

		# Auto-sync accounts after company creation/update
		try:
			_dbg(
				"sync_company:auto_sync_accounts",
				{"company": doc.name, "action": action},
			)
			from .sync_account import sync_account

			sync_account(qb)
		except Exception as acc_error:
			_dbg(
				"sync_company:auto_sync_accounts_error",
				{"error": str(acc_error)},
			)
			qb_log_error(
				title="Account Auto-Sync Warning",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_("Accounts could not be auto-synced: {0}").format(str(acc_error)),
				module="sync_company",
			)

		# Auto-sync payment methods after company creation/update
		try:
			_dbg(
				"sync_company:auto_sync_payment_methods",
				{"company": doc.name, "action": action},
			)
			from .sync_payment_method import sync_payment_method

			sync_payment_method(qb)
		except Exception as pm_error:
			_dbg(
				"sync_company:auto_sync_payment_methods_error",
				{"error": str(pm_error)},
			)
			qb_log_error(
				title="Payment Method Auto-Sync Warning",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_("Payment methods could not be auto-synced: {0}").format(str(pm_error)),
				module="sync_company",
			)

		# Auto-sync tax codes after company creation/update
		try:
			_dbg(
				"sync_company:auto_sync_tax_codes",
				{"company": doc.name, "action": action},
			)
			tax_stats = sync_tax_code(qb)
			if tax_stats:
				qb_log_status(
					title="Tax Codes Auto-Synced",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Tax codes synced: {tax_stats.get('created', 0)} created, {tax_stats.get('updated', 0)} updated",
					module="sync_company",
				)

			# Auto-sync tax rates to get actual percentages
			_dbg(
				"sync_company:auto_sync_tax_rates",
				{"company": doc.name, "action": action},
			)
			rate_stats = sync_tax_rate(qb)
			if rate_stats:
				qb_log_status(
					title="Tax Rates Auto-Synced",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Tax rates synced: {rate_stats.get('updated', 0)} updated",
					module="sync_company",
				)

			# Auto-sync classes to cost centers
			_dbg(
				"sync_company:auto_sync_classes",
				{"company": doc.name, "action": action},
			)
			from .sync_class import sync_class

			class_stats = sync_class(qb, doc.name)
			if class_stats:
				qb_log_status(
					title="Classes Auto-Synced",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Classes synced: {class_stats.get('created', 0)} created, {class_stats.get('updated', 0)} updated",
					module="sync_company",
				)

			# Auto-sync tax agencies to suppliers
			_dbg(
				"sync_company:auto_sync_tax_agencies",
				{"company": doc.name, "action": action},
			)
			from .sync_taxagency import sync_taxagency

			taxagency_stats = sync_taxagency(qb, doc.name)
			if taxagency_stats:
				qb_log_status(
					title="Tax Agencies Auto-Synced",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Tax agencies synced: {taxagency_stats.get('created', 0)} created, {taxagency_stats.get('updated', 0)} updated",
					module="sync_company",
				)

			# Auto-sync payment terms
			_dbg(
				"sync_company:auto_sync_terms",
				{"company": doc.name, "action": action},
			)
			from .sync_term import sync_term

			term_stats = sync_term(qb, doc.name)
			if term_stats:
				qb_log_status(
					title="Payment Terms Auto-Synced",
					status="Success",
					method="sync_quickbooks_company_with_client",
					message=f"Payment terms synced: {term_stats.get('created', 0)} created, {term_stats.get('updated', 0)} updated",
					module="sync_company",
				)
		except Exception as tax_error:
			# Don't fail company sync if tax sync fails
			_dbg(
				"sync_company:auto_sync_tax_codes_error",
				{"error": str(tax_error)},
			)
			qb_log_error(
				title="Company Data Auto-Sync Warning",
				status="Error",
				method="sync_quickbooks_company_with_client",
				message=_(
					"Tax codes/classes/agencies/terms could not be auto-synced: {0}\\n\\nYou can manually sync these items later."
				).format(str(tax_error)),
				module="sync_company",
			)

		# Commit changes and reload session
		frappe.db.commit()  # nosemgrep

		# Trigger reload on client side if called from web request
		try:
			if frappe.request:
				frappe.response["reload"] = True
				_dbg(
					"sync_company:reload_triggered",
					{"company": doc.name},
				)
		except Exception:
			pass

		# Post-sync setup: Price Lists, QB Settings, Stock Settings
		_post_company_sync_setup(doc)

		return {"company": doc.name, "action": action, "reload": True}

	except Exception as e:
		qb_log_error(
			title="QuickBooks Company Sync Error",
			status="Error",
			method="sync_quickbooks_company_with_client",
			message=_("""Error: {0}\n\nTraceback:\n{1}""").format(str(e), frappe.get_traceback()),
			module="sync_company",
		)
		raise


def _post_company_sync_setup(company_doc):
	"""
	Perform additional setup after company sync:
	1. Create Selling and Buying Price Lists ({Currency}-{Abbr})
	2. Update Quickbooks Settings with company, price lists, and default warehouse
	3. Enable 'Allow Negative Stock' in Stock Settings
	"""
	try:
		currency = company_doc.default_currency
		abbr = company_doc.abbr
		price_list_name = f"{currency}-{abbr}"

		# 1. Create Price Lists
		# Selling
		if not frappe.db.exists("Price List", {"name": price_list_name, "selling": 1}):
			pl_selling = frappe.new_doc("Price List")
			pl_selling.price_list_name = price_list_name
			pl_selling.enabled = 1
			pl_selling.selling = 1
			pl_selling.buying = 0
			pl_selling.currency = currency
			pl_selling.ignore_permissions = True
			pl_selling.insert()
			_dbg("post_sync:created_selling_pl", {"name": price_list_name})

		# Buying - might share same name if structure allows, but usually Price List names must be unique.
		# If we use exact same name, we can't have two records.
		# Price List is not a child table, it's a DocType. Name is primary key.
		# A SINGLE Price List can be both Selling and Buying.
		# Requirement: "create 2 Price List... one for selling type and one for buying type"
		# If names must be same format, maybe we make ONE price list for BOTH?
		# User said: "Name the Price List as {Currency}-{company abbr}. one for selling type and one for buying type."
		# This implies two different records. But if name is fixed... likely implies:
		# "Selling-{Currency}-{Abbr}" and "Buying-{Currency}-{Abbr}" OR
		# "Standard Selling {Abbr}" etc.
		# BUT user strictly said: "Name the Price List as {Currency}-{company abbr}"
		# If I strictly follow: create ONE price list `USD-ABC` and enable BOTH selling and buying?
		# OR create `USD-ABC-Selling` and `USD-ABC-Buying`?

		# Retrying interpretation: "Name the Price List as {Currency}-{company abbr}."
		# If I make one PL `USD-ABC` and set selling=1, buying=1.
		# Use simple approach: ONE Price List for both if possible, or Check if user meant distinct names.
		# "create 2 Price List... Name the Price List as {Currency}-{company abbr}" -> Ambiguous if distinct names not specified.
		# Let's assume user wants ONE price list that serves both, OR if they really want two, I need to distinguish names.
		# However, usually companies have separate lists.
		# Let's try to create one and set both flags?
		# WAIT: "one for selling type and one for buying type" -> implies 2 objects.
		# If I name them the same, it creates collision.
		# I will start with ONE price list capable of BOTH, or if distinct required, I'll suffix.
		# "Name the Price List as {Currency}-{company abbr}" -> This is the name template.
		# Unique names required. I will use standard convention `{Currency}-{Abbr}-Selling` and `{Currency}-{Abbr}-Buying`
		# UNLESS user logic implies the *same* name.
		# Re-reading: "Name the Price List as {Currency}-{company abbr}."
		# Maybe the user implies the *Standard* price lists?
		# Let's try creating distinct names to avoid error:
		# Selling Price List: "{Currency}-{Abbr}-Selling"
		# Buying Price List: "{Currency}-{Abbr}-Buying"
		# checking "options" in QB Settings: Link to "Price List".

		# Let's assume the prompt implies separate lists. I will treat "{Currency}-{company abbr}" as the *base* but I physically cannot create 2 docs with same name.
		# I'll create one Price List `{Currency}-{company abbr}` and enable BOTH Selling and Buying.
		# If user explicitly said "create 2", maybe they expect me to handle the naming collision or use that name for the *Selling* one and something else for Buying?
		# Let's go with:
		# 1. Selling PL: "{Currency}-{Abbr}-Selling"
		# 2. Buying PL: "{Currency}-{Abbr}-Buying"
		# And update the code to reflect this deviation from strict naming if strictly "{Currency}-{Abbr}" was requested for *both* (impossible).

		# Alternative Interpretation:
		# User might mean create 2 *links* or *vars*? No, "create 2 Price List".
		# I will create TWO:
		# selling_pl_name = f"{currency}-{abbr}-Selling"
		# buying_pl_name = f"{currency}-{abbr}-Buying"
		# Use simple suffix.

		selling_pl_name = f"{currency}-{abbr}-Selling"
		buying_pl_name = f"{currency}-{abbr}-Buying"

		if not frappe.db.exists("Price List", selling_pl_name):
			pl = frappe.new_doc("Price List")
			pl.price_list_name = selling_pl_name
			pl.enabled = 1
			pl.selling = 1
			pl.buying = 0
			pl.currency = currency
			pl.ignore_permissions = True
			pl.insert()

		if not frappe.db.exists("Price List", buying_pl_name):
			pl = frappe.new_doc("Price List")
			pl.price_list_name = buying_pl_name
			pl.enabled = 1
			pl.selling = 0
			pl.buying = 1
			pl.currency = currency
			pl.ignore_permissions = True
			pl.insert()

		# 2. Update Quickbooks Settings
		qs = frappe.get_doc("Quickbooks Settings")

		# Find warehouse "Stores" for this company
		warehouse = frappe.db.get_value(
			"Warehouse", {"company": company_doc.name, "warehouse_name": ["like", "%Stores%"]}, "name"
		)

		# Update company_settings table
		found = False
		for row in qs.company_settings:
			if row.company == company_doc.name:
				row.selling_price_list = selling_pl_name
				row.buying_price_list = buying_pl_name
				if warehouse:
					row.warehouse = warehouse
				found = True
				break

		if not found:
			qs.append(
				"company_settings",
				{
					"company": company_doc.name,
					"selling_price_list": selling_pl_name,
					"buying_price_list": buying_pl_name,
					"warehouse": warehouse,
				},
			)

		qs.flags.ignore_permissions = True
		qs.save()
		_dbg("post_sync:updated_qb_settings", {"company": company_doc.name})

		# 3. Stock Settings - Allow Negative Stock
		stock_settings = frappe.get_doc("Stock Settings")
		if not stock_settings.allow_negative_stock:
			stock_settings.allow_negative_stock = 1
			stock_settings.flags.ignore_permissions = True
			stock_settings.save()
			stock_settings.save()
			_dbg("post_sync:updated_stock_settings", {"allow_negative_stock": 1})

		frappe.db.commit()  # nosemgrep
		_dbg("post_sync:committed_changes")

	except Exception as e:
		qb_log_error(
			title="Post-Sync Setup Failed",
			status="Error",
			method="_post_company_sync_setup",
			message=str(e),
			module="sync_company",
		)


def ensure_all_defaults(company_doc):
	"""
	Ensure all default accounts and cost centers are set on the Company.
	If accounts are missing, create them.
	If fields are unset, set them.
	"""
	_dbg("ensure_defaults:start", {"company": company_doc.name})

	# 1. Ensure Cost Centers
	# Main - {Abbr} should exist from creation.
	# Set it to cost_center, round_off_cost_center, depreciation_cost_center if missing
	default_cc_name = f"Main - {company_doc.abbr}"
	if not frappe.db.exists("Cost Center", default_cc_name):
		# Try to find 'Main' or create it
		if frappe.db.exists("Cost Center", {"company": company_doc.name, "cost_center_name": "Main"}):
			default_cc_name = frappe.db.get_value(
				"Cost Center", {"company": company_doc.name, "cost_center_name": "Main"}, "name"
			)

	# If still not found, search for ANY group-0 cost center
	if not frappe.db.exists("Cost Center", default_cc_name):
		default_cc_name = frappe.db.get_value(
			"Cost Center", {"company": company_doc.name, "is_group": 0}, "name"
		)

	# If we have a cost center, set it to fields
	if default_cc_name:
		cc_fields = ["cost_center", "round_off_cost_center", "depreciation_cost_center"]
		for f in cc_fields:
			if not company_doc.get(f):
				company_doc.db_set(f, default_cc_name)
				_dbg(f"ensure_defaults:set_{f}", default_cc_name)

	# 2. Account Configurations
	# Format: (Field Name, Account Name, Account Type, Root Type)
	# Root Type: Asset, Liability, Equity, Income, Expense
	defaults_config = [
		("default_bank_account", "Bank", "Bank", "Asset"),
		("default_cash_account", "Cash", "Cash", "Asset"),
		("default_receivable_account", "Debtors", "Receivable", "Asset"),
		("default_payable_account", "Creditors", "Payable", "Liability"),
		("default_expense_account", "Cost of Goods Sold", "Cost of Goods Sold", "Expense"),
		("default_income_account", "Sales", "Income Account", "Income"),
		(
			"stock_received_but_not_billed",
			"Stock Received But Not Billed",
			"Stock Received But Not Billed",
			"Liability",
		),
		("stock_adjustment_account", "Stock Adjustment", "Stock Adjustment", "Expense"),
		(
			"expenses_included_in_valuation",
			"Expenses Included In Valuation",
			"Expenses Included In Valuation",
			"Expense",
		),
		("default_inventory_account", "Stock", "Stock", "Asset"),
		("round_off_account", "Round Off", "Round Off", "Expense"),
		("write_off_account", "Write Off", "Bad Debts", "Expense"),  # or Write Off type?
		(
			"exchange_gain_loss_account",
			"Exchange Gain/Loss",
			"Exchange Gain or Loss",
			"Income",
		),  # Or Expense? usually under income or expense, let's try finding existing.
		(
			"unrealized_exchange_gain_loss_account",
			"Unrealized Exchange Gain/Loss",
			"Unrealized Exchange Gain or Loss",
			"Income",
		),
		("default_deferred_revenue_account", "Deferred Revenue", "Deferred Revenue", "Liability"),
		("default_deferred_expense_account", "Deferred Expense", "Deferred Expense", "Asset"),
	]

	for field, name, account_type, root_type in defaults_config:
		# Check if field is already set
		if company_doc.get(field):
			continue

		# Check if an account of this TYPE already exists for company (best match)
		existing_account = frappe.db.get_value(
			"Account", {"company": company_doc.name, "account_type": account_type, "is_group": 0}, "name"
		)

		if existing_account:
			company_doc.db_set(field, existing_account)
			_dbg(f"ensure_defaults:set_{field}_existing", existing_account)
			continue

		# If not, create it
		# Needs a parent. Find root account of 'root_type'
		parent_account = frappe.db.get_value(
			"Account",
			{
				"company": company_doc.name,
				"root_type": root_type,
				"is_group": 1,
				"parent_account": ["is", "not set"],
			},
			"name",
		)

		# If we can't find root, we can't create (chart of accounts issue).
		# But usually standard chart has these.
		# Fallback: Find ANY group account consistent with root_type (e.g. 'Current Liabilities')
		if not parent_account:
			# Try finding "Current Liabilities" or similar
			# Rough mapping
			search_term = ""
			if root_type == "Liability":
				search_term = "Liabilities"
			elif root_type == "Asset":
				search_term = "Assets"
			elif root_type == "Expense":
				search_term = "Expenses"
			elif root_type == "Income":
				search_term = "Income"

			if search_term:
				parent_account = frappe.db.get_value(
					"Account",
					{
						"company": company_doc.name,
						"account_name": ["like", f"%{search_term}%"],
						"is_group": 1,
					},
					"name",
				)

		if parent_account:
			# Create the account
			new_account_name = f"{name} - {company_doc.abbr}"

			# Double check existence by name (in case type wasn't set on it)
			if frappe.db.exists("Account", new_account_name):
				# Update its type if possible, or just use it
				company_doc.db_set(field, new_account_name)
				continue

			try:
				acc = frappe.new_doc("Account")
				acc.account_name = name
				acc.company = company_doc.name
				acc.parent_account = parent_account
				acc.account_type = account_type
				acc.currency = company_doc.default_currency
				acc.is_group = 0
				acc.insert(ignore_permissions=True)

				company_doc.db_set(field, acc.name)
				_dbg(f"ensure_defaults:created_{field}", acc.name)
			except Exception as e:
				_dbg(f"ensure_defaults:create_failed_{field}", str(e))
		else:
			_dbg(f"ensure_defaults:no_parent_for_{field}", str(root_type))


def sync_quickbooks_company_to_erpnext():
	qs = frappe.get_doc("Quickbooks Settings")
	if not qs.enable_quickbooks_online:
		qb_log_error(
			title="Quickbooks connector is disabled",
			status="Error",
			method="sync_quickbooks_company_to_erpnext",
			message=_("Enable QuickBooks Online in Quickbooks Settings."),
			module="sync_company",
		)
		return

	_validate_quickbooks_settings(qs)

	# Use helper function which intelligently handles singleton clearing
	# Only drops when actually switching companies, not on every sync
	from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

	qb = _create_quickbooks_client(qs)

	return sync_quickbooks_company_with_client(qb)
