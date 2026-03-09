// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["QuickBooks Balance Overview"] = {
    "filters": [
        {
            "fieldname": "as_of_date",
            "label": __("As of Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "tolerance",
            "label": __("Tolerance"),
            "fieldtype": "Float",
            "default": 0.01
        }
    ],
    "formatter": function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldname == "total_difference" && data) {
            // Extract numeric value from formatted string if possible, or use data.total_difference if available (it might be formatted string)
            // Actually report data usually has raw value? No, I returned formatted string.
            // So parsing is hard.
            // But I can check if it contains ANY non-zero digit?
            // Better: The server returned formatted string.
            // Let's assume if it's not "0.00" or equivalent, it's a difference.
            // Or check tolerance.

            // Simple check: if value contains digits other than 0 and punctuation?
            // Let's just color it red if it looks like a non-zero amount.
            // Actually in the python script I could return a dict {value: ..., formatted: ...}?
            // Or just checking if it is not 0.

            // For now, let's leave default formatting.
        }

        return value;
    }
};
