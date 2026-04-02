try:  # Python 3
	import http.client as httplib
	from urllib.parse import parse_qsl
except ImportError:  # Python 2
	import httplib
	from urlparse import parse_qsl

from .exceptions import QuickbooksException, SevereException

try:
	from rauth import OAuth1Service, OAuth1Session
except ImportError:
	print("Please import Rauth:\n\n")
	print("http://rauth.readthedocs.org/en/latest/\n")
	raise
import json


class QuickBooks:
	"""A wrapper class around Python's Rauth module for Quickbooks the API"""

	access_token = ""
	access_token_secret = ""
	refresh_token = ""  # OAuth 2.0 refresh token
	consumer_key = ""
	consumer_secret = ""
	company_id = 0
	callback_url = ""
	session = None
	sandbox = False
	minorversion = None

	qbService = None

	sandbox_api_url_v3 = "https://sandbox-quickbooks.api.intuit.com/v3"
	api_url_v3 = "https://quickbooks.api.intuit.com/v3"

	request_token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
	access_token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

	authorize_url = "https://appcenter.intuit.com/connect/oauth2"

	request_token = ""
	request_token_secret = ""

	_BUSINESS_OBJECTS = [
		"Account",
		"Attachable",
		"Bill",
		"BillPayment",
		"Class",
		"CompanyInfo",
		"CreditMemo",
		"Customer",
		"Department",
		"Employee",
		"Estimate",
		"Invoice",
		"Item",
		"JournalEntry",
		"Payment",
		"PaymentMethod",
		"Preferences",
		"Purchase",
		"PurchaseOrder",
		"SalesReceipt",
		"TaxCode",
		"TaxRate",
		"Term",
		"TimeActivity",
		"Vendor",
		"VendorCredit",
	]

	__instance = None

	def __new__(cls, **kwargs):
		if QuickBooks.__instance is None:
			QuickBooks.__instance = object.__new__(cls)

		if "consumer_key" in kwargs:
			cls.consumer_key = kwargs["consumer_key"]

		if "consumer_secret" in kwargs:
			cls.consumer_secret = kwargs["consumer_secret"]

		if "access_token" in kwargs:
			cls.access_token = kwargs["access_token"]

		if "access_token_secret" in kwargs:
			cls.access_token_secret = kwargs["access_token_secret"]
			# CRITICAL: In OAuth 2.0, access_token_secret stores the refresh token
			# Set refresh_token from access_token_secret so token refresh works
			if kwargs["access_token_secret"]:
				cls.refresh_token = kwargs["access_token_secret"]

		if "company_id" in kwargs:
			cls.company_id = kwargs["company_id"]

		if "callback_url" in kwargs:
			cls.callback_url = kwargs["callback_url"]

		if "sandbox" in kwargs:
			cls.sandbox = kwargs["sandbox"]

		if "minorversion" in kwargs:
			cls.minorversion = kwargs["minorversion"]

		return QuickBooks.__instance

	@classmethod
	def get_instance(cls):
		return cls.__instance

	def _drop(self):
		"""Drop the singleton instance and reset all class-level authentication fields.

		This is critical for multi-company support to prevent credential mixing.
		"""
		# Reset all class-level authentication and configuration fields
		QuickBooks.access_token = ""
		QuickBooks.access_token_secret = ""
		QuickBooks.refresh_token = ""
		QuickBooks.consumer_key = ""
		QuickBooks.consumer_secret = ""
		QuickBooks.company_id = 0
		QuickBooks.callback_url = ""
		QuickBooks.session = None
		QuickBooks.sandbox = False
		QuickBooks.minorversion = None
		QuickBooks.qbService = None

		# Clear the singleton instance
		QuickBooks.__instance = None

	@property
	def api_url(self):
		if self.sandbox:
			return self.sandbox_api_url_v3
		else:
			return self.api_url_v3

	def create_session(self):
		"""Create session for API requests - supports both OAuth 1.0 and OAuth 2.0"""
		import requests
		from requests.auth import HTTPBasicAuth

		# Check if we have OAuth 2.0 access token (Bear token authentication)
		if self.access_token:
			# OAuth 2.0: Use Bearer token authentication
			# Debug print removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Creating OAuth 2.0 session with Bearer token")
			headers = {
				"Authorization": f"Bearer {self.access_token}",
				"Accept": "application/json",
			}

			class OAuth2SessionWrapper:
				"""Wrapper class to mimic OAuth1Session interface but use OAuth 2.0 Bearer tokens"""

				def __init__(self, access_token, headers):
					self.access_token = access_token
					self.access_token_secret = ""  # Not used in OAuth 2.0
					self.headers = headers
					self.session = requests.Session()
					self.session.headers.update(headers)

				def request(self, method, url, use_oauth=True, company_id=None, *args, **kwargs):
					"""
					Make API request with Bearer token
					Signature matches OAuth1Session.request() interface for compatibility
					- use_oauth: ignored for OAuth 2.0 (always uses Bearer token)
					- company_id: used for URL construction in QuickBooks API
					"""
					# Merge headers
					request_headers = kwargs.get("headers", {}).copy()
					request_headers.update(self.headers)
					kwargs["headers"] = request_headers

					# If company_id is provided and not in URL, it might be needed for URL construction
					# But typically company_id is already in the URL path for QuickBooks API
					return self.session.request(method, url, *args, **kwargs)

				def update_bearer(self, new_access_token):
					self.access_token = new_access_token
					self.headers["Authorization"] = f"Bearer {new_access_token}"
					self.session.headers.update({"Authorization": self.headers["Authorization"]})

			self.session = OAuth2SessionWrapper(self.access_token, headers)
			return self.session

		# Legacy OAuth 1.0 support (fallback)
		elif self.consumer_secret and self.consumer_key and self.access_token_secret:
			# Debug print removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Creating OAuth 1.0 session")
			session = OAuth1Session(
				self.consumer_key,
				self.consumer_secret,
				self.access_token,
				self.access_token_secret,
			)
			self.session = session
			return self.session
		else:
			raise QuickbooksException("Quickbooks authentication fields not set. Cannot create session.")

	def get_authorize_url(self, force_company_selection=False):
		"""
		Returns the OAuth 2.0 authorization URL for QuickBooks.
		OAuth 2.0 doesn't require a request token - we build the URL directly.

		:param force_company_selection: If True, adds prompt=select_account to force company selection screen
		:return URI:
		"""
		from urllib.parse import quote, urlencode

		# Debug prints removed to prevent broken pipe errors in background jobs
		# print("DEBUG: Building OAuth 2.0 authorization URL...")
		# print("DEBUG: Authorize URL base:", self.authorize_url)
		# print("DEBUG: Callback URL:", self.callback_url)
		# print("DEBUG: Consumer Key (Client ID):", self.consumer_key)

		if not self.consumer_key:
			raise QuickbooksException("Consumer Key (Client ID) is required for OAuth 2.0", 10000)

		if not self.callback_url:
			raise QuickbooksException("Callback URL is required for OAuth 2.0", 10000)

		# OAuth 2.0 parameters for QuickBooks
		params = {
			"client_id": self.consumer_key,
			"redirect_uri": self.callback_url,
			"response_type": "code",
			"scope": "com.intuit.quickbooks.accounting openid profile email",
			"state": "quickbooks_oauth_state",  # Optional but recommended for security
		}

		# Add prompt parameter to force company selection screen
		# This ensures users can select a different company even if already authorized
		if force_company_selection:
			params["prompt"] = "select_account"

		# Build the authorization URL
		query_string = urlencode(params)
		authorize_url = f"{self.authorize_url}?{query_string}"

		# Debug prints removed to prevent broken pipe errors in background jobs
		# print("DEBUG: Generated OAuth 2.0 authorization URL:", authorize_url)
		# print("DEBUG: Parameters used:", params)

		# For compatibility, we don't set request_token/secret in OAuth 2.0
		# These will be replaced by authorization_code -> access_token flow
		self.request_token = "oauth2_code_flow"
		self.request_token_secret = ""

		return authorize_url

	def set_up_service(self):
		# Debug prints removed to prevent broken pipe errors in background jobs
		# print("DEBUG: Setting up OAuth service...")
		# print("DEBUG: Request token URL:", self.request_token_url)
		# print("DEBUG: Access token URL:", self.access_token_url)
		# print("DEBUG: Authorize URL:", self.authorize_url)
		# print("DEBUG: Consumer key:", self.consumer_key)
		# print("DEBUG: Consumer secret:", self.consumer_secret[:20] + "..." if self.consumer_secret else "None")

		self.qbService = OAuth1Service(
			name=None,
			consumer_key=self.consumer_key,
			consumer_secret=self.consumer_secret,
			request_token_url=self.request_token_url,
			access_token_url=self.access_token_url,
			authorize_url=self.authorize_url,
			base_url=None,
		)

		# Debug print removed to prevent broken pipe errors in background jobs
		# print("DEBUG: OAuth service created successfully")

	def get_access_tokens(self, authorization_code):
		"""
		Exchange OAuth 2.0 authorization code for access tokens.
		:param authorization_code: the authorization code from OAuth 2.0 callback
		"""
		import base64

		import requests

		# Debug prints removed to prevent broken pipe errors in background jobs
		# print("DEBUG: Exchanging authorization code for access tokens...")
		# print("DEBUG: Authorization code:", authorization_code[:20] + "..." if authorization_code else "None")
		# print("DEBUG: Token endpoint:", self.access_token_url)
		# print("DEBUG: Callback URL:", self.callback_url)

		if not authorization_code:
			raise QuickbooksException("Authorization code is required for OAuth 2.0 token exchange", 10000)

		if not self.consumer_key or not self.consumer_secret:
			raise QuickbooksException("Consumer key and secret are required for OAuth 2.0", 10000)

		# OAuth 2.0 token exchange - use Basic Auth with client_id:client_secret
		credentials = f"{self.consumer_key}:{self.consumer_secret}"
		basic_auth = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

		# Prepare token request
		token_data = {
			"grant_type": "authorization_code",
			"code": authorization_code,
			"redirect_uri": self.callback_url,
		}

		headers = {
			"Accept": "application/json",
			"Content-Type": "application/x-www-form-urlencoded",
			"Authorization": f"Basic {basic_auth}",
		}

		# Debug prints removed to prevent broken pipe errors in background jobs
		# print("DEBUG: Making POST request to token endpoint...")
		# print("DEBUG: Headers (without auth):", {k: v for k, v in headers.items() if k != 'Authorization'})
		# print("DEBUG: Token data:", token_data)

		try:
			response = requests.post(self.access_token_url, data=token_data, headers=headers)

			# Debug prints removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Token exchange response status:", response.status_code)
			# print("DEBUG: Token exchange response headers:", dict(response.headers))
			# print("DEBUG: Token exchange response text:", response.text)

			if response.status_code != 200:
				error_detail = ""
				try:
					error_json = response.json()
					error_detail = error_json
					# Debug print removed to prevent broken pipe errors in background jobs
					# print("DEBUG: Error response JSON:", error_json)
				except Exception:
					error_detail = response.text

				raise QuickbooksException(
					f"Failed to exchange authorization code for tokens. Status: {response.status_code}, Response: {error_detail}",
					response.status_code,
				)

			token_response = response.json()
			# Debug print removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Token response:", {k: v[:50] + "..." if isinstance(v, str) and len(v) > 50 else v
			#                                  for k, v in token_response.items()})

			# OAuth 2.0 uses 'access_token' and 'refresh_token'
			self.access_token = token_response.get("access_token", "")
			self.refresh_token = token_response.get("refresh_token", "")

			# For backward compatibility, set access_token_secret to refresh_token
			# (some code might still reference this)
			self.access_token_secret = self.refresh_token

			# Debug prints removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Successfully obtained access token (length: {})".format(len(self.access_token)))
			# print("DEBUG: Successfully obtained refresh token (length: {})".format(len(self.refresh_token) if self.refresh_token else 0))

			# Create a simple session object for compatibility
			class OAuth2Session:
				def __init__(self, access_token, refresh_token):
					self.access_token = access_token
					self.refresh_token = refresh_token
					# For OAuth 1.0 compatibility
					self.access_token_secret = refresh_token

			return OAuth2Session(self.access_token, self.refresh_token)

		except requests.exceptions.RequestException as e:
			# Debug prints removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Request exception during token exchange:", str(e))
			# import traceback
			# traceback.print_exc()
			raise QuickbooksException(f"Network error during token exchange: {e!s}", 10000)
		except Exception:
			# Debug prints removed to prevent broken pipe errors in background jobs
			# print("DEBUG: Exception during token exchange:", type(e).__name__, str(e))
			# import traceback
			# traceback.print_exc()
			raise

	def make_request(self, request_type, url, request_body=None, content_type="application/json"):
		params = {}

		if self.minorversion:
			params["minorversion"] = self.minorversion
		# Debug print removed to prevent broken pipe errors in background jobs
		# print("params",type (params), params)

		if not request_body:
			request_body = {}

		if self.session is None:
			self.create_session()

		headers = {"Content-Type": content_type, "Accept": "application/json"}

		req = self.session.request(
			request_type,
			url,
			True,
			self.company_id,
			headers=headers,
			params=params,
			data=request_body,
		)

		try:
			result = req.json()
		except Exception:
			raise QuickbooksException(f"Error reading json response: {req.text}", 10000)

		# If unauthorized, try to refresh the token once and retry
		if req.status_code == 401:
			if self._attempt_token_refresh():
				req = self.session.request(
					request_type,
					url,
					True,
					self.company_id,
					headers=headers,
					params=params,
					data=request_body,
				)
				try:
					result = req.json()
				except Exception:
					raise QuickbooksException(f"Error reading json response: {req.text}", 10000)

		# Check for authorization errors in Fault response and attempt refresh
		# This handles cases where QuickBooks returns auth errors as Fault (not 401 status)
		fault = result.get("Fault") or result.get("fault") or result.get("faults")
		if fault:
			# Check if this is an authorization error before handling
			is_auth_error = False
			errors = []
			if isinstance(fault, dict):
				errors = fault.get("Error") or fault.get("error") or fault.get("errors") or []
			if isinstance(errors, dict):
				errors = [errors]
			if isinstance(errors, list):
				for error in errors:
					message = error.get("Message") or error.get("message") or ""
					if (
						"Authorization Failure" in message
						or "Authorization" in message
						or "Authentication" in message
					):
						is_auth_error = True
						break

			# If authorization error, try refresh before raising exception
			if is_auth_error and self._attempt_token_refresh():
				# Token refreshed - retry the request
				req = self.session.request(
					request_type,
					url,
					True,
					self.company_id,
					headers=headers,
					params=params,
					data=request_body,
				)
				try:
					result = req.json()
				except Exception:
					raise QuickbooksException(f"Error reading json response: {req.text}", 10000)
				# Continue to status code check below

		# Use != (not identity) and tolerate responses without a Fault wrapper
		if req.status_code != httplib.OK:
			fault = result.get("Fault") or result.get("fault") or result.get("faults")
			if fault:
				self.handle_exceptions(fault)
			else:
				raise QuickbooksException(
					f"HTTP {req.status_code}: {req.text}",
					req.status_code,
				)
		elif ("Fault" in result) or ("fault" in result):
			self.handle_exceptions(result.get("Fault") or result.get("fault"))
		else:
			return result

	def make_request_query(self, request_type, url, request_body=None, content_type="application/json"):
		params = {}
		if self.minorversion:
			params["minorversion"] = self.minorversion
		params["query"] = request_body

		# Debug logging removed to prevent broken pipe errors in background jobs
		# print("params",type (params), params ,type (params['query']))
		if not request_body:
			request_body = {}

		if self.session is None:
			self.create_session()

		headers = {"Content-Type": content_type, "Accept": "application/json"}

		req = self.session.request(
			request_type,
			url,
			True,
			str(self.company_id),
			headers=headers,
			params=params,
		)
		# req = self.session.request(request_type, url, True, self.company_id, headers=headers, params=params, data=request_body)

		try:
			result = req.json()
		except Exception:
			raise QuickbooksException(f"Error reading json response: {req.text}", 10000)

		# If unauthorized, try to refresh the token once and retry
		if req.status_code == 401:
			if self._attempt_token_refresh():
				req = self.session.request(
					request_type,
					url,
					True,
					str(self.company_id),
					headers=headers,
					params=params,
				)
				try:
					result = req.json()
				except Exception:
					raise QuickbooksException(f"Error reading json response: {req.text}", 10000)

				# After retry, check if there's still an authorization error
				if req.status_code == 401:
					# Still 401 after refresh - tokens are invalid/expired
					raise QuickbooksException(
						f"Authorization Failure: Token refresh succeeded but authorization still failed. Your QuickBooks connection may have expired. Response: {req.text}. Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
						401,
					)

				# Check for authorization errors in retry result
				retry_fault = result.get("Fault") or result.get("fault") or result.get("faults")
				if retry_fault:
					retry_is_auth_error = False
					retry_errors = []
					if isinstance(retry_fault, dict):
						retry_errors = (
							retry_fault.get("Error")
							or retry_fault.get("error")
							or retry_fault.get("errors")
							or []
						)
					if isinstance(retry_errors, dict):
						retry_errors = [retry_errors]
					if isinstance(retry_errors, list):
						for error in retry_errors:
							message = error.get("Message") or error.get("message") or ""
							if (
								"Authorization Failure" in message
								or "Authorization" in message
								or "Authentication" in message
							):
								retry_is_auth_error = True
								break

					if retry_is_auth_error:
						# Still authorization error after refresh - tokens are invalid/expired
						raise QuickbooksException(
							f"Authorization Failure: Token refresh succeeded but authorization still failed. Your QuickBooks connection may have expired. Response: {json.dumps(result)}. Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
							401,
						)
			else:
				# Token refresh failed - raise clear error
				raise QuickbooksException(
					f"Authorization Failure: Unable to refresh access token. Response: {req.text}. Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
					401,
				)

		# Check for authorization errors in Fault response and attempt refresh
		# This handles cases where QuickBooks returns auth errors as Fault (not 401 status)
		fault = result.get("Fault") or result.get("fault") or result.get("faults")
		if fault:
			# Check if this is an authorization error before handling
			is_auth_error = False
			errors = []
			if isinstance(fault, dict):
				errors = fault.get("Error") or fault.get("error") or fault.get("errors") or []
			if isinstance(errors, dict):
				errors = [errors]
			if isinstance(errors, list):
				for error in errors:
					message = error.get("Message") or error.get("message") or ""
					if (
						"Authorization Failure" in message
						or "Authorization" in message
						or "Authentication" in message
					):
						is_auth_error = True
						break

			# If authorization error, try refresh before raising exception
			if is_auth_error and self._attempt_token_refresh():
				# Token refreshed - retry the request
				req = self.session.request(
					request_type,
					url,
					True,
					str(self.company_id),
					headers=headers,
					params=params,
				)
				try:
					result = req.json()
				except Exception:
					raise QuickbooksException(f"Error reading json response: {req.text}", 10000)

				# After retry, check if there's still an authorization error
				retry_fault = result.get("Fault") or result.get("fault") or result.get("faults")
				if retry_fault:
					retry_is_auth_error = False
					retry_errors = []
					if isinstance(retry_fault, dict):
						retry_errors = (
							retry_fault.get("Error")
							or retry_fault.get("error")
							or retry_fault.get("errors")
							or []
						)
					if isinstance(retry_errors, dict):
						retry_errors = [retry_errors]
					if isinstance(retry_errors, list):
						for error in retry_errors:
							message = error.get("Message") or error.get("message") or ""
							if (
								"Authorization Failure" in message
								or "Authorization" in message
								or "Authentication" in message
							):
								retry_is_auth_error = True
								break

					if retry_is_auth_error:
						# Still authorization error after refresh - tokens are invalid/expired
						raise QuickbooksException(
							f"Authorization Failure: Token refresh succeeded but authorization still failed. Response: {json.dumps(result)}. Your QuickBooks connection may have expired (refresh token expired after 100 days). Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
							401,
						)

				# Also check status code of retry - if still 401, tokens are invalid
				if req.status_code == 401:
					raise QuickbooksException(
						f"Authorization Failure: Token refresh succeeded but authorization still failed (HTTP 401). Response: {req.text}. Your QuickBooks connection may have expired. Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
						401,
					)

				# Continue to status code check below - retry was successful
			elif is_auth_error:
				# Token refresh failed - raise clear error
				raise QuickbooksException(
					f"Authorization Failure: Unable to refresh access token. Response: {json.dumps(result)}. Please reconnect to QuickBooks by clicking 'Reconnect to QuickBooks' in QuickBooks Settings.",
					401,
				)

		# Use != and handle cases where QuickBooks returns errors without a Fault key
		if req.status_code != httplib.OK:
			fault = result.get("Fault") or result.get("fault") or result.get("faults")
			if fault:
				self.handle_exceptions(fault)
			else:
				raise QuickbooksException(
					f"HTTP {req.status_code}: {req.text}",
					req.status_code,
				)
		elif ("Fault" in result) or ("fault" in result):
			self.handle_exceptions(result.get("Fault") or result.get("fault"))
		else:
			return result

	def get_single_object(self, qbbo, pk):
		url = self.api_url + f"/company/{self.company_id}/{qbbo.lower()}/{pk}/"
		result = self.make_request_query("GET", url, {})

		return result

	def handle_exceptions(self, results):
		"""Normalize and raise QuickBooks API errors.

		Supports variations like {"Fault": {"Error": [...]}} or lowercase keys.
		"""
		# Normalize container: results may already be the inner fault dict
		fault_dict = results
		if isinstance(results, dict) and ("Fault" in results or "fault" in results):
			fault_dict = results.get("Fault") or results.get("fault")

		errors = []
		if isinstance(fault_dict, dict):
			errors = fault_dict.get("Error") or fault_dict.get("error") or fault_dict.get("errors") or []

		if isinstance(errors, dict):
			errors = [errors]

		if not isinstance(errors, list) or not errors:
			# Fallback: raise generic exception with serialized payload
			try:
				serialized = json.dumps(results)
			except Exception:
				serialized = str(results)
			raise QuickbooksException(serialized, 10000)

		for error in errors:
			message = error.get("Message") or error.get("message") or "QuickBooks API Error"
			detail = error.get("Detail") or error.get("detail") or ""
			code_value = error.get("code") or error.get("Code")
			try:
				code = int(code_value) if code_value is not None else 0
			except Exception:
				# Sometimes code can be a string like "AuthenticationFault"
				code = 0

			if code >= 10000:
				raise SevereException(message, code, detail)
			else:
				raise QuickbooksException(message, code, detail)

	def create_object(self, qbbo, request_body):
		self.isvalid_object_name(qbbo)

		url = self.api_url + f"/company/{self.company_id}/{qbbo.lower()}"
		results = self.make_request("POST", url, request_body)
		# Debug print removed to prevent broken pipe errors in background jobs
		# print(results)
		return results

	def query(self, select):
		url = self.api_url + f"/company/{self.company_id}/query"
		result = self.make_request_query("POST", url, select, content_type="text/plain")
		return result

	def isvalid_object_name(self, object_name):
		if object_name not in self._BUSINESS_OBJECTS:
			raise Exception(f"{object_name} is not a valid QBO Business Object.")

		return True

	def update_object(self, qbbo, request_body):
		url = self.api_url + f"/company/{self.company_id}/{qbbo.lower()}"
		result = self.make_request("POST", url, request_body)

		return result

	def batch_operation(self, request_body):
		url = self.api_url + f"/company/{self.company_id}/batch"
		results = self.make_request("POST", url, request_body)
		return results

	def download_pdf(self, qbbo, item_id):
		url = self.api_url + f"/company/{self.company_id}/{qbbo.lower()}/{item_id}/pdf"

		if self.session is None:
			self.create_session()

		headers = {
			"Content-Type": "application/pdf",
			"Accept": "application/pdf, application/json",
		}

		response = self.session.request("GET", url, True, self.company_id, headers=headers)

		if response.status_code != httplib.OK:
			try:
				json = response.json()
			except Exception:
				raise QuickbooksException(f"Error reading json response: {response.text}", 10000)
			fault = json.get("Fault") or json.get("fault") or json
			self.handle_exceptions(fault)
		else:
			return response.content

	def _attempt_token_refresh(self):
		"""Try to refresh OAuth2 access token using the stored refresh token.

		Returns True if refresh succeeded; False otherwise.

		CRITICAL: This also saves the refreshed tokens back to QuickBooks Settings
		to prevent re-authentication on every sync.
		"""
		import base64

		import requests

		refresh_token = self.refresh_token or self.access_token_secret
		if not refresh_token:
			# Log why refresh failed (for debugging)
			try:
				import frappe

				frappe.log_error(
					message="Token refresh failed: No refresh token available. refresh_token='{}', access_token_secret='{}'".format(
						self.refresh_token or "None", self.access_token_secret or "None"
					),
					title="QuickBooks Token Refresh - No Refresh Token",
				)
			except Exception:
				print("Token refresh failed: No refresh token available.")
			return False

		if not self.consumer_key or not self.consumer_secret:
			try:
				import frappe

				frappe.log_error(
					message="Token refresh failed: Missing consumer credentials",
					title="QuickBooks Token Refresh - Missing Credentials",
				)
			except Exception:
				print("Token refresh failed: Missing consumer credentials")
			return False

		try:
			credentials = f"{self.consumer_key}:{self.consumer_secret}"
			basic_auth = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
			data = {
				"grant_type": "refresh_token",
				"refresh_token": refresh_token,
			}
			headers = {
				"Accept": "application/json",
				"Content-Type": "application/x-www-form-urlencoded",
				"Authorization": f"Basic {basic_auth}",
			}
			resp = requests.post(self.access_token_url, data=data, headers=headers)
			if resp.status_code != 200:
				# Log refresh failure for debugging
				try:
					import frappe

					frappe.log_error(
						message=f"Token refresh API call failed: HTTP {resp.status_code}, Response: {resp.text[:500]}",
						title="QuickBooks Token Refresh - API Error",
					)
				except Exception:
					print(f"Token refresh API call failed: HTTP {resp.status_code}")
				return False
			token_response = resp.json()
			new_access = token_response.get("access_token")
			new_refresh = token_response.get("refresh_token") or refresh_token
			if not new_access:
				# Log missing access token in response
				try:
					import frappe

					frappe.log_error(
						message=f"Token refresh response missing access_token. Response: {str(token_response)[:500]}",
						title="QuickBooks Token Refresh - Invalid Response",
					)
				except Exception:
					print("Token refresh response missing access_token.")
				return False

			# Update tokens in memory
			self.access_token = new_access
			self.refresh_token = new_refresh
			self.access_token_secret = new_refresh  # backward-compat

			# CRITICAL: Save refreshed tokens back to database
			# This prevents having to re-authenticate on every sync
			try:
				import frappe

				if frappe.db:
					quickbooks_settings = frappe.get_doc("Quickbooks Settings")
					quickbooks_settings.access_token = new_access
					quickbooks_settings.access_token_secret = new_refresh
					quickbooks_settings.save(ignore_permissions=True)
					frappe.db.commit()
			except Exception as save_error:
				# Log but don't fail - token refresh still worked in memory
				# This allows sync to continue even if save fails
				try:
					import frappe

					frappe.log_error(title="QuickBooks Token Refresh - Save Warning")
				except Exception:
					print(f"Token refreshed successfully but failed to save to database: {save_error!s}")

			# Log successful refresh
			try:
				import frappe

				frappe.log_error(
					message=f"Token refreshed successfully. New access token length: {len(new_access)}",
					title="QuickBooks Token Refresh - Success",
				)
			except Exception:
				pass

			# Update session bearer if available
			if self.session and hasattr(self.session, "update_bearer"):
				self.session.update_bearer(new_access)
			else:
				# Recreate session with new bearer
				self.create_session()
			return True
		except Exception:
			return False
