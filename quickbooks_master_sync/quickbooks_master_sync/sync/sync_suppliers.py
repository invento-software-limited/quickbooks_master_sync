from __future__ import unicode_literals

import json

import frappe
from frappe import _

from quickbooks_master_sync.pyqb.quickbooks.batch import batch_create
from quickbooks_master_sync.pyqb.quickbooks.objects.vendor import Vendor

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_exception
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id
from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import (
    _resolve_country_name,
    get_account_from_quickbooks_ref,
    query_with_pagination,
    reset_company_cache,
    save_qb_data_to_json,
)


def _dbg(event, payload=None):
    """Debug logging helper for supplier sync"""
    _dbg_common("sync_suppliers", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_suppliers"):
    """Wrapper for sync_suppliers module"""
    return _get_quickbooks_company_base(
        quickbooks_obj=quickbooks_obj,
        qb_company=qb_company,
        use_cache=True,
        module_name=section,
    )


def sync_suppliers(quickbooks_obj):
    """Fetch Supplier data from QuickBooks"""
    # Reset cache at start of sync
    reset_company_cache()

    _dbg("sync_suppliers:start", {"timestamp": frappe.utils.now()})
    # Get QuickBooks synced company once
    qb_company = _get_quickbooks_company(quickbooks_obj)
    if qb_company:
        _dbg("sync_suppliers:qb_company", {"company": qb_company})
    else:
        _dbg("sync_suppliers:no_company", {"warning": "No QuickBooks company found"})

    quickbooks_supplier_list = []
    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "failed_suppliers": []}
    supplier_query = """SELECT * FROM Vendor"""
    # Use pagination helper to fetch all suppliers
    get_qb_supplier = query_with_pagination(
        quickbooks_obj,
        supplier_query,
        "Vendor",
        module_name="sync_suppliers",
        data_name="suppliers_list"
    )
    _dbg("sync_suppliers:fetched_all", {"count": len(get_qb_supplier)})

    # NOTE: query_with_pagination() already saved the full QuickBooks response
    # to qb_BISB_suppliers_list_full_response.json. No need to save again.
    sync_qb_suppliers(get_qb_supplier, quickbooks_supplier_list, stats, qb_company)
    _dbg(
        "sync_suppliers:done",
        {
            "created": stats["created"],
            "updated": stats["updated"],
            "skipped": stats.get("skipped", 0),
            "errors": stats.get("errors", 0),
            "total_processed": len(quickbooks_supplier_list),
            "total_fetched": len(get_qb_supplier),
        },
    )

    # Log summary if there were failures
    if stats.get("errors", 0) > 0:
        failed_suppliers = stats.get("failed_suppliers", [])
        error_msg = _(
            "Supplier sync completed with {0} error(s). "
            "{1} suppliers failed to sync. "
            "Please check the Activity Log for details."
        ).format(stats["errors"], stats["errors"])

        if failed_suppliers:
            failed_names = [supp.get("name", "Unknown") for supp in failed_suppliers[:10]]
            error_msg += _("\n\nFailed suppliers (first 10): {0}").format(", ".join(failed_names))

        qb_log_error(
            title=_("Supplier Sync Completed with Errors"),
            status="Error",
            method="sync_suppliers",
            message=error_msg,
            module="sync_suppliers",
            request_data={"failed_suppliers": failed_suppliers[:10]}
        )


def sync_qb_suppliers(
    get_qb_supplier, quickbooks_supplier_list, stats=None, qb_company=None
):
    if stats is None:
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "failed_suppliers": []}

    # Initialize generic Import Tracker
    from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
    tracker = ImportTracker(len(get_qb_supplier), "qb_supplier_import.log", company=qb_company, module_name="SUPPLIER")

    _dbg("sync_qb_suppliers:start", {
        "total_suppliers": len(get_qb_supplier),
        "qb_company": qb_company
    })

    # Performance optimization: Batch commits every 20 suppliers instead of each one
    commit_interval = 20
    last_commit_count = 0

    for idx, qb_supplier in enumerate(get_qb_supplier):
        qb_id = qb_supplier.get("Id")
        # Check if supplier exists (use unique QB ID with company prefix)

        unique_qb_id = make_unique_qb_id(qb_id, qb_company)
        filters = {"quickbooks_supp_id": unique_qb_id}
        if qb_company:
            filters["custom_company"] = qb_company
        existing_name = frappe.db.get_value("Supplier", filters, "name")
        
        # Extract supplier display name for logging
        supplier_display_name = qb_supplier.get("DisplayName") or qb_supplier.get("CompanyName") or qb_supplier.get("name") or "Unknown"
        
        # Log start of processing for this item
        tracker_id = f"{unique_qb_id} ({supplier_display_name})"
        tracker.log_processing("QuickBooks", tracker_id)
        if not existing_name:
            _dbg("sync_qb_suppliers:create", {"idx": idx, "Id": qb_id})
            try:
                result = create_Supplier(
                    qb_supplier, quickbooks_supplier_list, qb_company
                )
                # Check if supplier was actually created
                if result:
                    stats["created"] += 1
                    tracker.log_success("QuickBooks", tracker_id, "CREATED")
                    _dbg(
                        "sync_qb_suppliers:created",
                        {"idx": idx, "Id": qb_id, "created": True},
                    )
                else:
                    stats["skipped"] += 1
                    tracker.log_skip("QuickBooks", tracker_id, "creation_failed_or_skipped")
                    _dbg(
                        "sync_qb_suppliers:skipped",
                        {
                            "idx": idx,
                            "Id": qb_id,
                            "reason": "creation_failed_or_skipped",
                        },
                    )
            except Exception as e:
                stats["errors"] += 1
                supplier_name = qb_supplier.get("DisplayName") or qb_supplier.get("CompanyName") or "Unknown"
                tracker.log_error("QuickBooks", tracker_id, str(e))
                stats["failed_suppliers"].append({
                    "qb_id": qb_id,
                    "name": supplier_name,
                    "error": str(e),
                    "action": "create"
                })
                _dbg(
                    "sync_qb_suppliers:create_error",
                    {"idx": idx, "Id": qb_id, "name": supplier_name, "error": str(e)},
                )
                qb_log_error(
                    title=_("Failed to Create Supplier"),
                    status="Error",
                    method="sync_qb_suppliers",
                    message=_(
                        "Failed to create supplier '{0}' (QuickBooks ID: {1}). "
                        "Error: {2}"
                    ).format(supplier_name, qb_id, str(e)),
                    module="sync_suppliers",
                    request_data=qb_supplier
                )
        else:
            _dbg(
                "sync_qb_suppliers:update_exists",
                {"idx": idx, "Id": qb_id, "name": existing_name},
            )
            try:
                update_Supplier(
                    qb_supplier,
                    existing_name,
                    quickbooks_supplier_list,
                    qb_company,
                )
                stats["updated"] += 1
                tracker.log_success("QuickBooks", tracker_id, "UPDATED")
            except Exception as e:
                stats["errors"] += 1
                supplier_name = qb_supplier.get("DisplayName") or qb_supplier.get("CompanyName") or existing_name
                tracker.log_error("QuickBooks", tracker_id, str(e))
                stats["failed_suppliers"].append({
                    "qb_id": qb_id,
                    "name": supplier_name,
                    "error": str(e),
                    "action": "update"
                })
                _dbg(
                    "sync_qb_suppliers:update_error",
                    {"idx": idx, "Id": qb_id, "name": supplier_name, "error": str(e)},
                )
                qb_log_error(
                    title=_("Failed to Update Supplier"),
                    status="Error",
                    method="sync_qb_suppliers",
                    message=_(
                        "Failed to update supplier '{0}' (QuickBooks ID: {1}). "
                        "Error: {2}"
                    ).format(supplier_name, qb_id, str(e)),
                    module="sync_suppliers",
                    request_data=qb_supplier
                )

        # Batch commit every N suppliers for performance
        current_processed = stats["created"] + stats["updated"] + stats["skipped"] + stats["errors"]
        if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
            frappe.db.commit()
            last_commit_count = current_processed

    # Final commit for any remaining suppliers
    if stats["created"] > 0 or stats["updated"] > 0 or stats["errors"] > 0:
        frappe.db.commit()

    # Log final summary
    tracker.log_summary()

    return stats


def create_Supplier(qb_supplier, quickbooks_supplier_list, qb_company=None):
    """store Supplier data in ERPNEXT

    Returns:
        bool: True if supplier was created successfully, False otherwise
    """
    supplier = None
    try:
        # Use QuickBooks company if provided, otherwise use session default
        resolved_company = (
            qb_company
            or frappe.defaults.get_user_default("company")
            or frappe.db.get_single_value("Global Defaults", "default_company")
            or frappe.db.get_value("Company", {}, "name")
        )
        _dbg(
            "create_Supplier:resolved_company",
            {"resolved_company": resolved_company, "qb_company": qb_company},
        )
        if not resolved_company:
            raise Exception("No Company found to assign to Supplier")

        supplier_name = (
            str(qb_supplier.get("DisplayName"))
            if qb_supplier.get("DisplayName")
            else str(qb_supplier.get("name"))
        )

        # Check if supplier is deleted in QuickBooks (name contains "(deleted)")
        is_deleted = "(deleted)" in supplier_name.lower() if supplier_name else False
        # Ensure "(deleted)" suffix is present if supplier is deleted
        if is_deleted and not supplier_name.lower().endswith("(deleted)"):
            # Remove any existing "(deleted)" from middle and add at end
            supplier_name = supplier_name.replace(" (deleted)", "").replace("(deleted)", "").strip()
            supplier_name = f"{supplier_name} (deleted)"

        # Get QuickBooks supplier ID
        qb_supplier_id = (
            str(qb_supplier.get("Id"))
            if qb_supplier.get("Id")
            else str(qb_supplier.get("value"))
        )

        # First check if supplier with this QuickBooks ID already exists (use unique QB ID)

        unique_qb_id = make_unique_qb_id(qb_supplier_id, resolved_company)
        filters = {"quickbooks_supp_id": unique_qb_id}
        if resolved_company:
            filters["custom_company"] = resolved_company
        existing_by_qb_id = frappe.db.get_value("Supplier", filters, "name")
        if existing_by_qb_id:
            _dbg(
                "create_Supplier:exists_by_qb_id",
                {
                    "quickbooks_supp_id": qb_supplier_id,
                    "existing": existing_by_qb_id,
                    "action": "updating",
                },
            )
            # Update existing supplier with latest QuickBooks data
            update_Supplier(
                qb_supplier,
                existing_by_qb_id,
                quickbooks_supplier_list,
                qb_company,
            )
            return True  # Successfully updated

        # SECOND: Check for duplicate supplier_name WITHIN SAME COMPANY (only if no QB ID match found)
        # CRITICAL: Always filter by company to prevent cross-company data sharing
        name_filters = {"supplier_name": supplier_name}
        if resolved_company:
            name_filters["custom_company"] = resolved_company
        existing_supplier = frappe.db.get_value("Supplier", name_filters, "name")
        if existing_supplier:
            _dbg(
                "create_Supplier:duplicate_name",
                {
                    "supplier_name": supplier_name,
                    "existing": existing_supplier,
                    "company": resolved_company,
                },
            )
            # If duplicate exists but has no quickbooks_supp_id, update it
            existing_qb_id = frappe.db.get_value(
                "Supplier", existing_supplier, "quickbooks_supp_id"
            )
            if not existing_qb_id:
                _dbg(
                    "create_Supplier:duplicate_no_qb_id",
                    {
                        "supplier_name": supplier_name,
                        "existing": existing_supplier,
                        "company": resolved_company,
                        "action": "updating_with_qb_id",
                    },
                )
                update_Supplier(
                    qb_supplier,
                    existing_supplier,
                    quickbooks_supplier_list,
                    qb_company,
                )
                return True  # Successfully updated
            else:
                # Skip if already has quickbooks_supp_id (different QB supplier)
                _dbg(
                    "create_Supplier:skip_duplicate",
                    {
                        "supplier_name": supplier_name,
                        "existing": existing_supplier,
                        "existing_qb_id": existing_qb_id,
                        "new_qb_id": qb_supplier_id,
                        "company": resolved_company,
                        "reason": "already_has_different_quickbooks_supp_id",
                    },
                )
                return False  # Skipped

        # Extract email from PrimaryEmailAddr if available
        email_id = None
        if qb_supplier.get("PrimaryEmailAddr"):
            if isinstance(qb_supplier.get("PrimaryEmailAddr"), dict):
                email_id = qb_supplier["PrimaryEmailAddr"].get("Address", "")
            else:
                email_id = str(qb_supplier.get("PrimaryEmailAddr"))

        # Extract phone numbers
        primary_phone = None
        mobile_phone = None
        fax_number = None
        if qb_supplier.get("PrimaryPhone"):
            if isinstance(qb_supplier.get("PrimaryPhone"), dict):
                primary_phone = qb_supplier["PrimaryPhone"].get("FreeFormNumber", "")
            else:
                primary_phone = str(qb_supplier.get("PrimaryPhone"))
        if qb_supplier.get("Mobile"):
            if isinstance(qb_supplier.get("Mobile"), dict):
                mobile_phone = qb_supplier["Mobile"].get("FreeFormNumber", "")
            else:
                mobile_phone = str(qb_supplier.get("Mobile"))
        # Extract Fax for BIN Number (custom_bin_no)
        if qb_supplier.get("Fax"):
            if isinstance(qb_supplier.get("Fax"), dict):
                fax_number = qb_supplier["Fax"].get("FreeFormNumber", "")
            else:
                fax_number = str(qb_supplier.get("Fax"))

        # Map unsupported supplier types to valid options
        mapped_supplier_type = _("Company")

        # Build supplier payload

        qb_supp_id_raw = qb_supplier.get("Id") or qb_supplier.get("value")
        supplier_payload = {
            "doctype": "Supplier",
            "quickbooks_supp_id": make_unique_qb_id(qb_supp_id_raw, resolved_company),
            "supplier_name": supplier_name,
            "supplier_type": mapped_supplier_type,
            "default_currency": (
                qb_supplier["CurrencyRef"].get("value", "")
                if qb_supplier.get("CurrencyRef")
                else ""
            ),
            # Set both standard and custom company fields
            "custom_company": resolved_company,
        }

        # Add TaxIdentifier (GST/VAT) if available
        if qb_supplier.get("TaxIdentifier"):
            tax_id = str(qb_supplier.get("TaxIdentifier")).strip()
            if tax_id:
                # Try common field names (tax_id is most common in ERPNext)
                # If field doesn't exist, it will be silently ignored
                supplier_payload["tax_id"] = tax_id
                _dbg("create_Supplier:tax_id", {"tax_id": tax_id})

        # Map Disabled from QuickBooks Active
        # IMPORTANT: For deleted suppliers, don't disable - just add "(deleted)" suffix to name
        # This allows transactions to be created for deleted suppliers
        if qb_supplier.get("Active") is not None:
            try:
                meta = frappe.get_meta("Supplier")
                if meta.has_field("disabled"):
                    # Only disable if supplier is inactive AND not deleted
                    # Deleted suppliers should remain enabled to allow transaction creation
                    if is_deleted:
                        # Don't disable deleted suppliers - keep them enabled
                        supplier_payload["disabled"] = 0
                        _dbg("create_Supplier:deleted_supplier_not_disabled", {
                            "supplier_name": supplier_name,
                            "note": "Deleted supplier kept enabled to allow transaction creation"
                        })
                    else:
                        # IMPORTANT: Keep all suppliers enabled in ERPNext regardless of QuickBooks Active status
                        # ERPNext blocks transactions with disabled suppliers, so we keep them all active
                        supplier_payload["disabled"] = 0
            except Exception:
                pass

        # Map Fax to custom_bin_no (BIN Number)
        if fax_number:
            try:
                meta = frappe.get_meta("Supplier")
                if meta.has_field("custom_bin_no"):
                    supplier_payload["custom_bin_no"] = fax_number
                    _dbg("create_Supplier:custom_bin_no", {"bin_no": fax_number})
            except Exception:
                pass

        # DON'T add email_id or mobile_no to supplier document
        # This would trigger ERPNext's automatic contact creation which fails
        # with incomplete data. Instead, we create contacts manually with
        # create_supplier_contact() which has proper validation.
        # if email_id:
        #     try:
        #         meta = frappe.get_meta("Supplier")
        #         if meta.has_field("email_id"):
        #             supplier_payload["email_id"] = email_id
        #     except Exception:
        #         pass
        #
        # if primary_phone:
        #     try:
        #         meta = frappe.get_meta("Supplier")
        #         if meta.has_field("phone"):
        #             supplier_payload["phone"] = primary_phone
        #         elif meta.has_field("mobile_no"):
        #             supplier_payload["mobile_no"] = primary_phone
        #     except Exception:
        #         pass
        #
        # if mobile_phone and mobile_phone != primary_phone:
        #     try:
        #         meta = frappe.get_meta("Supplier")
        #         if meta.has_field("mobile_no"):
        #             supplier_payload["mobile_no"] = mobile_phone
        #     except Exception:
        #         pass

        # Add website from WebAddr.URI if available
        if qb_supplier.get("WebAddr"):
            website = None
            if isinstance(qb_supplier.get("WebAddr"), dict):
                website = qb_supplier["WebAddr"].get("URI", "")
            else:
                website = str(qb_supplier.get("WebAddr"))
            if website:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("website"):
                        supplier_payload["website"] = website
                        _dbg("create_Supplier:website", {"website": website})
                except Exception:
                    pass

        # Add AcctNum to supplier_account_no if available
        if qb_supplier.get("AcctNum"):
            acct_num = str(qb_supplier.get("AcctNum")).strip()
            if acct_num:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("supplier_account_no"):
                        supplier_payload["supplier_account_no"] = acct_num
                        _dbg(
                            "create_Supplier:supplier_account_no",
                            {"acct_num": acct_num},
                        )
                except Exception:
                    pass

        # Add CompanyName to company_name if available
        if qb_supplier.get("CompanyName"):
            company_name_val = str(qb_supplier.get("CompanyName")).strip()
            if company_name_val:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("company_name"):
                        supplier_payload["company_name"] = company_name_val
                        _dbg(
                            "create_Supplier:company_name",
                            {"company_name": company_name_val},
                        )
                except Exception:
                    pass

        # Add PrintOnCheckName to supplier_print_name if available
        if qb_supplier.get("PrintOnCheckName"):
            print_name = str(qb_supplier.get("PrintOnCheckName")).strip()
            if print_name:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("supplier_print_name"):
                        supplier_payload["supplier_print_name"] = print_name
                        _dbg(
                            "create_Supplier:supplier_print_name",
                            {"print_name": print_name},
                        )
                except Exception:
                    pass

        # Combine Title and Suffix to salutation if available
        title = qb_supplier.get("Title", "").strip() if qb_supplier.get("Title") else ""
        suffix = (
            qb_supplier.get("Suffix", "").strip() if qb_supplier.get("Suffix") else ""
        )
        if title or suffix:
            salutation = " ".join(filter(None, [title, suffix])).strip()
            if salutation:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("salutation"):
                        supplier_payload["salutation"] = salutation
                        _dbg("create_Supplier:salutation", {"salutation": salutation})
                except Exception:
                    pass

        _dbg(
            "create_Supplier:payload",
            {
                k: supplier_payload.get(k)
                for k in [
                    "quickbooks_supp_id",
                    "supplier_name",
                    "custom_company",
                ]
            },
        )

        # Create supplier document
        # No need for skip_contact_creation flag since we don't set email_id/mobile_no
        # which are required to trigger automatic contact creation
        supplier = frappe.get_doc(supplier_payload)
        try:
            supplier.insert()
        except frappe.DuplicateEntryError as e:
            # Supplier with this auto-generated ID already exists
            # Extract the duplicate name from the error message
            import re

            error_str = str(e)
            _dbg(
                "create_Supplier:duplicate_entry_caught",
                {
                    "supplier_name": supplier_name,
                    "error": error_str,
                    "action": "attempting_recovery",
                },
            )

            # Try to extract the duplicate supplier name from error
            # Error format: ('Supplier', 'ATL/VEND/2025/00116', ...)
            duplicate_name = None
            match = re.search(r"'([^']+/VEND/[^']+)'", error_str)
            if match:
                duplicate_name = match.group(1)

            # If we found the duplicate name, check if it needs QuickBooks ID
            if duplicate_name:
                _dbg(
                    "create_Supplier:found_duplicate_name",
                    {"duplicate_name": duplicate_name},
                )
                # Get QuickBooks supplier ID we're trying to set
                qb_supplier_id = (
                    str(qb_supplier.get("Id"))
                    if qb_supplier.get("Id")
                    else str(qb_supplier.get("value"))
                )
                # Check if this supplier has a quickbooks_supp_id
                existing_qb_id = frappe.db.get_value(
                    "Supplier", duplicate_name, "quickbooks_supp_id"
                )

                if not existing_qb_id:
                    # Update this supplier with QuickBooks data (including setting qb_id)
                    _dbg(
                        "create_Supplier:updating_duplicate_no_qb_id",
                        {
                            "duplicate_name": duplicate_name,
                            "qb_supplier_id": qb_supplier_id,
                        },
                    )
                    update_Supplier(
                        qb_supplier,
                        duplicate_name,
                        quickbooks_supplier_list,
                        qb_company,
                    )
                    return True  # Successfully updated
                elif existing_qb_id == qb_supplier_id:
                    # Same QuickBooks ID - just update with latest data
                    _dbg(
                        "create_Supplier:updating_same_qb_id",
                        {
                            "duplicate_name": duplicate_name,
                            "qb_supplier_id": qb_supplier_id,
                        },
                    )
                    update_Supplier(
                        qb_supplier,
                        duplicate_name,
                        quickbooks_supplier_list,
                        qb_company,
                    )
                    return True  # Successfully updated
                else:
                    # Supplier already has different QB ID, this is a real duplicate
                    _dbg(
                        "create_Supplier:skip_has_different_qb_id",
                        {
                            "duplicate_name": duplicate_name,
                            "existing_qb_id": existing_qb_id,
                            "new_qb_id": qb_supplier_id,
                        },
                    )
                    return False  # Skip

            # Fallback: try to find by supplier_name
            existing_by_name = frappe.db.get_value(
                "Supplier", {"supplier_name": supplier_name}, "name"
            )
            if existing_by_name:
                _dbg(
                    "create_Supplier:fallback_found_by_name",
                    {"existing_by_name": existing_by_name},
                )
                update_Supplier(
                    qb_supplier,
                    existing_by_name,
                    quickbooks_supplier_list,
                    qb_company,
                )
                return True  # Successfully updated instead

            # If we still can't handle it, skip this supplier
            _dbg(
                "create_Supplier:unhandled_duplicate",
                {"error": error_str, "skipping": True},
            )
            return False  # Skip this supplier to continue sync

        # Map APAccountRef to account (Child Table) if available
        # PRIORITY 1: Use AccountRef from QuickBooks (exact match)
        if supplier and qb_supplier.get("APAccountRef"):
            try:
                account_ref = qb_supplier.get("APAccountRef")
                if account_ref:
                    # Use get_account_from_quickbooks_ref to prioritize AccountRef
                    erpnext_account = get_account_from_quickbooks_ref(
                        account_ref,
                        company=resolved_company,
                        # account_type and root_type not needed - QB ID is unique identifier
                        module_name="sync_suppliers",
                        strict_qb_id_only=True
                    )
                    if erpnext_account:
                        # Add to account child table if it exists
                        meta = frappe.get_meta("Supplier")
                        if meta.has_field("accounts"):
                            supplier.append(
                                "accounts",
                                {
                                    "company": resolved_company,
                                    "account": erpnext_account,
                                },
                            )
                            supplier.save()
                            _dbg(
                                "create_Supplier:account_added",
                                {"account": erpnext_account, "qb_account_ref": account_ref},
                            )
            except Exception as e:
                _dbg(
                    "create_Supplier:account_mapping_error",
                    {"error": str(e), "qb_account_ref": account_ref},
                )

        # Create billing address if available
        if supplier and qb_supplier.get("BillAddr"):
            _dbg(
                "create_Supplier:create_billing_address",
                {"supplier": supplier.name},
            )
            create_supplier_address(supplier, qb_supplier.get("BillAddr"))

        # Create contact if we have proper contact data
        if supplier:
            create_supplier_contact(supplier, qb_supplier)

        # Note: Commit will be batched in sync_qb_suppliers() for better performance
        quickbooks_supplier_list.append(supplier.quickbooks_supp_id)
        _dbg(
            "create_Supplier:success",
            {
                "supplier": supplier.name,
                "quickbooks_supp_id": supplier.quickbooks_supp_id,
            },
        )
        return True  # Successfully created

    except Exception as e:
        _dbg("create_Supplier:error", {"error": str(e)})
        qb_log_exception(
            method="create_Supplier",
            err=e,
            request_data=qb_supplier,
            module="sync_suppliers",
        )
        return False  # Failed to create


def update_Supplier(
    qb_supplier, existing_name, quickbooks_supplier_list, qb_company=None
):
    """Update existing Supplier with latest data from QuickBooks"""
    try:
        supplier = frappe.get_doc("Supplier", existing_name)

        # Resolve company first (needed for unique QB ID generation)
        resolved_company = (
            qb_company
            or frappe.defaults.get_user_default("company")
            or frappe.db.get_single_value("Global Defaults", "default_company")
            or frappe.db.get_value("Company", {}, "name")
        )

        # Get QuickBooks supplier ID
        qb_supplier_id = (
            str(qb_supplier.get("Id"))
            if qb_supplier.get("Id")
            else str(qb_supplier.get("value"))
        )

        # CRITICAL: Update quickbooks_supp_id if it's missing or different (use unique ID)

        unique_qb_supp_id = make_unique_qb_id(qb_supplier_id, resolved_company)
        if not supplier.quickbooks_supp_id or supplier.quickbooks_supp_id != unique_qb_supp_id:
            old_qb_id = supplier.quickbooks_supp_id
            supplier.quickbooks_supp_id = unique_qb_supp_id
            _dbg(
                "update_Supplier:qb_id_updated",
                {
                    "old": old_qb_id,
                    "new": qb_supplier_id,
                    "supplier": supplier.name,
                },
            )

        # Update supplier_name if changed in QuickBooks
        qb_display_name = (
            str(qb_supplier.get("DisplayName"))
            if qb_supplier.get("DisplayName")
            else str(qb_supplier.get("name"))
        )
        if supplier.supplier_name != qb_display_name:
            old_name = supplier.supplier_name
            supplier.supplier_name = qb_display_name
            _dbg(
                "update_Supplier:name_changed",
                {"old": old_name, "new": qb_display_name},
            )

        # Update default currency if available
        if qb_supplier.get("CurrencyRef"):
            new_currency = qb_supplier["CurrencyRef"].get("value", "")
            if new_currency and supplier.default_currency != new_currency:
                supplier.default_currency = new_currency
                _dbg(
                    "update_Supplier:currency_changed",
                    {"currency": new_currency},
                )

        # DON'T update email_id or mobile_no on supplier document
        # This would trigger ERPNext's automatic contact creation which fails
        # with incomplete data. Instead, we create/update contacts manually with
        # create_supplier_contact() which has proper validation.
        #
        # The code below is commented out to prevent automatic contact creation:
        # if qb_supplier.get("PrimaryEmailAddr"):
        #     email_id = None
        #     if isinstance(qb_supplier.get("PrimaryEmailAddr"), dict):
        #         email_id = qb_supplier["PrimaryEmailAddr"].get("Address", "")
        #     else:
        #         email_id = str(qb_supplier.get("PrimaryEmailAddr"))
        #     if email_id:
        #         try:
        #             meta = frappe.get_meta("Supplier")
        #             if (
        #                 meta.has_field("email_id")
        #                 and getattr(supplier, "email_id", None) != email_id
        #             ):
        #                 supplier.email_id = email_id
        #                 _dbg(
        #                     "update_Supplier:email_changed",
        #                     {"email": email_id},
        #                 )
        #         except Exception:
        #             pass
        #
        # if qb_supplier.get("PrimaryPhone"):
        #     phone = None
        #     if isinstance(qb_supplier.get("PrimaryPhone"), dict):
        #         phone = qb_supplier["PrimaryPhone"].get("FreeFormNumber", "")
        #     else:
        #         phone = str(qb_supplier.get("PrimaryPhone"))
        #     if phone:
        #         try:
        #             meta = frappe.get_meta("Supplier")
        #             if meta.has_field("phone"):
        #                 if getattr(supplier, "phone", None) != phone:
        #                     supplier.phone = phone
        #                     _dbg(
        #                         "update_Supplier:phone_changed",
        #                         {"phone": phone},
        #                     )
        #             elif meta.has_field("mobile_no"):
        #                 if getattr(supplier, "mobile_no", None) != phone:
        #                     supplier.mobile_no = phone
        #                     _dbg(
        #                         "update_Supplier:phone_changed",
        #                         {"phone": phone},
        #                     )
        #         except Exception:
        #             pass
        #
        # if qb_supplier.get("Mobile"):
        #     mobile_phone = None
        #     if isinstance(qb_supplier.get("Mobile"), dict):
        #         mobile_phone = qb_supplier["Mobile"].get("FreeFormNumber", "")
        #     else:
        #         mobile_phone = str(qb_supplier.get("Mobile"))
        #     if mobile_phone:
        #         try:
        #             meta = frappe.get_meta("Supplier")
        #             if meta.has_field("mobile_no"):
        #                 if getattr(supplier, "mobile_no", None) != mobile_phone:
        #                     supplier.mobile_no = mobile_phone
        #                     _dbg(
        #                         "update_Supplier:mobile_changed",
        #                         {"mobile": mobile_phone},
        #                     )
        #         except Exception:
        #             pass

        # Update Fax to custom_bin_no (BIN Number)
        if qb_supplier.get("Fax"):
            fax_number = None
            if isinstance(qb_supplier.get("Fax"), dict):
                fax_number = qb_supplier["Fax"].get("FreeFormNumber", "")
            else:
                fax_number = str(qb_supplier.get("Fax"))
            if fax_number:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("custom_bin_no"):
                        if getattr(supplier, "custom_bin_no", None) != fax_number:
                            old_bin = getattr(supplier, "custom_bin_no", None)
                            supplier.custom_bin_no = fax_number
                            _dbg(
                                "update_Supplier:custom_bin_no_changed",
                                {"old": old_bin, "new": fax_number},
                            )
                except Exception:
                    pass

        # Update TaxIdentifier (GST/VAT) if available
        if qb_supplier.get("TaxIdentifier"):
            new_tax_id = str(qb_supplier.get("TaxIdentifier")).strip()
            if new_tax_id:
                # Check if tax_id field exists and update if changed
                if hasattr(supplier, "tax_id"):
                    if supplier.tax_id != new_tax_id:
                        old_tax_id = supplier.tax_id
                        supplier.tax_id = new_tax_id
                        _dbg(
                            "update_Supplier:tax_id_changed",
                            {"old": old_tax_id, "new": new_tax_id},
                        )
                else:
                    # Field doesn't exist, try to set it anyway
                    # (for custom fields)
                    try:
                        supplier.set("tax_id", new_tax_id)
                        _dbg(
                            "update_Supplier:tax_id_set",
                            {"tax_id": new_tax_id},
                        )
                    except Exception:
                        pass  # Field doesn't exist, skip

        # Update disabled from Active (inverse mapping)
        # IMPORTANT: For deleted suppliers, don't disable - just ensure "(deleted)" suffix in name
        if qb_supplier.get("Active") is not None:
            try:
                meta = frappe.get_meta("Supplier")
                if meta.has_field("disabled"):
                    # Check if supplier is deleted in QuickBooks
                    is_deleted = "(deleted)" in qb_display_name.lower() if qb_display_name else False
                    
                    if is_deleted:
                        # Don't disable deleted suppliers - keep them enabled
                        if getattr(supplier, "disabled", None) != 0:
                            supplier.disabled = 0
                            _dbg(
                                "update_Supplier:deleted_supplier_enabled",
                                {
                                    "supplier_name": qb_display_name,
                                    "note": "Deleted supplier kept enabled to allow transaction creation"
                                },
                            )
                        # Ensure "(deleted)" suffix is in supplier name
                        if not supplier.supplier_name.lower().endswith("(deleted)"):
                            supplier.supplier_name = qb_display_name
                    else:
                        # IMPORTANT: Keep all suppliers enabled in ERPNext regardless of QuickBooks Active status
                        # ERPNext blocks transactions with disabled suppliers, so we keep them all active
                        if getattr(supplier, "disabled", None) != 0:
                            supplier.disabled = 0
            except Exception:
                pass

        # Update website from WebAddr.URI if available
        if qb_supplier.get("WebAddr"):
            website = None
            if isinstance(qb_supplier.get("WebAddr"), dict):
                website = qb_supplier["WebAddr"].get("URI", "")
            else:
                website = str(qb_supplier.get("WebAddr"))
            if website:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("website"):
                        if getattr(supplier, "website", None) != website:
                            supplier.website = website
                            _dbg(
                                "update_Supplier:website_changed",
                                {"website": website},
                            )
                except Exception:
                    pass

        # Update AcctNum to supplier_account_no if available
        if qb_supplier.get("AcctNum"):
            acct_num = str(qb_supplier.get("AcctNum")).strip()
            if acct_num:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("supplier_account_no"):
                        if getattr(supplier, "supplier_account_no", None) != acct_num:
                            old_acct = getattr(supplier, "supplier_account_no", None)
                            supplier.supplier_account_no = acct_num
                            _dbg(
                                "update_Supplier:supplier_account_no_changed",
                                {"old": old_acct, "new": acct_num},
                            )
                except Exception:
                    pass

        # Update CompanyName to company_name if available
        if qb_supplier.get("CompanyName"):
            company_name_val = str(qb_supplier.get("CompanyName")).strip()
            if company_name_val:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("company_name"):
                        if getattr(supplier, "company_name", None) != company_name_val:
                            old_company_name = getattr(supplier, "company_name", None)
                            supplier.company_name = company_name_val
                            _dbg(
                                "update_Supplier:company_name_changed",
                                {"old": old_company_name, "new": company_name_val},
                            )
                except Exception:
                    pass

        # Update PrintOnCheckName to supplier_print_name if available
        if qb_supplier.get("PrintOnCheckName"):
            print_name = str(qb_supplier.get("PrintOnCheckName")).strip()
            if print_name:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("supplier_print_name"):
                        if getattr(supplier, "supplier_print_name", None) != print_name:
                            old_print_name = getattr(
                                supplier, "supplier_print_name", None
                            )
                            supplier.supplier_print_name = print_name
                            _dbg(
                                "update_Supplier:supplier_print_name_changed",
                                {"old": old_print_name, "new": print_name},
                            )
                except Exception:
                    pass

        # Update salutation from Title and Suffix if available
        title = qb_supplier.get("Title", "").strip() if qb_supplier.get("Title") else ""
        suffix = (
            qb_supplier.get("Suffix", "").strip() if qb_supplier.get("Suffix") else ""
        )
        if title or suffix:
            salutation = " ".join(filter(None, [title, suffix])).strip()
            if salutation:
                try:
                    meta = frappe.get_meta("Supplier")
                    if meta.has_field("salutation"):
                        if getattr(supplier, "salutation", None) != salutation:
                            old_salutation = getattr(supplier, "salutation", None)
                            supplier.salutation = salutation
                            _dbg(
                                "update_Supplier:salutation_changed",
                                {"old": old_salutation, "new": salutation},
                            )
                except Exception:
                    pass

        # Update custom_company field using QuickBooks company (already resolved at function start)
        if resolved_company:
            if getattr(supplier, "custom_company", None) != resolved_company:
                supplier.custom_company = resolved_company
                _dbg(
                    "update_Supplier:company_changed",
                    {"company": resolved_company, "qb_company": qb_company},
                )

        # Update address if BillAddr changed
        if qb_supplier.get("BillAddr"):
            update_supplier_address(supplier, qb_supplier.get("BillAddr"))

        # Save supplier (no need for flags since we don't set email_id/mobile_no)
        # Map APAccountRef to account (Child Table) if available
        # PRIORITY 1: Use AccountRef from QuickBooks (exact match)
        if qb_supplier.get("APAccountRef"):
            try:
                account_ref = qb_supplier.get("APAccountRef")
                if account_ref:
                    # Use get_account_from_quickbooks_ref to prioritize AccountRef
                    erpnext_account = get_account_from_quickbooks_ref(
                        account_ref,
                        company=resolved_company,
                        # account_type and root_type not needed - QB ID is unique identifier
                        module_name="sync_suppliers",
                        strict_qb_id_only=True
                    )
                    if erpnext_account:
                        # Check if account is already linked
                        account_exists = False
                        if hasattr(supplier, "accounts"):
                            for acc_row in supplier.get("accounts", []):
                                if acc_row.get("account") == erpnext_account and acc_row.get("company") == resolved_company:
                                    account_exists = True
                                    break
                        
                        # Add to account child table if it exists and not already linked
                        if not account_exists:
                            meta = frappe.get_meta("Supplier")
                            if meta.has_field("accounts"):
                                supplier.append(
                                    "accounts",
                                    {
                                        "company": resolved_company,
                                        "account": erpnext_account,
                                    },
                                )
                                _dbg(
                                    "update_Supplier:account_added",
                                    {"account": erpnext_account, "qb_account_ref": account_ref},
                                )
            except Exception as e:
                _dbg(
                    "update_Supplier:account_mapping_error",
                    {"error": str(e), "qb_account_ref": account_ref},
                )

        supplier.save()

        # Create contact if it doesn't exist and we have proper contact data
        create_supplier_contact(supplier, qb_supplier)

        # Note: Commit will be batched in sync_qb_suppliers() for better performance
        quickbooks_supplier_list.append(supplier.quickbooks_supp_id)
        _dbg("update_Supplier:success", {"supplier": supplier.name})

    except Exception as e:
        _dbg("update_Supplier:error", {"error": str(e)})
        qb_log_exception(
            method="update_Supplier",
            err=e,
            request_data=qb_supplier,
            module="sync_suppliers",
        )

    return quickbooks_supplier_list


def update_supplier_address(supplier, address):
    """Update existing address or create if not found"""
    try:
        # Try to find existing address by quickbooks_address_id only
        # Address in ERPNext uses Dynamic Link child table,
        # not direct supplier field
        if not address.get("Id"):
            _dbg("update_supplier_address:no_id", {"address": address})
            return

        existing_addr = frappe.db.get_value(
            "Address",
            {"quickbooks_address_id": str(address.get("Id"))},
            "name",
        )

        if existing_addr:
            adoc = frappe.get_doc("Address", existing_addr)

            # Update address fields
            if address.get("Line1"):
                adoc.address_line1 = address.get("Line1")
            else:
                # Ensure line1 is never empty
                if not adoc.address_line1:
                    adoc.address_line1 = "N/A"

            # Handle additional address lines
            address_lines = []
            for line_num in range(2, 6):
                line_key = f"Line{line_num}"
                if address.get(line_key):
                    address_lines.append(address.get(line_key))
            if address_lines:
                adoc.address_line2 = "\n".join(address_lines)

            # Update city with default if empty
            if address.get("City"):
                adoc.city = address.get("City")
            else:
                if not adoc.city:
                    adoc.city = "N/A"

            if address.get("CountrySubDivisionCode"):
                adoc.state = address.get("CountrySubDivisionCode")

            # Update pincode with default if empty
            if address.get("PostalCode"):
                adoc.pincode = address.get("PostalCode")
            else:
                if not adoc.pincode:
                    adoc.pincode = "000000"

            # Fix: Use Country field, not CountrySubDivisionCode
            # for country lookup
            if address.get("Country"):
                country_name = _resolve_country_name(address.get("Country"))
                if country_name:
                    adoc.country = country_name
            else:
                # Ensure country is set
                if not adoc.country:
                    adoc.country = "Bangladesh"
            if address.get("PrimaryEmailAddr"):
                if isinstance(address.get("PrimaryEmailAddr"), dict):
                    adoc.email_id = address["PrimaryEmailAddr"].get("Address", "")
                else:
                    adoc.email_id = str(address.get("PrimaryEmailAddr"))

            adoc.save()
            _dbg("update_supplier_address:updated", {"address": adoc.name})
        else:
            # Create new address if not found
            _dbg(
                "update_supplier_address:create_new",
                {"qb_id": address.get("Id")},
            )
            create_supplier_address(supplier, address)

    except Exception as e:
        _dbg("update_supplier_address:error", {"error": str(e)})
        qb_log_exception(
            method="update_supplier_address",
            err=e,
            request_data=address,
            module="sync_suppliers",
        )


def create_supplier_address(supplier, address):
    address_title, resolved_address_type = get_address_title_and_type(
        supplier.supplier_name
    )
    try:
        # Build address lines
        address_line1 = address.get("Line1", "")
        address_lines = []
        for line_num in range(2, 6):
            line_key = f"Line{line_num}"
            if address.get(line_key):
                address_lines.append(address.get(line_key))

        # Resolve country name from Country field (not CountrySubDivisionCode)
        country_name = None
        if address.get("Country"):
            country_name = _resolve_country_name(address.get("Country"))

        # Extract email from PrimaryEmailAddr if available
        email_id = None
        if address.get("PrimaryEmailAddr"):
            if isinstance(address.get("PrimaryEmailAddr"), dict):
                email_id = address["PrimaryEmailAddr"].get("Address", "")
            else:
                email_id = str(address.get("PrimaryEmailAddr"))

        # Build address payload with Dynamic Link
        # Ensure all mandatory fields have non-empty values
        city = address.get("City") or "N/A"
        pincode = address.get("PostalCode") or "000000"
        state = address.get("CountrySubDivisionCode") or ""
        country = country_name or "Bangladesh"  # Default to Bangladesh
        line1 = address_line1 or "N/A"  # Ensure address_line1 is never empty

        address_payload = {
            "doctype": "Address",
            "quickbooks_address_id": (
                str(address.get("Id")) if address.get("Id") else None
            ),
            "address_title": address_title,
            "address_type": resolved_address_type,
            "address_line1": line1,
            "city": city,
            "state": state,
            "pincode": pincode,
            "country": country,
            "links": [{"link_doctype": "Supplier", "link_name": supplier.name}],
        }

        # Add address_line2 if there are additional lines
        if address_lines:
            address_payload["address_line2"] = "\n".join(address_lines)

        # Add email if available
        if email_id:
            address_payload["email_id"] = email_id

        doc = frappe.get_doc(address_payload).insert()
        _dbg(
            "create_supplier_address:success",
            {
                "address": doc.name,
                "supplier": supplier.name,
                "type": resolved_address_type,
            },
        )

    except Exception as e:
        _dbg("create_supplier_address:error", {"error": str(e)})
        qb_log_exception(
            method="create_supplier_address",
            err=e,
            request_data=address,
            module="sync_suppliers",
        )
        raise e


def create_supplier_contact(supplier, qb_supplier):
    """Create or update a primary contact for the supplier if we have proper contact data"""
    try:
        # Extract contact information from QuickBooks
        first_name = None
        last_name = None

        # Try to get first_name and last_name from GivenName/FamilyName
        if qb_supplier.get("GivenName"):
            first_name = str(qb_supplier.get("GivenName"))
        if qb_supplier.get("FamilyName"):
            last_name = str(qb_supplier.get("FamilyName"))

        # If no proper name data, skip contact creation
        if not first_name:
            _dbg("create_supplier_contact:no_first_name", {
                "supplier": supplier.name,
                "qb_id": qb_supplier.get("Id")
            })
            return

        # Extract email
        email_id = None
        if qb_supplier.get("PrimaryEmailAddr"):
            if isinstance(qb_supplier.get("PrimaryEmailAddr"), dict):
                email_id = qb_supplier["PrimaryEmailAddr"].get("Address", "")
            else:
                email_id = str(qb_supplier.get("PrimaryEmailAddr"))

        # Extract phone numbers
        phone = None
        mobile_no = None
        if qb_supplier.get("PrimaryPhone"):
            if isinstance(qb_supplier.get("PrimaryPhone"), dict):
                phone = qb_supplier["PrimaryPhone"].get("FreeFormNumber", "")
            else:
                phone = str(qb_supplier.get("PrimaryPhone"))
        if qb_supplier.get("Mobile"):
            if isinstance(qb_supplier.get("Mobile"), dict):
                mobile_no = qb_supplier["Mobile"].get("FreeFormNumber", "")
            else:
                mobile_no = str(qb_supplier.get("Mobile"))

        # Check if contact already exists for this supplier
        existing_contact = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Supplier",
                "link_name": supplier.name,
                "parenttype": "Contact"
            },
            "parent"
        )

        if existing_contact:
            # Update existing contact
            _dbg("create_supplier_contact:updating_existing", {
                "supplier": supplier.name,
                "contact": existing_contact
            })
            contact = frappe.get_doc("Contact", existing_contact)

            # Update name fields
            if first_name and contact.first_name != first_name:
                contact.first_name = first_name
            if last_name and contact.last_name != last_name:
                contact.last_name = last_name

            # Update email if available
            if email_id:
                existing_email = False
                for email_row in contact.email_ids:
                    if email_row.email_id == email_id:
                        existing_email = True
                        break
                if not existing_email:
                    # Clear existing emails and add new one
                    contact.email_ids = []
                    contact.append("email_ids", {"email_id": email_id, "is_primary": 1})

            # Update phone numbers if available
            if phone or mobile_no:
                contact.phone_nos = []
                if mobile_no:
                    contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
                if phone and phone != mobile_no:
                    contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1})

            contact.save(ignore_permissions=True)
            _dbg("create_supplier_contact:updated", {
                "supplier": supplier.name,
                "contact": contact.name
            })
            return

        # Create new contact
        contact_payload = {
            "doctype": "Contact",
            "first_name": first_name,
            "last_name": last_name,
            "links": [{"link_doctype": "Supplier", "link_name": supplier.name}]
        }

        # Add email if available
        if email_id:
            contact_payload["email_ids"] = [{"email_id": email_id, "is_primary": 1}]

        # Add phone if available
        if phone or mobile_no:
            contact_payload["phone_nos"] = []
            if mobile_no:
                contact_payload["phone_nos"].append({"phone": mobile_no, "is_primary_mobile_no": 1})
            if phone and phone != mobile_no:
                contact_payload["phone_nos"].append({"phone": phone, "is_primary_phone": 1})

        contact = frappe.get_doc(contact_payload)
        contact.insert(ignore_permissions=True)

        _dbg("create_supplier_contact:success", {
            "supplier": supplier.name,
            "contact": contact.name,
            "first_name": first_name,
            "last_name": last_name
        })

    except Exception as e:
        # Don't fail the whole sync if contact creation fails
        _dbg("create_supplier_contact:error", {
            "supplier": supplier.name,
            "error": str(e)
        })


def get_address_title_and_type(supplier_name):
    """Get address title and type, handling duplicates."""
    resolved_address_type = _("Billing")
    address_title = supplier_name.strip()

    # Check if address with this title and type already exists
    # Note: We check by title and type only,
    # since link_doctype is in a child table
    # The quickbooks_address_id check in update_supplier_address
    # handles uniqueness
    existing = frappe.db.get_value(
        "Address",
        {
            "address_title": address_title,
            "address_type": resolved_address_type,
        },
        "name",
    )

    # If exists, make title unique by appending type
    if existing:
        address_title = "{0}-{1}".format(address_title, resolved_address_type)

    return address_title, resolved_address_type


"""Sync Supplier From Erpnext to Quickbooks"""


def sync_erp_suppliers():
    """Update quickbooks_supp_id on Supplier from QuickBooks responses."""
    # Ensure a Company context exists before proceeding
    # This uses session defaults since we're pushing from ERPNext to QB
    resolved_company = (
        frappe.defaults.get_user_default("company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {}, "name")
    )
    _dbg(
        "sync_erp_suppliers:resolved_company",
        {"resolved_company": resolved_company, "direction": "erpnext_to_qb"},
    )
    if not resolved_company:
        raise Exception("No Company found. Set a default Company before syncing.")

    response_from_quickbooks = sync_erp_suppliers_to_quickbooks()
    if response_from_quickbooks:
        try:
    
            for response_obj in response_from_quickbooks.successes:
                if response_obj:
                    # Get company from supplier to create unique QB ID
                    supplier_company = frappe.db.get_value("Supplier", {"supplier_name": response_obj.DisplayName}, "custom_company")
                    if not supplier_company:
                        supplier_company = frappe.db.get_value("Supplier", {"supplier_name": response_obj.DisplayName}, "company")
                    if supplier_company:
                        unique_qb_id = make_unique_qb_id(str(response_obj.Id), supplier_company)
                        # Fix SQL injection: use parameterized query
                        frappe.db.sql(
                            """
                            UPDATE tabSupplier
                            SET quickbooks_supp_id = %s
                            WHERE supplier_name = %s
                            """,
                            (unique_qb_id, response_obj.DisplayName),
                        )
                    else:
                        # Fallback if company not found
                        frappe.db.sql(
                            """
                            UPDATE tabSupplier
                            SET quickbooks_supp_id = %s
                            WHERE supplier_name = %s
                            """,
                            (str(response_obj.Id), response_obj.DisplayName),
                        )
                    frappe.db.commit()
                else:
                    raise _("Does not get any response from quickbooks")
        except Exception as e:
            qb_log_exception(
                method="sync_erp_suppliers",
                err=e,
                request_data=locals().get("response_obj"),
                module="sync_suppliers",
            )


def sync_erp_suppliers_to_quickbooks():
    Supplier_list = []
    for erp_supplier in erp_supplier_data():
        try:
            if erp_supplier:
                create_erp_suppliers_to_quickbooks(erp_supplier, Supplier_list)
            else:
                raise _("Supplier does not exist in ERPNext")
        except Exception as e:
            qb_log_exception(
                method="sync_erp_suppliers_to_quickbooks",
                err=e,
                request_data=erp_supplier,
                module="sync_suppliers",
            )
    results = batch_create(Supplier_list)
    return results


def erp_supplier_data():
    erp_supplier = frappe.db.sql(
        (
            "select `supplier_name` from `tabSupplier` "
            "WHERE  quickbooks_supp_id IS NULL"
        ),
        as_dict=1,
    )
    return erp_supplier


def create_erp_suppliers_to_quickbooks(erp_supplier, Supplier_list):
    supplier_obj = Vendor()
    supplier_obj.CompanyName = erp_supplier.supplier_name
    supplier_obj.DisplayName = erp_supplier.supplier_name
    supplier_obj.save()
    Supplier_list.append(supplier_obj)
    return Supplier_list


def get_all_suppliers_json(
    source="quickbooks", quickbooks_obj=None, include_addresses=True
):
    """
    Get all suppliers as JSON.

    Args:
        source (str): "erpnext" to get from ERPNext,
            "quickbooks" to get from QuickBooks
        quickbooks_obj: QuickBooks client instance
            (required if source="quickbooks")
        include_addresses (bool): Whether to include address details

    Returns:
        str: JSON string of all suppliers
    """
    if source == "erpnext":
        return get_erpnext_suppliers_json(include_addresses=include_addresses)
    elif source == "quickbooks":
        if not quickbooks_obj:
            raise Exception("quickbooks_obj is required when source='quickbooks'")
        return get_quickbooks_suppliers_json(
            quickbooks_obj, include_addresses=include_addresses
        )
    else:
        raise Exception("source must be 'erpnext' or 'quickbooks'")


def get_erpnext_suppliers_json(include_addresses=True):
    """Get all ERPNext suppliers as JSON."""
    try:
        # Get Supplier meta to check which fields exist
        meta = frappe.get_meta("Supplier")
        available_fields = [f.fieldname for f in meta.fields]

        # Build list of standard fields that exist
        standard_fields = [
            "name",
            "supplier_name",
            "supplier_type",
            "default_currency",
            "quickbooks_supp_id",
            "email_id",
            "mobile_no",
            "phone",
            "website",
            "supplier_account_no",
            "company_name",
            "supplier_print_name",
            "salutation",
            "disabled",
        ]

        # Only include fields that actually exist
        fields_to_query = [
            field for field in standard_fields if field in available_fields
        ]

        # Also check for company field (might be standard or custom)
        if "company" in available_fields:
            fields_to_query.append("company")

        suppliers = frappe.get_all(
            "Supplier", fields=fields_to_query, order_by="supplier_name"
        )

        result = []
        for supplier in suppliers:
            supplier_dict = supplier.copy()

            # Get full document to access all fields including custom ones
            try:
                doc = frappe.get_doc("Supplier", supplier.name)
                # Add any missing standard fields that exist
                additional_fields = [
                    "company",
                    "email_id",
                    "mobile_no",
                    "phone",
                    "website",
                    "supplier_account_no",
                    "company_name",
                    "supplier_print_name",
                    "salutation",
                    "disabled",
                ]
                for field in additional_fields:
                    if field in available_fields and field not in supplier_dict:
                        if hasattr(doc, field):
                            supplier_dict[field] = getattr(doc, field, None)

                # Add custom fields
                for field in meta.fields:
                    if field.fieldname.startswith("custom_"):
                        if hasattr(doc, field.fieldname):
                            supplier_dict[field.fieldname] = getattr(
                                doc, field.fieldname, None
                            )
            except Exception:
                pass

            # Include addresses if requested
            if include_addresses:
                addresses = frappe.get_all(
                    "Dynamic Link",
                    filters={
                        "link_doctype": "Supplier",
                        "link_name": supplier.name,
                        "parenttype": "Address",
                    },
                    fields=["parent"],
                )
                address_list = []
                for addr_link in addresses:
                    try:
                        addr_doc = frappe.get_doc("Address", addr_link.parent)
                        address_dict = {
                            "name": addr_doc.name,
                            "address_title": addr_doc.address_title,
                            "address_type": addr_doc.address_type,
                            "address_line1": addr_doc.address_line1,
                            "address_line2": addr_doc.address_line2,
                            "city": addr_doc.city,
                            "state": addr_doc.state,
                            "pincode": addr_doc.pincode,
                            "country": addr_doc.country,
                            "email_id": addr_doc.email_id,
                            "quickbooks_address_id": getattr(
                                addr_doc, "quickbooks_address_id", None
                            ),
                        }
                        address_list.append(address_dict)
                    except Exception:
                        pass
                supplier_dict["addresses"] = address_list

            result.append(supplier_dict)

        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        _dbg("get_erpnext_suppliers_json:error", {"error": str(e)})
        raise


def get_quickbooks_suppliers_json(quickbooks_obj, include_addresses=True):
    """Get all QuickBooks suppliers as JSON."""
    try:
        _dbg("get_quickbooks_suppliers_json:start")
        supplier_query = """SELECT * FROM Vendor"""
        qb_supplier = quickbooks_obj.query(supplier_query)
        get_qb_supplier = qb_supplier["QueryResponse"].get("Vendor", [])

        if not isinstance(get_qb_supplier, list):
            get_qb_supplier = [get_qb_supplier]

        result = []
        for qb_supp in get_qb_supplier:
            supplier_dict = {
                "Id": qb_supp.get("Id"),
                "SyncToken": qb_supp.get("SyncToken"),
                "DisplayName": qb_supp.get("DisplayName"),
                "CompanyName": qb_supp.get("CompanyName"),
                "Active": qb_supp.get("Active"),
                "Balance": qb_supp.get("Balance"),
            }

            # Add currency if available
            if qb_supp.get("CurrencyRef"):
                supplier_dict["CurrencyRef"] = {
                    "value": qb_supp["CurrencyRef"].get("value"),
                    "name": qb_supp["CurrencyRef"].get("name"),
                }

            # Add tax identifier if available
            if qb_supp.get("TaxIdentifier"):
                supplier_dict["TaxIdentifier"] = qb_supp.get("TaxIdentifier")

            # Add AcctNum if available
            if qb_supp.get("AcctNum"):
                supplier_dict["AcctNum"] = qb_supp.get("AcctNum")

            # Add Title and Suffix if available
            if qb_supp.get("Title"):
                supplier_dict["Title"] = qb_supp.get("Title")
            if qb_supp.get("Suffix"):
                supplier_dict["Suffix"] = qb_supp.get("Suffix")

            # Add PrintOnCheckName if available
            if qb_supp.get("PrintOnCheckName"):
                supplier_dict["PrintOnCheckName"] = qb_supp.get("PrintOnCheckName")

            # Add email if available
            if qb_supp.get("PrimaryEmailAddr"):
                if isinstance(qb_supp.get("PrimaryEmailAddr"), dict):
                    supplier_dict["PrimaryEmailAddr"] = {
                        "Address": qb_supp["PrimaryEmailAddr"].get("Address")
                    }
                else:
                    supplier_dict["PrimaryEmailAddr"] = qb_supp.get("PrimaryEmailAddr")

            # Add website from WebAddr if available
            if qb_supp.get("WebAddr"):
                if isinstance(qb_supp.get("WebAddr"), dict):
                    web_uri = qb_supp["WebAddr"].get("URI")
                    if web_uri:
                        supplier_dict["WebAddr"] = {"URI": web_uri}
                else:
                    supplier_dict["WebAddr"] = qb_supp.get("WebAddr")

            # Add phone numbers if available
            for phone_field in [
                "PrimaryPhone",
                "Mobile",
                "AlternatePhone",
                "Fax",
            ]:
                if qb_supp.get(phone_field):
                    if isinstance(qb_supp.get(phone_field), dict):
                        supplier_dict[phone_field] = {
                            "FreeFormNumber": qb_supp[phone_field].get("FreeFormNumber")
                        }
                    else:
                        supplier_dict[phone_field] = qb_supp.get(phone_field)

            # Include addresses if requested
            if include_addresses:
                addresses = {}
                for addr_type in ["BillAddr", "ShipAddr"]:
                    if qb_supp.get(addr_type):
                        addr = qb_supp[addr_type]
                        addresses[addr_type] = {
                            "Id": addr.get("Id"),
                            "Line1": addr.get("Line1"),
                            "Line2": addr.get("Line2"),
                            "Line3": addr.get("Line3"),
                            "Line4": addr.get("Line4"),
                            "Line5": addr.get("Line5"),
                            "City": addr.get("City"),
                            "CountrySubDivisionCode": addr.get(
                                "CountrySubDivisionCode"
                            ),
                            "Country": addr.get("Country"),
                            "PostalCode": addr.get("PostalCode"),
                        }
                supplier_dict["addresses"] = addresses

            result.append(supplier_dict)

        _dbg("get_quickbooks_suppliers_json:done", {"count": len(result)})
        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        _dbg("get_quickbooks_suppliers_json:error", {"error": str(e)})
        raise


@frappe.whitelist()
def get_suppliers_json_api(source="erpnext", include_addresses=True):
    """
    API endpoint to get all suppliers as JSON.
    Can be called via REST API or from Python.

    Args:
        source (str): "erpnext" or "quickbooks"
        include_addresses (bool): Whether to include address details

    Returns:
        dict: JSON response with suppliers data
    """
    try:
        if source == "quickbooks":
            # Get QuickBooks client
            from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

            quickbooks_settings = frappe.get_doc("Quickbooks Settings")
            quickbooks_obj = _create_quickbooks_client(quickbooks_settings)
            json_data = get_quickbooks_suppliers_json(
                quickbooks_obj, include_addresses=include_addresses
            )
        else:
            json_data = get_erpnext_suppliers_json(include_addresses=include_addresses)

        return {
            "success": True,
            "source": source,
            "count": len(json.loads(json_data)),
            "data": json.loads(json_data),
        }
    except Exception as e:
        frappe.log_error(
            f"Error getting suppliers JSON: {str(e)}", "get_suppliers_json_api"
        )
        return {
            "success": False,
            "error": str(e),
            "source": source,
        }
