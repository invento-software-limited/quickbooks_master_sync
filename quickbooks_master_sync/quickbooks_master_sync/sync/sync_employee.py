from __future__ import unicode_literals

from time import strftime

import frappe
from frappe import _

from quickbooks_master_sync.pyqb.quickbooks.batch import batch_create
from quickbooks_master_sync.pyqb.quickbooks.objects.employee import Employee

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_exception
from .sync_utils import _dbg as _dbg_common
from .sync_utils import query_with_pagination, save_qb_data_to_json


def _dbg(event, payload=None):
    """Debug logging helper for employee sync"""
    _dbg_common("sync_employee", event, payload)


def create_Employee(quickbooks_obj):
    """Fetch Employee data from QuickBooks and store in ERPNEXT"""

    # Get company for unique QB ID generation
    company = (
        frappe.defaults.get_user_default("company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {}, "name")
    )

    employee = None
    quickbooks_employee_list = []
    employee_query = """SELECT Id, DisplayName, PrimaryPhone, Mobile, Gender, PrimaryEmailAddr, BirthDate, HiredDate, ReleasedDate FROM Employee"""
    # Use pagination helper to fetch all employees
    get_qb_employee = query_with_pagination(
        quickbooks_obj,
        employee_query,
        "Employee",
        module_name="sync_employee",
        data_name="employees_list"
    )

    # Save raw QuickBooks employee data to JSON file for debugging
    try:
        save_qb_data_to_json(
            data=get_qb_employee,
            data_name="employees_list",
            module_name="sync_employee",
            full_response={"QueryResponse": {"Employee": get_qb_employee}},
        )
    except Exception as e:
        _dbg("create_Employee:debug_save_error", {"error": str(e)})
        # Don't fail the sync if debug file save fails

    stats = {"created": 0, "skipped": 0, "errors": 0}
    
    # Initialize generic Import Tracker
    from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
    tracker = ImportTracker(len(get_qb_employee), "qb_employee_import.log", company=company, module_name="EMPLOYEE")

    try:
        for fields in get_qb_employee:
            qb_employee_id = str(fields.get("Id", ""))
            
            # Generate unique ID immediately
            from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id
            unique_emp_id = make_unique_qb_id(qb_employee_id, company)

            # Log start of processing
            display_name = fields.get("DisplayName", "Unknown")
            tracker_id = f"{unique_emp_id} ({display_name})"
            tracker.log_processing("QuickBooks", tracker_id)

            if not qb_employee_id:
                stats["skipped"] += 1
                tracker.log_skip("QuickBooks", tracker_id, "no_id")
                _dbg("create_Employee:skip_no_id", {
                    "employee": display_name
                })
                continue

            # Check if employee already exists
            # Check if employee already exists
            # unique_emp_id already created

            existing_employee = frappe.db.get_value(
                "Employee", {"quickbooks_emp_id": unique_emp_id, "company": company}, "name"
            )
            if existing_employee:
                stats["skipped"] += 1
                tracker.log_skip("QuickBooks", tracker_id, "already_exists")
                _dbg("create_Employee:already_exists", {
                    "qb_employee_id": qb_employee_id,
                    "employee_name": fields.get("DisplayName"),
                    "existing_employee": existing_employee
                })
                quickbooks_employee_list.append(qb_employee_id)
                continue

            # Create new employee
            employee = frappe.new_doc("Employee")
            display_name = fields.get("DisplayName") or "Unnamed Employee"
            employee.employee_name = display_name
            from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id
            employee.quickbooks_emp_id = make_unique_qb_id(qb_employee_id, company)

            # Parse DisplayName to extract first_name and last_name
            # ERPNext requires first_name as mandatory field
            name_parts = display_name.strip().split()
            if len(name_parts) >= 1:
                employee.first_name = name_parts[0]
                if len(name_parts) > 1:
                    employee.last_name = " ".join(name_parts[1:])
                else:
                    employee.last_name = ""  # Optional field
            else:
                employee.first_name = display_name
                employee.last_name = ""
            employee.date_of_joining = (
                fields.get("HiredDate")
                if fields.get("HiredDate")
                else strftime("%Y-%m-%d")
            )
            employee.date_of_birth = (
                fields.get("BirthDate") if fields.get("BirthDate") else "1916-04-01"
            )
            employee.gender = (
                fields.get("Gender") if fields.get("Gender") else "Male"
            )

            # Handle Mobile phone - can be Mobile or PrimaryPhone
            cell_number = ""
            if fields.get("Mobile"):
                cell_number = fields["Mobile"].get("FreeFormNumber", "")
            elif fields.get("PrimaryPhone"):
                cell_number = fields["PrimaryPhone"].get("FreeFormNumber", "")
            employee.cell_number = cell_number

            employee.personal_email = (
                fields["PrimaryEmailAddr"].get("Address", "")
                if fields.get("PrimaryEmailAddr")
                else ""
            )

            # Set company field
            if company:
                employee.company = company

            employee.insert()
            quickbooks_employee_list.append(qb_employee_id)

            _dbg("create_Employee:created", {
                "qb_employee_id": qb_employee_id,
                "employee_name": employee.employee_name,
                "erpnext_employee": employee.name
            })
            stats["created"] += 1
            tracker.log_success("QuickBooks", tracker_id, "CREATED")
    except Exception as e:
        if e.args and str(e.args[0]).startswith("402"):
            raise e
        else:
            _dbg("create_Employee:error", {"error": str(e)})
            qb_log_exception(
                method="create_Employee",
                err=e,
                request_data=locals().get("get_qb_employee"),
                module="sync_employee",
            )
            tracker.log_error("QuickBooks", tracker_id if 'tracker_id' in locals() else "Unknown", str(e))
            stats["errors"] += 1
    
    # Log final summary
    tracker.log_summary()
    
    _dbg("create_Employee:stats", stats)
    return quickbooks_employee_list


""" Sync Employee data from ERPNext to Quickbooks """


def sync_erp_employees():
    """Receive Response From Quickbooks and Update quickbooks_emp_id in Employee"""
    response_from_quickbooks = sync_erp_employees_to_quickbooks()
    if response_from_quickbooks:
        try:
            from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id
            for response_obj in response_from_quickbooks.successes:
                if response_obj:
                    # Get company from employee to create unique QB ID
                    employee_company = frappe.db.get_value("Employee", {"employee_name": response_obj.DisplayName}, "company")
                    if employee_company:
                        unique_qb_id = make_unique_qb_id(str(response_obj.Id), employee_company)
                        # Fix SQL injection: use parameterized query
                        frappe.db.sql(
                            """
                            UPDATE tabEmployee
                            SET quickbooks_emp_id = %s
                            WHERE employee_name = %s
                            """,
                            (unique_qb_id, response_obj.DisplayName),
                        )
                    else:
                        # Fallback if company not found
                        frappe.db.sql(
                            """
                            UPDATE tabEmployee
                            SET quickbooks_emp_id = %s
                            WHERE employee_name = %s
                            """,
                            (str(response_obj.Id), response_obj.DisplayName),
                        )
                    frappe.db.commit()
                else:
                    raise _("Does not get any response from quickbooks")
        except Exception as e:
            _dbg("sync_erp_employees:error", {"error": str(e)})
            qb_log_exception(
                method="sync_erp_employees",
                err=e,
                request_data=locals().get("response_obj"),
                module="sync_employee",
            )


def sync_erp_employees_to_quickbooks():
    Employee_list = []
    for erp_employee in erp_employee_data():
        try:
            if erp_employee:
                create_erp_employee_to_quickbooks(erp_employee, Employee_list)
            else:
                raise _("Employee does not exist in ERPNext")
        except Exception as e:
            if e.args and str(e.args[0]).startswith("402"):
                raise e
            else:
                _dbg("sync_erp_employees_to_quickbooks:error", {"error": str(e)})
                qb_log_exception(
                    method="sync_erp_employees_to_quickbooks",
                    err=e,
                    request_data=erp_employee,
                    module="sync_employee",
                )
    results = batch_create(Employee_list)
    return results


def erp_employee_data():
    erp_employee = frappe.db.sql(
        """select employee_name, gender from `tabEmployee` where `quickbooks_emp_id` is NULL && employee_name is not null""",
        as_dict=1,
    )
    return erp_employee


def create_erp_employee_to_quickbooks(erp_employee, Employee_list):
    employee_obj = Employee()
    employee_obj.DisplayName = erp_employee.employee_name
    employee_obj.GivenName = erp_employee.employee_name
    employee_obj.FamilyName = erp_employee.employee_name
    employee_obj.Gender = erp_employee.gender
    employee_obj.save()
    Employee_list.append(employee_obj)
    return Employee_list
