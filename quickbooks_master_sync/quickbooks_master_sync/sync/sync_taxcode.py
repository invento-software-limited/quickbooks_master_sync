from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cstr, flt, get_abbr, nowdate

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_exception
from .sync_utils import _dbg as _dbg_common
from .sync_utils import (
    _get_quickbooks_company,
    query_with_pagination,
    save_qb_data_to_json,
)


def _dbg(event, payload=None):
    """Debug logging helper for taxcode sync"""
    _dbg_common("sync_taxcode", event, payload)


def sync_tax_code(quickbooks_obj):
    """Fetch TaxCode data from QuickBooks and sync to Quickbooks Tax Account table"""

    _dbg("sync_tax_code:start")

    try:
        quickbooks_settings = frappe.get_doc(
            "Quickbooks Settings", "Quickbooks Settings"
        )
    except frappe.DoesNotExistError:
        frappe.throw(
            _(
                "Quickbooks Settings not found. Please configure Quickbooks Settings first."
            )
        )

    tax_code_query = """SELECT * FROM TaxCode"""

    try:
        # Use pagination helper to fetch all tax codes
        get_qb_tax_code = query_with_pagination(
            quickbooks_obj,
            tax_code_query,
            "TaxCode",
            module_name="sync_taxcode",
            data_name="tax_codes_list"
        )

        if not get_qb_tax_code:
            _dbg("sync_tax_code:no_data")
            return

        _dbg("sync_tax_code:fetched", {"count": len(get_qb_tax_code)})

        # Save raw QuickBooks tax code data to JSON file for debugging
        try:
            save_qb_data_to_json(
                data=get_qb_tax_code,
                data_name="tax_codes_list",
                module_name="sync_taxcode",
                full_response={"QueryResponse": {"TaxCode": get_qb_tax_code}},
            )
        except Exception as e:
            _dbg("sync_tax_code:debug_save_error", {"error": str(e)})
            # Don't fail the sync if debug file save fails

        stats = sync_qb_tax_codes(get_qb_tax_code, quickbooks_settings, _get_quickbooks_company(quickbooks_obj))
        _dbg("sync_tax_code:done", stats)

        # Log summary if there were failures
        if stats.get("errors", 0) > 0:
            failed_tax_codes = stats.get("failed_tax_codes", [])
            error_msg = _(
                "Tax code sync completed with {0} error(s). "
                "{1} tax codes failed to sync. "
                "Please check the Activity Log for details."
            ).format(stats["errors"], stats["errors"])

            if failed_tax_codes:
                failed_names = [tc.get("name", "Unknown") for tc in failed_tax_codes[:10]]
                error_msg += _("\n\nFailed tax codes (first 10): {0}").format(", ".join(failed_names))

            qb_log_error(
                title=_("Tax Code Sync Completed with Errors"),
                status="Error",
                method="sync_tax_code",
                message=error_msg,
                module="sync_taxcode",
                request_data={"failed_tax_codes": failed_tax_codes[:10]}
            )

        return stats

    except Exception as e:
        _dbg("sync_tax_code:error", {"error": str(e)})
        qb_log_exception(
            method="sync_tax_code",
            err=e,
            request_data={"query": tax_code_query},
            module="sync_taxcode",
        )


def sync_qb_tax_codes(get_qb_tax_code, quickbooks_settings, company=None):
    """Sync individual tax codes to Quickbooks Tax Account table"""

    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0, "failed_tax_codes": []}
    new_tax_codes = []
    settings_modified = False
    
    # Initialize generic Import Tracker
    from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
    tracker = ImportTracker(len(get_qb_tax_code), "qb_tax_code_import.log", company=company, module_name="TAX_CODE")

    for idx, qb_tax_code in enumerate(get_qb_tax_code):
        try:
            tax_code_id = cstr(qb_tax_code.get("Id"))
            tax_code_name = cstr(qb_tax_code.get("Name", ""))
            
            # Log start of processing
            tracker_id = f"{tax_code_id} ({tax_code_name})"
            tracker.log_processing("QuickBooks", tracker_id)

            if not tax_code_id:
                stats["skipped"] += 1
                tracker.log_skip("QuickBooks", tracker_id, "no_id")
                _dbg("sync_qb_tax_codes:skip_no_id", {"idx": idx})
                continue

            # Extract TaxRate information from TaxCode - both Sales and Purchase
            sales_tax_rate_id = None
            sales_tax_rate_name = None
            purchase_tax_rate_id = None
            purchase_tax_rate_name = None

            # Extract from SalesTaxRateList
            sales_tax_rate_list = qb_tax_code.get("SalesTaxRateList", {})
            if sales_tax_rate_list and isinstance(sales_tax_rate_list, dict):
                tax_rate_details = sales_tax_rate_list.get("TaxRateDetail", [])
                if isinstance(tax_rate_details, dict):
                    tax_rate_details = [tax_rate_details]
                if tax_rate_details and len(tax_rate_details) > 0:
                    tax_rate_ref = tax_rate_details[0].get("TaxRateRef", {})
                    if tax_rate_ref:
                        sales_tax_rate_id = cstr(tax_rate_ref.get("value", ""))
                        sales_tax_rate_name = cstr(tax_rate_ref.get("name", ""))

            # Extract from PurchaseTaxRateList
            purchase_tax_rate_list = qb_tax_code.get("PurchaseTaxRateList", {})
            if purchase_tax_rate_list and isinstance(purchase_tax_rate_list, dict):
                tax_rate_details = purchase_tax_rate_list.get("TaxRateDetail", [])
                if isinstance(tax_rate_details, dict):
                    tax_rate_details = [tax_rate_details]
                if tax_rate_details and len(tax_rate_details) > 0:
                    tax_rate_ref = tax_rate_details[0].get("TaxRateRef", {})
                    if tax_rate_ref:
                        purchase_tax_rate_id = cstr(tax_rate_ref.get("value", ""))
                        purchase_tax_rate_name = cstr(tax_rate_ref.get("name", ""))

            # For backward compatibility, use sales tax rate as primary (if available)
            tax_rate_id = sales_tax_rate_id or purchase_tax_rate_id
            tax_rate_name = sales_tax_rate_name or purchase_tax_rate_name

            # Check if this tax code already exists in Quickbooks Tax Account table
            # If company is provided, check specifically for that company
            # If company is None, check for global mapping (company=None/empty)
            
            if company:
                # Check for company-specific entry
                existing_tax = frappe.db.get_value(
                    "Quickbooks Tax Account",
                    {
                        "parent": "Quickbooks Settings",
                        "quickbooks_tax_id": tax_code_id,
                        "company": company
                    },
                    ["name", "quickbooks_tax", "company"],
                    as_dict=True,
                )
            else:
                # Check for global entry (company is NULL or empty string)
                existing_tax = frappe.db.sql("""
                    SELECT name, quickbooks_tax, company
                    FROM `tabQuickbooks Tax Account`
                    WHERE parent = %s
                    AND quickbooks_tax_id = %s
                    AND (company IS NULL OR company = '')
                    LIMIT 1
                """, (quickbooks_settings.name, tax_code_id), as_dict=True)
                existing_tax = existing_tax[0] if existing_tax else None
            
            # If checking for specific company and not found, DO NOT fall back to global
            # We want to create a specific entry for this company if it doesn't exist


            if existing_tax:
                # Update existing entry if name or active status changed
                existing_doc = frappe.get_doc("Quickbooks Tax Account", existing_tax.name)
                updated = False

                if existing_doc.quickbooks_tax != tax_code_name:
                    existing_doc.quickbooks_tax = tax_code_name
                    updated = True

                # Update Sales TaxRate info if available
                if sales_tax_rate_id and hasattr(existing_doc, "quickbooks_tax_rate_id"):
                    if existing_doc.quickbooks_tax_rate_id != sales_tax_rate_id:
                        existing_doc.quickbooks_tax_rate_id = sales_tax_rate_id
                        updated = True
                if sales_tax_rate_name and hasattr(existing_doc, "quickbooks_tax_rate_name"):
                    if existing_doc.quickbooks_tax_rate_name != sales_tax_rate_name:
                        existing_doc.quickbooks_tax_rate_name = sales_tax_rate_name
                        updated = True

                # Update Purchase TaxRate info if available
                if purchase_tax_rate_id and hasattr(existing_doc, "quickbooks_purchase_tax_rate_id"):
                    if existing_doc.quickbooks_purchase_tax_rate_id != purchase_tax_rate_id:
                        existing_doc.quickbooks_purchase_tax_rate_id = purchase_tax_rate_id
                        updated = True
                if purchase_tax_rate_name and hasattr(existing_doc, "quickbooks_purchase_tax_rate_name"):
                    if existing_doc.quickbooks_purchase_tax_rate_name != purchase_tax_rate_name:
                        existing_doc.quickbooks_purchase_tax_rate_name = purchase_tax_rate_name
                        updated = True

                # For backward compatibility, also update primary tax_rate_id if no sales tax rate
                if not sales_tax_rate_id and purchase_tax_rate_id and hasattr(existing_doc, "quickbooks_tax_rate_id"):
                    if existing_doc.quickbooks_tax_rate_id != purchase_tax_rate_id:
                        existing_doc.quickbooks_tax_rate_id = purchase_tax_rate_id
                        updated = True
                if not sales_tax_rate_name and purchase_tax_rate_name and hasattr(existing_doc, "quickbooks_tax_rate_name"):
                    if existing_doc.quickbooks_tax_rate_name != purchase_tax_rate_name:
                        existing_doc.quickbooks_tax_rate_name = purchase_tax_rate_name
                        updated = True

                if updated:
                    # Ensure unique_key is updated
                    existing_doc.set_unique_key()
                    existing_doc.save(ignore_permissions=True)
                    frappe.db.commit()
                    stats["updated"] += 1
                    _dbg(
                        "sync_qb_tax_codes:updated",
                        {
                            "idx": idx,
                            "tax_code_id": tax_code_id,
                            "name": tax_code_name,
                            "sales_tax_rate_id": sales_tax_rate_id,
                            "purchase_tax_rate_id": purchase_tax_rate_id,
                        },
                    )
                    tracker.log_success("QuickBooks", tracker_id, "UPDATED")
                else:
                    stats["skipped"] += 1
                    _dbg(
                        "sync_qb_tax_codes:skip_no_changes",
                        {"idx": idx, "tax_code_id": tax_code_id},
                    )
                    tracker.log_skip("QuickBooks", tracker_id, "no_changes")
            else:
                # Collect new entry to append later
                # If company is provided, create mapping for that company
                # If company is None, create global mapping
                new_tax_code = {
                    "quickbooks_tax_id": tax_code_id,
                    "quickbooks_tax": tax_code_name,
                    "tax_account": "",  # User will need to map this manually
                    "company": company,  # Use the passed company (or None for global)
                }
                # Add Sales TaxRate info if available (for backward compatibility)
                if sales_tax_rate_id:
                    new_tax_code["quickbooks_tax_rate_id"] = sales_tax_rate_id
                elif purchase_tax_rate_id:
                    # Use purchase tax rate if no sales tax rate exists
                    new_tax_code["quickbooks_tax_rate_id"] = purchase_tax_rate_id
                if sales_tax_rate_name:
                    new_tax_code["quickbooks_tax_rate_name"] = sales_tax_rate_name
                elif purchase_tax_rate_name:
                    # Use purchase tax rate name if no sales tax rate name exists
                    new_tax_code["quickbooks_tax_rate_name"] = purchase_tax_rate_name

                # Add Purchase TaxRate info if available
                if purchase_tax_rate_id:
                    new_tax_code["quickbooks_purchase_tax_rate_id"] = purchase_tax_rate_id
                if purchase_tax_rate_name:
                    new_tax_code["quickbooks_purchase_tax_rate_name"] = purchase_tax_rate_name

                new_tax_codes.append(new_tax_code)
                stats["created"] += 1
                _dbg(
                    "sync_qb_tax_codes:create",
                    {
                        "idx": idx,
                        "tax_code_id": tax_code_id,
                        "name": tax_code_name,
                        "sales_tax_rate_id": sales_tax_rate_id,
                        "purchase_tax_rate_id": purchase_tax_rate_id,
                        "company": company
                    },
                )
                tracker.log_success("QuickBooks", tracker_id, "QUEUED_FOR_CREATION")

        except Exception as e:
            stats["errors"] += 1
            stats["failed_tax_codes"].append({
                "qb_id": tax_code_id,
                "name": tax_code_name,
                "error": str(e)
            })
            tracker.log_error("QuickBooks", tracker_id, str(e))
            _dbg("sync_qb_tax_codes:error", {"idx": idx, "tax_code_id": tax_code_id, "name": tax_code_name, "error": str(e)})
            qb_log_error(
                title=_("Failed to Sync Tax Code"),
                status="Error",
                method="sync_qb_tax_codes",
                message=_(
                    "Failed to sync tax code '{0}' (QuickBooks ID: {1}). "
                    "Error: {2}"
                ).format(tax_code_name, tax_code_id, str(e)),
                module="sync_taxcode",
                request_data=qb_tax_code
            )
            qb_log_exception(
                method="sync_qb_tax_codes",
                err=e,
                request_data=qb_tax_code,
                module="sync_taxcode",
            )

    # Batch append all new tax codes and save once
    # Before appending, check for duplicates in the existing table to prevent duplicates
    if new_tax_codes:
        # Reload settings to get latest state (including any entries added in this session)
        quickbooks_settings.reload()
        
        # Get ALL existing tax IDs to check against (more efficient than checking one by one)
        # Use SQL to get all existing entries at once
        all_existing = frappe.db.sql("""
            SELECT quickbooks_tax_id, company
            FROM `tabQuickbooks Tax Account`
            WHERE parent = %s
            AND quickbooks_tax_id IS NOT NULL
            AND quickbooks_tax_id != ''
        """, (quickbooks_settings.name,), as_dict=True)
        
        # Build sets for fast lookup
        existing_tax_ids = set()  # All tax IDs (for global check)
        existing_tax_ids_with_company = set()  # (tax_id, company) tuples
        
        for entry in all_existing:
            tax_id = entry.get("quickbooks_tax_id")
            entry_company = entry.get("company") or None
            if tax_id:
                existing_tax_ids.add(tax_id)
                # Track tax_id + company combinations (None for global)
                existing_tax_ids_with_company.add((tax_id, entry_company))
        
        # Filter out any that would create duplicates
        filtered_new_tax_codes = []
        seen_in_batch = set()  # Track duplicates within the batch itself
        
        for new_tax_code in new_tax_codes:
            tax_id = new_tax_code.get("quickbooks_tax_id")
            company = new_tax_code.get("company") or None  # Normalize None
            
            if not tax_id:
                continue
            
            # Check for duplicates within the batch first
            batch_key = (tax_id, company)
            if batch_key in seen_in_batch:
                _dbg("sync_qb_tax_codes:skip_duplicate_in_batch", {
                    "tax_id": tax_id,
                    "company": company
                })
                stats["skipped"] += 1
                continue
            
            # Check against existing entries
            # For company-specific mappings, check for same (tax_id, company) combination
            # For global mappings, check only for global entries (company is None/empty)
            if company is None:
                # Check if a global entry already exists (company is None or empty)
                global_exists = any(
                    entry_tax_id == tax_id and (entry_company is None or entry_company == "")
                    for entry_tax_id, entry_company in existing_tax_ids_with_company
                )
                if global_exists:
                    _dbg("sync_qb_tax_codes:skip_duplicate_global", {
                        "tax_id": tax_id,
                        "note": "Skipping - global mapping already exists"
                    })
                    stats["skipped"] += 1
                    continue
            else:
                # For company-specific mappings, check for same (tax_id, company) combination
                if (tax_id, company) in existing_tax_ids_with_company:
                    # Skip - duplicate exists
                    _dbg("sync_qb_tax_codes:skip_duplicate_company", {
                        "tax_id": tax_id,
                        "company": company
                    })
                    stats["skipped"] += 1
                    continue
            
            # Not a duplicate - add to filtered list and mark as seen
            seen_in_batch.add(batch_key)
            filtered_new_tax_codes.append(new_tax_code)
        
        # Only append non-duplicate entries
        if filtered_new_tax_codes:
            for new_tax_code in filtered_new_tax_codes:
                quickbooks_settings.append("taxes", new_tax_code)
            # Save will trigger validate() which sets unique_key for each row
            # Validation will catch any remaining duplicates as a safety net
            try:
                quickbooks_settings.save(ignore_permissions=True)
                settings_modified = True
                # Update created count to reflect actual additions
                stats["created"] = len(filtered_new_tax_codes)
            except Exception as save_error:
                # If save fails due to validation (duplicate), handle gracefully
                error_msg = str(save_error)
                if "duplicate" in error_msg.lower() or "already mapped" in error_msg.lower():
                    _dbg("sync_qb_tax_codes:save_validation_error", {
                        "error": error_msg,
                        "note": "Validation caught duplicate - trying to save entries individually"
                    })
                    # Try to save entries one by one to identify which ones are duplicates
                    successful_additions = 0
                    for new_tax_code in filtered_new_tax_codes:
                        try:
                            # Reload and append one at a time
                            test_doc = frappe.get_doc("Quickbooks Settings", quickbooks_settings.name)
                            test_doc.append("taxes", new_tax_code)
                            test_doc.save(ignore_permissions=True)
                            frappe.db.commit()
                            successful_additions += 1
                        except Exception as individual_error:
                            # This entry is a duplicate - skip it
                            error_msg_individual = str(individual_error)
                            _dbg("sync_qb_tax_codes:skip_individual_duplicate", {
                                "tax_id": new_tax_code.get("quickbooks_tax_id"),
                                "company": new_tax_code.get("company"),
                                "error": error_msg_individual
                            })
                    stats["created"] = successful_additions
                    stats["skipped"] += (len(filtered_new_tax_codes) - successful_additions)
                    if successful_additions > 0:
                        settings_modified = True
                else:
                    # Different error - re-raise
                    raise
        else:
            # All were duplicates - update stats
            stats["skipped"] += len(new_tax_codes)
            stats["created"] = 0

    # Commit all changes
    if settings_modified or stats["updated"] > 0:
        frappe.db.commit()
    
    # Log final summary
    tracker.log_summary()

    _dbg("sync_qb_tax_codes:stats", stats)
    return stats


def sync_tax_rate(quickbooks_obj):
    """Fetch TaxRate data from QuickBooks and sync to Quickbooks Tax Account table"""

    _dbg("sync_tax_rate:start")

    try:
        quickbooks_settings = frappe.get_doc(
            "Quickbooks Settings", "Quickbooks Settings"
        )
    except frappe.DoesNotExistError:
        frappe.throw(
            _(
                "Quickbooks Settings not found. Please configure Quickbooks Settings first."
            )
        )

    tax_rate_query = """SELECT * FROM TaxRate"""

    try:
        # Use pagination helper to fetch all tax rates
        get_qb_tax_rate = query_with_pagination(
            quickbooks_obj,
            tax_rate_query,
            "TaxRate",
            module_name="sync_taxcode",
            data_name="tax_rates_list"
        )

        if not get_qb_tax_rate:
            _dbg("sync_tax_rate:no_data")
            return

        _dbg("sync_tax_rate:fetched", {"count": len(get_qb_tax_rate)})

        # Save raw QuickBooks tax rate data to JSON file for debugging
        try:
            save_qb_data_to_json(
                data=get_qb_tax_rate,
                data_name="tax_rates_list",
                module_name="sync_taxcode",
                full_response={"QueryResponse": {"TaxRate": get_qb_tax_rate}},
            )
        except Exception as e:
            _dbg("sync_tax_rate:debug_save_error", {"error": str(e)})
            # Don't fail the sync if debug file save fails

        # Get company from settings
        company = _get_quickbooks_company(quickbooks_obj)

        stats = sync_qb_tax_rates(get_qb_tax_rate, quickbooks_settings, company)
        _dbg("sync_tax_rate:done", stats)
        return stats

    except Exception as e:
        _dbg("sync_tax_rate:error", {"error": str(e)})
        qb_log_exception(
            method="sync_tax_rate",
            err=e,
            request_data={"query": tax_rate_query},
            module="sync_taxcode",
        )


def sync_qb_tax_rates(get_qb_tax_rate, quickbooks_settings, company):
    """Sync individual tax rates and update existing Quickbooks Tax Account entries"""

    stats = {
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "not_found": 0,
        "templates_created": 0,
        "withholding_created": 0,
    }

    # Check if auto-create is enabled (default to True if field doesn't exist)
    auto_create = getattr(quickbooks_settings, "auto_create_tax_objects", True)
    
    # Initialize generic Import Tracker
    # Using append mode since this is the second phase of tax sync
    from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
    tracker = ImportTracker(len(get_qb_tax_rate), "qb_tax_rate_import.log", company=company, module_name="TAX_RATE")

    for idx, qb_tax_rate in enumerate(get_qb_tax_rate):
        try:
            tax_rate_id   = cstr(qb_tax_rate.get("Id"))
            tax_rate_name = cstr(qb_tax_rate.get("Name", ""))
            rate_value    = flt(qb_tax_rate.get("RateValue", 0))
            description   = cstr(qb_tax_rate.get("Description", ""))
            
            # Log start of processing
            tracker_id = f"{tax_rate_id} ({tax_rate_name})"
            tracker.log_processing("QuickBooks", tracker_id)

            if not tax_rate_id:
                stats["skipped"] += 1
                tracker.log_skip("QuickBooks", tracker_id, "no_id")
                _dbg("sync_qb_tax_rates:skip_no_id", {"idx": idx})
                continue

            # Find existing tax account entry by tax_rate_id (check both sales and purchase)
            # IMPORTANT: Get ALL entries with this tax_rate_id, not just one
            # Multiple entries might exist (global + company-specific) with same tax_rate_id
            existing_taxes = frappe.db.sql("""
                SELECT name, quickbooks_tax_id, company, quickbooks_tax_rate_id, quickbooks_purchase_tax_rate_id
                FROM `tabQuickbooks Tax Account`
                WHERE parent = %s
                AND (quickbooks_tax_rate_id = %s OR quickbooks_purchase_tax_rate_id = %s)
            """, (quickbooks_settings.name, tax_rate_id, tax_rate_id), as_dict=True)
            
            # Use the first one found (prefer company-specific if company is provided)
            existing_tax = None
            if existing_taxes:
                if company:
                    # Prefer company-specific entry
                    for tax_entry in existing_taxes:
                        if tax_entry.get("company") == company:
                            existing_tax = tax_entry.get("name")
                            break
                    # If no company-specific found, use first available
                    if not existing_tax:
                        existing_tax = existing_taxes[0].get("name")
                else:
                    # Prefer global entry (company is None or empty)
                    for tax_entry in existing_taxes:
                        if not tax_entry.get("company"):
                            existing_tax = tax_entry.get("name")
                            break
                    # If no global found, use first available
                    if not existing_tax:
                        existing_tax = existing_taxes[0].get("name")
                
                # Log if multiple entries found with same tax_rate_id
                if len(existing_taxes) > 1:
                    _dbg("sync_qb_tax_rates:multiple_entries_found", {
                        "tax_rate_id": tax_rate_id,
                        "count": len(existing_taxes),
                        "selected": existing_tax,
                        "all_entries": [t.get("name") for t in existing_taxes]
                    })

            if existing_taxes:
                # Update ALL entries with this tax_rate_id (not just one)
                # This ensures all duplicates get updated consistently
                for tax_entry in existing_taxes:
                    entry_name = tax_entry.get("name")
                    entry_tax_id = tax_entry.get("quickbooks_tax_id")
                    entry_company = tax_entry.get("company")
                    
                    try:
                        existing_doc = frappe.get_doc("Quickbooks Tax Account", entry_name)
                        updated = False

                        # Determine if this tax rate is for sales or purchase
                        is_purchase_tax = (
                            hasattr(existing_doc, "quickbooks_purchase_tax_rate_id") and
                            existing_doc.quickbooks_purchase_tax_rate_id == tax_rate_id
                        )
                        is_sales_tax = (
                            hasattr(existing_doc, "quickbooks_tax_rate_id") and
                            existing_doc.quickbooks_tax_rate_id == tax_rate_id
                        )

                        # Update sales tax rate info
                        if is_sales_tax:
                            if hasattr(existing_doc, "quickbooks_tax_rate_name"):
                                if existing_doc.quickbooks_tax_rate_name != tax_rate_name:
                                    existing_doc.quickbooks_tax_rate_name = tax_rate_name
                                    updated = True

                        # Update purchase tax rate info
                        if is_purchase_tax:
                            if hasattr(existing_doc, "quickbooks_purchase_tax_rate_name"):
                                if existing_doc.quickbooks_purchase_tax_rate_name != tax_rate_name:
                                    existing_doc.quickbooks_purchase_tax_rate_name = tax_rate_name
                                    updated = True
                            # Also update primary fields if purchase tax is the only one
                            elif not is_sales_tax and hasattr(existing_doc, "quickbooks_tax_rate_name"):
                                if existing_doc.quickbooks_tax_rate_name != tax_rate_name:
                                    existing_doc.quickbooks_tax_rate_name = tax_rate_name
                                    updated = True

                        # Update rate value and description (shared fields)
                        if hasattr(existing_doc, "tax_rate"):
                            if existing_doc.tax_rate != rate_value:
                                existing_doc.tax_rate = rate_value
                                updated = True

                        if hasattr(existing_doc, "tax_rate_description"):
                            if existing_doc.tax_rate_description != description:
                                existing_doc.tax_rate_description = description
                                updated = True

                        if updated:
                            # Check for duplicates BEFORE saving to avoid validation errors
                            # Use SQL for proper NULL/empty company handling
                            if existing_doc.company:
                                duplicate_check = frappe.db.get_value(
                                    "Quickbooks Tax Account",
                                    {
                                        "parent": "Quickbooks Settings",
                                        "quickbooks_tax_id": existing_doc.quickbooks_tax_id,
                                        "company": existing_doc.company,
                                        "name": ["!=", existing_doc.name]
                                    },
                                    "name"
                                )
                            else:
                                # Check for global duplicates (company is NULL or empty)
                                duplicate_result = frappe.db.sql("""
                                    SELECT name
                                    FROM `tabQuickbooks Tax Account`
                                    WHERE parent = %s
                                    AND quickbooks_tax_id = %s
                                    AND (company IS NULL OR company = '')
                                    AND name != %s
                                    LIMIT 1
                                """, (quickbooks_settings.name, existing_doc.quickbooks_tax_id, existing_doc.name), as_dict=True)
                                duplicate_check = duplicate_result[0].name if duplicate_result else None
                            
                            if duplicate_check:
                                # Duplicate exists - skip update and log warning
                                _dbg("sync_qb_tax_rates:duplicate_skip", {
                                    "idx": idx,
                                    "tax_rate_id": tax_rate_id,
                                    "tax_id": existing_doc.quickbooks_tax_id,
                                    "company": existing_doc.company,
                                    "existing_doc": existing_doc.name,
                                    "duplicate_doc": duplicate_check,
                                    "note": "Skipping update due to duplicate tax_id+company mapping"
                                })
                                stats["errors"] += 1
                                # Don't try to save - continue with next entry
                                continue
                            
                            # Ensure unique_key is updated before validation
                            existing_doc.set_unique_key()
                            
                            # Save with error handling for other validation errors
                            try:
                                existing_doc.save(ignore_permissions=True)
                                frappe.db.commit()
                                stats["updated"] += 1
                                _dbg(
                                    "sync_qb_tax_rates:updated",
                                    {
                                        "idx": idx,
                                        "tax_rate_id": tax_rate_id,
                                        "name": tax_rate_name,
                                        "rate": rate_value,
                                        "entry": entry_name,
                                    },
                                )
                                tracker.log_success("QuickBooks", tracker_id, "UPDATED")
                            except Exception as ve:
                                # Catch any other validation or save errors
                                error_msg = str(ve)
                                error_type = type(ve).__name__
                                
                                _dbg("sync_qb_tax_rates:save_error", {
                                    "idx": idx,
                                    "tax_rate_id": tax_rate_id,
                                    "entry": entry_name,
                                    "error_type": error_type,
                                    "error": error_msg
                                })
                                stats["errors"] += 1
                                qb_log_exception(
                                    method="sync_qb_tax_rates",
                                    err=ve,
                                    request_data={
                                        "tax_rate_id": tax_rate_id,
                                        "tax_rate_name": tax_rate_name,
                                        "existing_doc": entry_name
                                    },
                                    module="sync_taxcode",
                                )
                                # Don't re-raise - continue with next entry
                                continue

                        # Auto-create ERPNext tax objects if enabled (only for first entry to avoid duplicates)
                        if auto_create and rate_value > 0 and tax_entry == existing_taxes[0]:
                            creation_stats = auto_create_tax_objects(
                                qb_tax_rate, company, tax_rate_id
                            )
                            stats["templates_created"] += creation_stats.get("templates", 0)
                            stats["withholding_created"] += creation_stats.get("withholding", 0)

                        if not updated:
                            stats["skipped"] += 1
                            _dbg(
                                "sync_qb_tax_rates:skip_no_changes",
                                {"idx": idx, "tax_rate_id": tax_rate_id, "entry": entry_name},
                            )
                            tracker.log_skip("QuickBooks", tracker_id, "no_changes")
                    except Exception as entry_error:
                        # Log error for this specific entry but continue with others
                        _dbg("sync_qb_tax_rates:entry_error", {
                            "idx": idx,
                            "tax_rate_id": tax_rate_id,
                            "entry": entry_name,
                            "error": str(entry_error)
                        })
                        stats["errors"] += 1
                        continue
            else:
                # TaxRate not linked to any TaxCode yet
                stats["not_found"] += 1
                _dbg(
                    "sync_qb_tax_rates:not_linked",
                    {
                        "idx": idx,
                        "tax_rate_id": tax_rate_id,
                        "name": tax_rate_name,
                        "rate": rate_value,
                    },
                )
                tracker.log_skip("QuickBooks", tracker_id, "not_linked_to_code")

        except Exception as e:
            stats["errors"] += 1
            tracker.log_error("QuickBooks", f"{qb_tax_rate.get('Id')} ({qb_tax_rate.get('Name')})", str(e))
            _dbg("sync_qb_tax_rates:error", {"idx": idx, "error": str(e)})
            qb_log_exception(
                method="sync_qb_tax_rates",
                err=e,
                request_data=qb_tax_rate,
                module="sync_taxcode",
            )

    _dbg("sync_qb_tax_rates:stats", stats)
    
    # Log final summary
    tracker.log_summary()
    
    return stats


# ============================================================================
# Enhanced Tax Sync: Auto-create ERPNext Native Tax Objects
# ============================================================================

def determine_tax_type(qb_tax_rate):
    """Determine what type of ERPNext tax object to create"""
    name = qb_tax_rate.get("Name", "").lower()
    special_type = cstr(qb_tax_rate.get("SpecialTaxType", "")).lower()

    # Withholding taxes
    withholding_keywords = ["tds", "tcs", "withholding", "deduct", "withheld"]
    if any(keyword in name for keyword in withholding_keywords):
        return "withholding"

    # Zero rate or exempt - skip auto-creation
    if special_type in ["zero_rate", "exempt"]:
        return "skip"

    # Sales taxes (default for most cases)
    sales_keywords = ["sales", "vat", "gst", "tax", "output", "input"]
    if any(keyword in name for keyword in sales_keywords):
        return "item_tax"

    # Default to item tax template
    return "item_tax"


def find_or_create_tax_account(tax_name, company, account_type="Tax"):
    """Find existing tax account or create new one"""

    try:
        company_abbr = get_abbr(company)

        # Try to find existing account
        account_name = f"{tax_name} - {company_abbr}"

        if frappe.db.exists("Account", account_name):
            _dbg("find_tax_account:found", {"account": account_name})
            return account_name

        # Account doesn't exist, create it
        _dbg("find_tax_account:creating", {"account": account_name})

        # Find parent account for taxes
        parent_account = frappe.db.get_value(
            "Account",
            {
                "company": company,
                "account_type": account_type,
                "is_group": 1,
            },
            "name",
        )

        if not parent_account:
            # Try to find "Duties and Taxes" or similar
            parent_account = frappe.db.get_value(
                "Account",
                {
                    "company": company,
                    "account_name": ["like", "%Tax%"],
                    "is_group": 1,
                },
                "name",
            )

        if not parent_account:
            _dbg("find_tax_account:no_parent", {"company": company})
            return None

        # Create the account
        account = frappe.get_doc({
            "doctype": "Account",
            "account_name": tax_name,
            "company": company,
            "parent_account": parent_account,
            "account_type": account_type,
            "is_group": 0,
        })
        account.insert(ignore_permissions=True)
        frappe.db.commit()

        _dbg("find_tax_account:created", {"account": account.name})
        return account.name

    except Exception as e:
        _dbg("find_tax_account:error", {"error": str(e), "tax_name": tax_name})
        return None


def create_item_tax_template(qb_tax_rate, company, tax_account):
    """Create Item Tax Template from QuickBooks TaxRate"""

    try:
        tax_rate_name = qb_tax_rate.get("Name", "")
        rate_value = flt(qb_tax_rate.get("RateValue", 0))
        company_abbr = get_abbr(company)

        template_name = f"{tax_rate_name} - {company_abbr}"

        # Check if template already exists
        if frappe.db.exists("Item Tax Template", template_name):
            _dbg("create_item_tax_template:exists", {"template": template_name})
            return template_name

        # Create new template with base fields
        template_data = {
            "doctype": "Item Tax Template",
            "title": tax_rate_name,
            "company": company,
            "taxes": [{
                "tax_type": tax_account,
                "tax_rate": rate_value,
            }]
        }

        # Check for mandatory fields (standard or custom) and set defaults
        try:
            meta = frappe.get_meta("Item Tax Template")

            # Check for Service Code field (could be standard or custom)
            service_code_fields = ["service_code", "custom_service_code", "Service Code", "custom_service_code"]
            for fieldname in service_code_fields:
                if meta.has_field(fieldname):
                    field = meta.get_field(fieldname)
                    if field and field.reqd:
                        # Try to get default from company or use empty string
                        default_service_code = (
                            frappe.db.get_value("Company", company, fieldname) or
                            frappe.db.get_value("Company", company, "custom_service_code") or
                            ""
                        )
                        template_data[fieldname] = default_service_code
                        break

            # Check for Cost Center field (could be standard or custom)
            cost_center_fields = ["cost_center", "custom_cost_center", "Cost Center", "custom_cost_center"]
            for fieldname in cost_center_fields:
                if meta.has_field(fieldname):
                    field = meta.get_field(fieldname)
                    if field and field.reqd:
                        # Try to get default cost center from company or find first cost center
                        default_cost_center = (
                            frappe.db.get_value("Company", company, fieldname) or
                            frappe.db.get_value("Company", company, "custom_cost_center") or
                            frappe.db.get_value(
                                "Cost Center",
                                {"company": company, "is_group": 0},
                                "name",
                                order_by="creation asc"
                            ) or
                            ""
                        )
                        template_data[fieldname] = default_cost_center
                        break
        except Exception as field_check_error:
            # If field check fails, log but continue
            _dbg("create_item_tax_template:field_check_error", {
                "error": str(field_check_error)
            })

        template = frappe.get_doc(template_data)
        template.insert(ignore_permissions=True)
        frappe.db.commit()

        _dbg("create_item_tax_template:created", {
            "template": template.name,
            "rate": rate_value
        })
        return template.name

    except Exception as e:
        _dbg("create_item_tax_template:error", {
            "error": str(e),
            "tax_name": qb_tax_rate.get("Name")
        })
        # Log the error but don't fail the sync
        frappe.log_error(
            title="Item Tax Template Creation Failed",
            message=f"Failed to create Item Tax Template for {tax_rate_name}: {str(e)}"
        )
        return None


def create_tax_withholding_category(qb_tax_rate, company, tax_account):
    """Create Tax Withholding Category from QuickBooks TaxRate"""

    try:
        category_name = qb_tax_rate.get("Name", "")
        rate_value = flt(qb_tax_rate.get("RateValue", 0))

        # Check if category already exists
        if frappe.db.exists("Tax Withholding Category", category_name):
            _dbg("create_withholding:exists", {"category": category_name})
            return category_name

        # Create new category
        category = frappe.get_doc({
            "doctype": "Tax Withholding Category",
            "name": category_name,
            "category_name": category_name,
            "accounts": [{
                "company": company,
                "account": tax_account,
            }],
            "rates": [{
                "from_date": nowdate(),
                "tax_withholding_rate": rate_value,
            }]
        })
        category.insert(ignore_permissions=True)
        frappe.db.commit()

        _dbg("create_withholding:created", {
            "category": category.name,
            "rate": rate_value
        })
        return category.name

    except Exception as e:
        _dbg("create_withholding:error", {
            "error": str(e),
            "tax_name": qb_tax_rate.get("Name")
        })
        return None


def auto_create_tax_objects(qb_tax_rate, company, tax_rate_id):
    """Auto-create ERPNext tax objects based on tax type"""

    stats = {"templates": 0, "withholding": 0}

    try:
        # Determine tax type
        tax_type = determine_tax_type(qb_tax_rate)

        if tax_type == "skip":
            _dbg("auto_create:skip", {"tax_id": tax_rate_id})
            return stats

        # Find or create tax account
        tax_name = qb_tax_rate.get("Name", "")
        tax_account = find_or_create_tax_account(tax_name, company)

        if not tax_account:
            _dbg("auto_create:no_account", {"tax_id": tax_rate_id})
            return stats

        # Create appropriate ERPNext object
        if tax_type == "item_tax":
            result = create_item_tax_template(qb_tax_rate, company, tax_account)
            if result:
                stats["templates"] = 1
                _dbg("auto_create:item_tax_created", {
                    "tax_id": tax_rate_id,
                    "template": result
                })

        elif tax_type == "withholding":
            result = create_tax_withholding_category(qb_tax_rate, company, tax_account)
            if result:
                stats["withholding"] = 1
                _dbg("auto_create:withholding_created", {
                    "tax_id": tax_rate_id,
                    "category": result
                })

    except Exception as e:
        _dbg("auto_create:error", {"error": str(e), "tax_id": tax_rate_id})

    return stats
