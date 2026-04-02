# Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import ast
import http.client as httplib
import json
from time import strftime
from urllib.parse import parse_qsl

import frappe
import urllib3
from frappe import _, msgprint
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_files_path, get_url, nowdate
from rauth import OAuth1Service, OAuth1Session

from quickbooks_master_sync.pyqb.quickbooks import QuickBooks
from quickbooks_master_sync.quickbooks_master_sync.utils.exceptions import QuickbooksError


class QuickbooksSettings(Document):
	pass


@frappe.whitelist(allow_guest=True)
def First_callback(realmId, oauth_verifier=None, code=None, state=None):
	"""OAuth 2.0 callback - accepts 'code' parameter from QuickBooks OAuth 2.0"""
	# OAuth 2.0 uses 'code', OAuth 1.0 uses 'oauth_verifier' (for backward compatibility)
	auth_code = code or oauth_verifier
	login_via_oauth2(realmId, auth_code)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/desk#Form/Quickbooks Settings"


def login_via_oauth2(realmId, auth_code):
	"""Store necessary token's to Setup service - OAuth 2.0 implementation"""

	quickbooks_settings = frappe.get_doc("Quickbooks Settings")

	# Get dynamic callback URL using site URL
	callback_url = get_url(
		"/api/method/quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.First_callback"
	)

	# Get sandbox setting from document (default to True if not set)
	sandbox_mode = cint(quickbooks_settings.get("sandbox", 1))

	# CRITICAL: Clear any existing QuickBooks singleton instance before connecting to new company
	# This prevents "Wrong Cluster" errors when switching between companies
	existing_instance = QuickBooks.get_instance()
	if existing_instance:
		existing_instance._drop()

	quickbooks = QuickBooks(
		sandbox=sandbox_mode,
		consumer_key=quickbooks_settings.consumer_key,
		consumer_secret=quickbooks_settings.consumer_secret,
		callback_url=callback_url,
		minorversion=4,
	)

	# Exchange authorization code for access tokens (OAuth 2.0)
	quickbooks.get_access_tokens(auth_code)

	quickbooks.company_id = realmId

	quickbooks_settings.realm_id = realmId
	quickbooks_settings.access_token = quickbooks.access_token
	# OAuth 2.0 uses refresh_token instead of access_token_secret
	quickbooks_settings.access_token_secret = getattr(quickbooks, "refresh_token", "") or ""
	quickbooks_settings.save()
	frappe.db.commit()

	# Sync company after successful OAuth connection
	# This ensures the company is created/updated in ERPNext when connecting
	try:
		from quickbooks_master_sync.quickbooks_master_sync.sync.sync_company import (
			sync_quickbooks_company_with_client,
		)

		# Use the existing quickbooks object - it already has access_token from get_access_tokens()
		# Make sure company_id is set (it was set on line 57)
		if not quickbooks.company_id:
			quickbooks.company_id = realmId
		sync_quickbooks_company_with_client(quickbooks)
		# sync_quickbooks_company_with_client already logs success/errors via qb_log_status/qb_log_error
		# Note: User default cannot be set in OAuth callback (runs as Guest)
		# Company default will be set when user syncs data (not in guest context)
	except Exception as e:
		# Log error but don't fail the connection - company can be synced manually later
		frappe.log_error(
			message=f"Failed to sync company after QuickBooks connection: {e!s}\n\nTraceback:\n{frappe.get_traceback()}",
			title="QuickBooks Company Sync Error",
		)


@frappe.whitelist(allow_guest=True)
def quickbooks_authentication_popup(consumer_key, consumer_secret, force_company_selection=False):
	"""
	Open new popup window to Connect Quickbooks App to Quickbooks sandbox Account

	Args:
		consumer_key: QuickBooks Consumer Key (Client ID)
		consumer_secret: QuickBooks Consumer Secret
		force_company_selection: If True, forces QuickBooks to show company selection screen
	"""

	quickbooks_settings = frappe.get_doc("Quickbooks Settings")

	# Get dynamic callback URL using site URL
	callback_url = get_url(
		"/api/method/quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.First_callback"
	)

	# Get sandbox setting from document (default to True if not set)
	sandbox_mode = cint(quickbooks_settings.get("sandbox", 1))

	# Convert force_company_selection to boolean if passed as string
	force_company_selection = (
		cint(force_company_selection)
		if isinstance(force_company_selection, str)
		else bool(force_company_selection)
	)

	# CRITICAL: Clear any existing QuickBooks singleton instance before connecting to new company
	# This prevents "Wrong Cluster" errors when switching between companies
	existing_instance = QuickBooks.get_instance()
	if existing_instance:
		existing_instance._drop()

	quickbooks = QuickBooks(
		sandbox=sandbox_mode,
		consumer_key=quickbooks_settings.consumer_key,
		consumer_secret=quickbooks_settings.consumer_secret,
		callback_url=callback_url,
	)

	# Use force_company_selection parameter to show company selection screen
	quickbooks_settings.authorize_url = quickbooks.get_authorize_url(
		force_company_selection=force_company_selection
	)
	quickbooks_settings.request_token = quickbooks.request_token
	quickbooks_settings.request_token_secret = quickbooks.request_token_secret
	quickbooks_settings.save()
	frappe.db.commit()

	# NOTE: Company sync happens in the OAuth callback (First_callback) after tokens are obtained
	# Don't try to sync here - we don't have tokens yet!

	return quickbooks_settings.authorize_url


@frappe.whitelist()
def clear_quickbooks_connection():
	"""
	Clear the current QuickBooks connection to allow re-authentication with a different company.
	This clears realm_id, access_token, and access_token_secret.

	Note: When reconnecting, the system will force QuickBooks to show the company selection screen
	so you can choose a different company. If you want to completely remove the app authorization,
	you may also need to disconnect it from your QuickBooks account settings.
	"""
	quickbooks_settings = frappe.get_doc("Quickbooks Settings")

	# Clear connection fields
	quickbooks_settings.realm_id = ""
	quickbooks_settings.access_token = ""
	quickbooks_settings.access_token_secret = ""
	quickbooks_settings.request_token = ""
	quickbooks_settings.request_token_secret = ""
	quickbooks_settings.authorize_url = ""

	quickbooks_settings.save()
	frappe.db.commit()

	return {
		"status": "success",
		"message": _(
			"QuickBooks connection cleared. When you reconnect, you will be able to select a different company."
		),
	}
