frappe.listview_settings['Quickbooks Log'] = {
	add_fields: ["status", "company"],
	get_indicator: function(doc) {
		if(doc.status==="Success"){
			return [__("Success"), "green", "status,=,Success"];
        } else if(doc.status ==="Error"){
			return [__("Error"), "red", "status,=,Error"];
        } else if(doc.status ==="Queued"){
			return [__("Queued"), "orange", "status,=,Queued"];
        }
	},
	filters: [
		["company", "=", frappe.defaults.get_user_default("company") || ""]
	]
}
