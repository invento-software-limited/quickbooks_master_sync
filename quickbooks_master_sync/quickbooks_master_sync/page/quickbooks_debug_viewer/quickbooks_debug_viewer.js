frappe.pages['quickbooks-debug-viewer'].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'QuickBooks Debug Viewer',
        single_column: true
    });

    page.main.addClass('frappe-card');
    $(wrapper).addClass('quickbooks-debug-viewer');

    // Store wrapper reference for later use
    page.wrapper = wrapper;

    // Initialize filters object
    page.filters = {
        filename_filter: '',
        data_type: '',
        company_filter: '',
        sort_by: 'modified',
        sort_order: 'desc',
        list_full_response_only: 0
    };

    // Add filter controls
    add_filter_controls(page, wrapper);

    // Add inner buttons
    page.add_inner_button(__('View Import Logs'), function () {
        show_import_log_viewer(page);
    });



    // Add action buttons
    page.add_inner_button(__('Refresh'), function () {
        load_debug_files(page);
    });

    page.add_inner_button(__('Clear All Files'), function () {
        frappe.confirm(
            __('Are you sure you want to delete all QuickBooks debug files?'),
            function () {
                clear_all_files(page);
            }
        );
    }, __('Actions'));


    // Load files on page load
    load_debug_files(page);
}

function add_filter_controls(page, wrapper) {
    // Create filter bar HTML
    const filter_html = `
		<div class="filter-bar" style="padding: 15px; background: #f8f9fa; border-bottom: 1px solid #d1d8dd; margin-bottom: 15px;">
			<div class="row">
				<div class="col-sm-6">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">${__('Search Filename')}</label>
						<input type="text" class="form-control input-sm" id="filename-filter"
							placeholder="${__('Filter by filename...')}" style="height: 30px;">
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">${__('Data Type')}</label>
						<select class="form-control input-sm" id="data-type-filter" style="height: 30px;">
							<option value="">All Types</option>
						</select>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">${__('Company')}</label>
						<select class="form-control input-sm" id="company-filter" style="height: 30px;">
							<option value="">All Companies</option>
						</select>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">${__('Sort By')}</label>
						<select class="form-control input-sm" id="sort-by-filter" style="height: 30px;">
							<option value="modified">Modified Date</option>
							<option value="size">File Size</option>
							<option value="filename">File Name</option>
							<option value="data_name">Data Type</option>
						</select>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">${__('Sort Order')}</label>
						<select class="form-control input-sm" id="sort-order-filter" style="height: 30px;">
							<option value="desc">Descending (↓)</option>
							<option value="asc">Ascending (↑)</option>
						</select>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">&nbsp;</label>
						<div class="checkbox" style="margin-top: 5px;">
							<label>
								<input type="checkbox" id="reconcile-list-filter"> ${__('Reconcile List Only')}
							</label>
						</div>
					</div>
				</div>
				<div class="col-sm-3">
					<div class="form-group">
						<label class="control-label" style="font-size: 12px;">&nbsp;</label>
						<div>
							<button class="btn btn-default btn-sm" id="clear-filters-btn" style="height: 30px; margin-right: 5px;">
								<i class="fa fa-times"></i> ${__('Clear Filters')}
							</button>
							<button class="btn btn-primary btn-sm" id="apply-filters-btn" style="height: 30px;">
								<i class="fa fa-filter"></i> ${__('Apply')}
							</button>
						</div>
					</div>
				</div>
			</div>
		</div>
	`;

    // Insert filter bar before main content
    $(wrapper).find('.page-content').prepend(filter_html);

    // Store references to filter inputs
    page.filename_input = $(wrapper).find('#filename-filter');
    page.data_type_select = $(wrapper).find('#data-type-filter');
    page.company_select = $(wrapper).find('#company-filter');
    page.sort_by_select = $(wrapper).find('#sort-by-filter');
    page.sort_order_select = $(wrapper).find('#sort-order-filter');
    page.reconcile_list_checkbox = $(wrapper).find('#reconcile-list-filter');

    // Attach event handlers with auto-apply on change
    let timeout;
    page.filename_input.on('input', function () {
        clearTimeout(timeout);
        timeout = setTimeout(function () {
            page.filters.filename_filter = page.filename_input.val() || '';
            load_debug_files(page);
        }, 500); // Debounce for 500ms
    });

    page.data_type_select.on('change', function () {
        const value = $(this).val();
        page.filters.data_type = value || '';
        load_debug_files(page);
    });

    page.company_select.on('change', function () {
        const value = $(this).val();
        page.filters.company_filter = value || '';
        load_debug_files(page);
    });

    page.sort_by_select.on('change', function () {
        page.filters.sort_by = $(this).val() || 'modified';
        load_debug_files(page);
    });

    page.sort_order_select.on('change', function () {
        page.filters.sort_order = $(this).val() || 'desc';
        load_debug_files(page);
    });

    page.reconcile_list_checkbox.on('change', function () {
        page.filters.list_full_response_only = $(this).is(':checked') ? 1 : 0;
        load_debug_files(page);
    });

    // Clear filters button
    $(wrapper).find('#clear-filters-btn').on('click', function () {
        page.filename_input.val('');
        page.data_type_select.val('');
        page.company_select.val('');
        page.sort_by_select.val('modified');
        page.sort_order_select.val('desc');
        page.reconcile_list_checkbox.prop('checked', false);

        page.filters.filename_filter = '';
        page.filters.data_type = '';
        page.filters.company_filter = '';
        page.filters.sort_by = 'modified';
        page.filters.sort_order = 'desc';
        page.filters.list_full_response_only = 0;

        load_debug_files(page);
    });

    // Apply filters button (manual trigger)
    $(wrapper).find('#apply-filters-btn').on('click', function () {
        page.filters.filename_filter = page.filename_input.val() || '';
        page.filters.data_type = page.data_type_select.val() || '';
        page.filters.company_filter = page.company_select.val() || '';
        page.filters.sort_by = page.sort_by_select.val() || 'modified';
        page.filters.sort_order = page.sort_order_select.val() || 'desc';
        page.filters.list_full_response_only = page.reconcile_list_checkbox.is(':checked') ? 1 : 0;

        load_debug_files(page);
    });
}

function load_debug_files(page) {
    page.main.html('<div class="text-center" style="padding: 40px;"><i class="fa fa-spinner fa-spin fa-2x"></i><br><br>Loading debug files...</div>');

    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_files',
        args: {
            filename_filter: page.filters.filename_filter || null,
            data_type: page.filters.data_type || null,
            company_filter: page.filters.company_filter || null,
            sort_by: page.filters.sort_by || 'modified',
            sort_order: page.filters.sort_order || 'desc',
            list_full_response_only: page.filters.list_full_response_only || 0
        },
        callback: function (r) {
            if (r.message && r.message.success) {
                // Update data type dropdown options
                update_data_type_options(page, r.message.available_data_types);
                // Update company dropdown options
                update_company_options(page, r.message.available_companies);
                render_files_list(page, r.message);
            } else {
                page.main.html('<div class="alert alert-danger">Error: ' + (r.message.error || 'Unknown error') + '</div>');
            }
        },
        error: function (err) {
            page.main.html('<div class="alert alert-danger">Error loading files: ' + err.message + '</div>');
        }
    });
}

function update_company_options(page, available_companies) {
    if (!page.company_select || !available_companies) return;

    // Get current value before updating
    const current_value = page.company_select.val();

    // Clear existing options except first one
    page.company_select.empty();
    page.company_select.append($('<option>', {
        value: '',
        text: 'All Companies'
    }));

    // Add all available companies
    available_companies.forEach(function (company) {
        page.company_select.append($('<option>', {
            value: company,
            text: company
        }));
    });

    // Restore selected value if it still exists
    if (current_value && available_companies.includes(current_value)) {
        page.company_select.val(current_value);
    }
}

function update_data_type_options(page, available_types) {
    if (!page.data_type_select || !available_types) return;

    // Get current value before updating
    const current_value = page.data_type_select.val();

    // Clear existing options except first one
    page.data_type_select.empty();
    page.data_type_select.append($('<option>', {
        value: '',
        text: 'All Types'
    }));

    // Add all available types
    available_types.forEach(function (type) {
        // Format the type name for better display
        let display_name = type.replace(/_/g, ' ')
            .split(' ')
            .map(word => word.charAt(0).toUpperCase() + word.slice(1))
            .join(' ');

        page.data_type_select.append($('<option>', {
            value: type,
            text: display_name
        }));
    });

    // Restore selected value if it still exists
    if (current_value && available_types.includes(current_value)) {
        page.data_type_select.val(current_value);
    }
}

function render_files_list(page, data) {
    // Build filter status message
    let filter_status = '';
    if (data.filters_applied) {
        let active_filters = [];
        if (data.filters_applied.filename_filter) {
            active_filters.push(`Filename: "${data.filters_applied.filename_filter}"`);
        }
        if (data.filters_applied.data_type) {
            active_filters.push(`Type: "${data.filters_applied.data_type}"`);
        }
        if (data.filters_applied.company_filter) {
            active_filters.push(`Company: "${data.filters_applied.company_filter}"`);
        }
        if (active_filters.length > 0) {
            filter_status = `<small class="text-muted"><i class="fa fa-filter"></i> Filters: ${active_filters.join(', ')}</small>`;
        }
    }

    let sort_icon = data.filters_applied && data.filters_applied.sort_order === 'asc' ?
        '<i class="fa fa-sort-amount-asc"></i>' : '<i class="fa fa-sort-amount-desc"></i>';

    let html = `
		<div style="padding: 20px;">
			<div class="row">
				<div class="col-md-12">
					<h4>QuickBooks Debug Files</h4>
					<p class="text-muted">
						${sort_icon} Total Files: <strong>${data.count}</strong> |
						Total Size: <strong>${data.total_size_mb} MB</strong>
						${filter_status ? '<br>' + filter_status : ''}
					</p>
				</div>
			</div>
	`;

    if (data.files.length === 0) {
        let no_results_msg = data.filters_applied && (data.filters_applied.filename_filter || data.filters_applied.data_type || data.filters_applied.company_filter) ?
            'No files match your filters. Try adjusting the search criteria.' :
            'No debug files found. Debug files are created when you sync data from QuickBooks.';

        html += `
			<div class="alert alert-info">
				<i class="fa fa-info-circle"></i> ${no_results_msg}
			</div>
		`;
    } else {
        html += `
			<div class="row">
				<div class="col-md-12">
					<table class="table table-bordered table-hover">
						<thead>
							<tr>
								<th style="width: 35%;">File Name</th>
								<th style="width: 15%;">Data Type</th>
								<th style="width: 10%;">Company</th>
								<th style="width: 12%;">Size</th>
								<th style="width: 13%;">Modified</th>
								<th style="width: 15%;">Actions</th>
							</tr>
						</thead>
						<tbody>
		`;

        data.files.forEach(function (file) {
            html += `
				<tr>
					<td>
						<code style="font-size: 11px; word-break: break-all;">${file.filename}</code>
					</td>
					<td>
						<span class="badge badge-info">${file.data_name}</span>
					</td>
					<td>
						${file.company ? '<span class="badge badge-primary">' + file.company + '</span>' : '<span class="text-muted">-</span>'}
					</td>
					<td>${file.size_mb} MB</td>
					<td><small>${file.modified}</small></td>
					<td>
						<button class="btn btn-xs btn-primary view-file" data-filename="${file.filename}">
							<i class="fa fa-eye"></i> View
						</button>
						${file.filename.endsWith('list_full_response.json') ? `
						<button class="btn btn-xs btn-info reconcile-file" data-filename="${file.filename}" title="Reconcile with ERPNext">
							<i class="fa fa-balance-scale"></i>
						</button>` : ''}
						<button class="btn btn-xs btn-danger delete-file" data-filename="${file.filename}">
							<i class="fa fa-trash"></i>
						</button>
					</td>
				</tr>
			`;
        });

        html += `
						</tbody>
					</table>
				</div>
			</div>
		`;
    }

    html += '</div>';

    page.main.html(html);

    // Add event listeners
    page.main.find('.view-file').on('click', function () {
        let filename = $(this).data('filename');
        view_file_content(filename);
    });

    page.main.find('.delete-file').on('click', function () {
        let filename = $(this).data('filename');
        delete_file(filename, page);
    });

    page.main.find('.reconcile-file').on('click', function () {
        let filename = $(this).data('filename');
        reconcile_file(filename);
    });
}

function view_file_content(filename) {
    frappe.show_alert({ message: __('Loading file...'), indicator: 'blue' }, 2);

    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_file_content',
        args: {
            filename: filename
        },
        callback: function (r) {
            if (r.message && r.message.success) {
                show_file_dialog(r.message);
            } else {
                frappe.msgprint({
                    title: __('Error'),
                    message: r.message.error || 'Failed to load file',
                    indicator: 'red'
                });
            }
        }
    });
}

function show_file_dialog(data) {
    let dialog = new frappe.ui.Dialog({
        title: __('View Debug File: {0}', [data.filename]),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'file_info',
                options: `
					<div class="alert alert-info" style="margin-bottom: 15px;">
						<strong>File:</strong> ${data.filename}<br>
						<strong>Size:</strong> ${data.size_mb} MB<br>
						<strong>Records:</strong> ${data.record_count}
					</div>
				`
            },
            {
                fieldtype: 'Code',
                fieldname: 'content',
                label: 'JSON Content',
                options: 'JSON',
                default: JSON.stringify(data.content, null, 2)
            }
        ],
        primary_action_label: __('Download'),
        primary_action: function () {
            download_json(data.filename, data.content);
        }
    });

    dialog.show();

    // Make the code field read-only and scrollable
    setTimeout(function () {
        let editor = dialog.fields_dict.content.editor;
        if (editor) {
            editor.setOption('readOnly', true);
            editor.setOption('lineNumbers', true);
            editor.setSize(null, 500);
        }
    }, 100);
}

function download_json(filename, content) {
    let dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(content, null, 2));
    let downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", filename);
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();

    frappe.show_alert({ message: __('File downloaded'), indicator: 'green' }, 3);
}

function highlight_json(json_str) {
    // Simple JSON syntax highlighting
    // Process in specific order to avoid pattern conflicts
    return json_str
        // Highlight keys first (strings followed by colon and whitespace)
        .replace(/("(?:[^"\\]|\\.)*")\s*:/g, '<span class="json-key">$1</span>:')
        // Highlight string values (strings after colons, not keys)
        .replace(/:\s*("(?:[^"\\]|\\.)*")/g, ': <span class="json-string">$1</span>')
        // Highlight numbers (after colons, integers and decimals)
        .replace(/:\s*(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\b/g, ': <span class="json-number">$1</span>')
        // Highlight boolean values
        .replace(/\b(true|false)\b/g, '<span class="json-boolean">$1</span>')
        // Highlight null values
        .replace(/\bnull\b/g, '<span class="json-null">null</span>');
}

function delete_file(filename, page) {
    frappe.confirm(
        __('Are you sure you want to delete {0}?', [filename]),
        function () {
            frappe.call({
                method: 'quickbooks_master_sync.quickbooks_master_sync.api.delete_quickbooks_debug_file',
                args: {
                    filename: filename
                },
                callback: function (r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({ message: __('File deleted'), indicator: 'green' }, 3);
                        load_debug_files(page);
                    } else {
                        frappe.msgprint({
                            title: __('Error'),
                            message: r.message.error || 'Failed to delete file',
                            indicator: 'red'
                        });
                    }
                }
            });
        }
    );
}

function clear_all_files(page) {
    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.clear_all_quickbooks_debug_files',
        callback: function (r) {
            if (r.message && r.message.success) {
                frappe.show_alert({
                    message: __('{0} files deleted', [r.message.deleted_count]),
                    indicator: 'green'
                }, 3);
                load_debug_files(page);
            } else {
                frappe.msgprint({
                    title: __('Error'),
                    message: r.message.error || 'Failed to clear files',
                    indicator: 'red'
                });
            }
        }
    });
}




// ============================================================================
// Reconciliation Tool
// ============================================================================

function reconcile_file(filename) {
    frappe.show_alert({ message: __('Reconciling {0}...', [filename]), indicator: 'blue' }, 2);

    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.reconcile_debug_file_content',
        args: {
            filename: filename
        },
        callback: function (r) {
            if (r.message && r.message.success) {
                show_reconciliation_results(filename, r.message.results);
            } else {
                frappe.msgprint({
                    title: __('Reconciliation Failed'),
                    message: r.message.error || 'Unknown error during reconciliation',
                    indicator: 'red'
                });
            }
        }
    });
}


function get_link(doctype, name) {
    if (!name) return "";
    if (!doctype) return name;

    let route = frappe.router.slug(doctype);
    return `<a href="/app/${route}/${name}" target="_blank">${name}</a>`;
}

function show_reconciliation_results(filename, results) {
    let html = `
		<div style="padding: 10px;">
			<div class="row">
				<div class="col-xs-12">
					<div class="alert alert-info">
						<strong>File:</strong> ${filename}<br>
						<strong>Entity Type:</strong> ${results.entity_type}<br>
						<strong>Total Records:</strong> ${results.total_records}
					</div>
				</div>
			</div>
			
			<div class="row">
				<div class="col-xs-4 text-center">
					<h3 class="text-success">${results.matched.length}</h3>
					<div class="text-muted small">Matched</div>
				</div>
				<div class="col-xs-4 text-center">
					<h3 class="text-danger">${results.missing_in_erpnext.length}</h3>
					<div class="text-muted small">Missing in ERPNext</div>
				</div>
				<div class="col-xs-4 text-center">
					<h3 class="text-warning">${results.amount_mismatches.length}</h3>
					<div class="text-muted small">Amount Mismatches</div>
				</div>
			</div>
			
			<hr>
	`;

    // Missing Section
    if (results.missing_in_erpnext.length > 0) {
        html += `<h5><span class="text-danger">Missing in ERPNext</span> (${results.missing_in_erpnext.length})</h5>`;
        html += `<div style="max-height: 200px; overflow-y: auto; margin-bottom: 20px;">
			<table class="table table-bordered table-striped table-condensed">
				<thead>
					<tr>
						<th>QB ID</th>
						<th>Doc Num</th>
						<th>Details</th>
					</tr>
				</thead>
				<tbody>`;
        results.missing_in_erpnext.forEach(row => {
            let val = row.total_amt !== undefined ? row.total_amt : row.name;
            let date = row.txn_date ? `<br><small class="text-muted">${row.txn_date}</small>` : '';
            html += `<tr>
				<td><small>${row.qb_id}</small></td>
				<td><small>${row.doc_num || '-'}</small></td>
				<td>${val}${date}</td>
			</tr>`;
        });
        html += `</tbody></table></div>`;
    }

    // Mismatches Section
    if (results.amount_mismatches.length > 0) {
        html += `<h5><span class="text-warning">Amount Mismatches</span> (${results.amount_mismatches.length})</h5>`;
        html += `<div style="max-height: 200px; overflow-y: auto;">
			<table class="table table-bordered table-striped table-condensed">
				<thead>
					<tr>
						<th>Ref</th>
						<th>ERPNext</th>
						<th>QB Amount</th>
						<th>ERP Amount</th>
						<th>Diff</th>
					</tr>
				</thead>
				<tbody>`;
        results.amount_mismatches.forEach(row => {

            if (row.doc_num == undefined) {
                row.doc_num = "";
            }
            html += `<tr>
				<td><small>${row.doc_num} (${row.qb_id})</small></td>
				<td>${get_link(row.doctype, row.erp_name)}</td>
				<td>${row.qb_amount.toFixed(2)}</td>
				<td>${row.erp_amount.toFixed(2)}</td>
				<td class="text-danger"><strong>${row.difference.toFixed(2)}</strong></td>
			</tr>`;
        });
        html += `</tbody></table></div>`;
    }

    if (results.missing_in_erpnext.length === 0 && results.amount_mismatches.length === 0) {
        html += `<div class="alert alert-success text-center">
			<i class="fa fa-check-circle fa-2x"></i><br>
			All records matched successfully!
		</div>`;
    }

    html += `</div>`;

    let dialog = new frappe.ui.Dialog({
        title: __('Reconciliation Result'),
        size: 'large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'report',
                options: html
            }
        ],
        primary_action_label: __('Close'),
        primary_action: function () {
            dialog.hide();
        }
    });

    dialog.show();
}

// ============================================================================
// ImportTracker Log Viewer
// ============================================================================

function show_import_log_viewer(page) {
    // Get list of log files
    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_files',
        args: {
            filename_filter: '.log',
            sort_by: 'modified',
            sort_order: 'desc'
        },
        callback: function (r) {
            if (r.message && r.message.success) {
                let log_files = r.message.files.filter(f => f.filename.endsWith('.log'));

                if (log_files.length === 0) {
                    frappe.msgprint({
                        title: __('No Log Files'),
                        message: __('No import log files found. Log files are created when you sync data from QuickBooks.'),
                        indicator: 'blue'
                    });
                    return;
                }

                show_log_file_selector(log_files);
            } else {
                frappe.msgprint({
                    title: __('Error'),
                    message: r.message.error || 'Failed to load log files',
                    indicator: 'red'
                });
            }
        }
    });
}

function show_log_file_selector(log_files) {
    let dialog = new frappe.ui.Dialog({
        title: __('📊 Import Log Viewer'),
        size: 'small',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'info',
                options: `
					<div class="alert alert-info" style="margin-bottom: 15px;">
						<i class="fa fa-info-circle"></i> Select a log file to view import tracking details
					</div>
				`
            },
            {
                fieldtype: 'Select',
                fieldname: 'log_file',
                label: __('Log File'),
                options: log_files.map(f => f.filename),
                reqd: 1,
                description: __('Select the import log file to view')
            }
        ],
        primary_action_label: __('View Log'),
        primary_action: function () {
            let values = dialog.get_values();
            if (values.log_file) {
                dialog.hide();
                load_import_log(values.log_file);
            }
        }
    });

    dialog.show();
}

function load_import_log(filename) {
    frappe.show_alert({ message: __('Loading log file...'), indicator: 'blue' }, 2);

    frappe.call({
        method: 'quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_file_content',
        args: {
            filename: filename
        },
        callback: function (r) {
            if (r.message && r.message.success) {
                show_import_log_dialog(filename, r.message.content);
            } else {
                frappe.msgprint({
                    title: __('Error'),
                    message: r.message.error || 'Failed to load log file',
                    indicator: 'red'
                });
            }
        }
    });
}

function show_import_log_dialog(filename, log_entries) {
    // Parse and analyze log entries
    let analysis = analyze_log_entries(log_entries);

    // Create dialog with filters
    let dialog = new frappe.ui.Dialog({
        title: __('Import Log: {0}', [filename]),
        size: 'extra-large',
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'summary',
                options: render_log_summary(analysis)
            },
            {
                fieldtype: 'Section Break'
            },
            {
                fieldtype: 'Column Break',
                fieldname: 'col1'
            },
            {
                fieldtype: 'Select',
                fieldname: 'run_filter',
                label: __('Filter by Run'),
                options: ['All Runs'].concat(analysis.run_ids),
                default: 'All Runs',
                onchange: function () {
                    update_log_display(dialog, log_entries, analysis);
                }
            },
            {
                fieldtype: 'Column Break',
                fieldname: 'col2'
            },
            {
                fieldtype: 'Select',
                fieldname: 'level_filter',
                label: __('Filter by Level'),
                options: ['All Levels', 'INFO', 'OK', 'WARN', 'ERROR'],
                default: 'All Levels',
                onchange: function () {
                    update_log_display(dialog, log_entries, analysis);
                }
            },
            {
                fieldtype: 'Column Break',
                fieldname: 'col3'
            },
            {
                fieldtype: 'Select',
                fieldname: 'event_filter',
                label: __('Filter by Event'),
                options: ['All Events'].concat(analysis.event_types),
                default: 'All Events',
                onchange: function () {
                    update_log_display(dialog, log_entries, analysis);
                }
            },
            {
                fieldtype: 'Section Break'
            },
            {
                fieldtype: 'HTML',
                fieldname: 'log_content',
                options: render_log_table(log_entries, {})
            }
        ],
        primary_action_label: __('Export CSV'),
        primary_action: function () {
            export_log_to_csv(filename, log_entries);
        },
        secondary_action_label: __('Refresh'),
        secondary_action: function () {
            // Show loading indicator
            frappe.show_alert({ message: __('Refreshing log...'), indicator: 'blue' }, 2);

            // Reload the log file content
            frappe.call({
                method: 'quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_file_content',
                args: {
                    filename: filename
                },
                callback: function (r) {
                    if (r.message && r.message.success) {
                        // Update log entries with fresh data
                        log_entries = r.message.content;
                        analysis = analyze_log_entries(log_entries);

                        // Update summary
                        dialog.fields_dict.summary.$wrapper.html(render_log_summary(analysis));

                        // Update filter options
                        dialog.set_df_property('run_filter', 'options', ['All Runs'].concat(analysis.run_ids));
                        dialog.set_df_property('event_filter', 'options', ['All Events'].concat(analysis.event_types));

                        // Reset filters to defaults
                        dialog.set_value('run_filter', 'All Runs');
                        dialog.set_value('level_filter', 'All Levels');
                        dialog.set_value('event_filter', 'All Events');

                        // Update log content display
                        dialog.fields_dict.log_content.$wrapper.html(render_log_table(log_entries, {}));

                        frappe.show_alert({ message: __('Log refreshed successfully'), indicator: 'green' }, 3);
                    } else {
                        frappe.msgprint({
                            title: __('Error'),
                            message: r.message.error || 'Failed to refresh log',
                            indicator: 'red'
                        });
                    }
                }
            });
        }
    });

    dialog.show();
    dialog.$wrapper.find('.modal-dialog').css('width', '95%');
}

function analyze_log_entries(log_entries) {
    let run_ids = new Set();
    let event_types = new Set();
    let levels = { INFO: 0, OK: 0, WARN: 0, ERROR: 0 };
    let summary = {
        total_entries: log_entries.length,
        runs: {},
        events: {}
    };

    let unique_ids = new Set();

    log_entries.forEach(entry => {
        if (entry.run_id) {
            run_ids.add(entry.run_id);
            if (!summary.runs[entry.run_id]) {
                summary.runs[entry.run_id] = {
                    created: 0,
                    updated: 0,
                    skipped: 0,
                    failed: 0,
                    processed: 0,
                    duration_sec: 0
                };
            }
        }

        if (entry.event) {
            event_types.add(entry.event);
            summary.events[entry.event] = (summary.events[entry.event] || 0) + 1;
        }

        if (entry.level && levels.hasOwnProperty(entry.level)) {
            levels[entry.level]++;
        }

        // Track unique record IDs
        if (entry.id) {
            unique_ids.add(entry.id);
        }

        // Extract summary data from IMPORT_END events
        if (entry.event === 'IMPORT_END' && entry.run_id) {
            summary.runs[entry.run_id].created = parseInt(entry.created) || 0;
            summary.runs[entry.run_id].updated = parseInt(entry.updated) || 0;
            summary.runs[entry.run_id].skipped = parseInt(entry.skipped) || 0;
            summary.runs[entry.run_id].failed = parseInt(entry.failed) || 0;
            summary.runs[entry.run_id].processed = parseInt(entry.processed) || 0;
            summary.runs[entry.run_id].duration_sec = parseFloat(entry.duration_sec) || 0;
        }
    });

    // Store total unique records
    summary.total_records = unique_ids.size;

    // Extract progress from the last entry that has progress data
    // Check multiple possible fields: progress, processed/total, or similar patterns
    let latest_progress = null;
    let progress_current = null;
    let progress_total = null;

    for (let i = log_entries.length - 1; i >= 0; i--) {
        let entry = log_entries[i];

        // Check if progress field exists
        if (entry.progress) {
            latest_progress = entry.progress;
            break;
        }

        // Check if processed and total fields exist
        if (entry.processed && entry.total) {
            progress_current = parseInt(entry.processed);
            progress_total = parseInt(entry.total);
            latest_progress = `${progress_current}/${progress_total}`;
            break;
        }
    }


    return {
        run_ids: Array.from(run_ids).sort().reverse(),
        event_types: Array.from(event_types).sort(),
        levels: levels,
        summary: summary,
        latest_progress: latest_progress
    };
}

function render_log_summary(analysis) {

    let runs_html = '';

    Object.keys(analysis.summary.runs).forEach(run_id => {
        let run = analysis.summary.runs[run_id];
        if (run.processed > 0) {
            runs_html += `
				<div class="col-md-6" style="margin-bottom: 10px;">
					<div style="padding: 10px; background: #f8f9fa; border-left: 3px solid #5e64ff; border-radius: 3px;">
						<strong style="font-size: 11px; color: #666;">${run_id}</strong><br>
						<div style="margin-top: 5px; font-size: 12px;">
							<span class="badge" style="background: #28a745; margin-right: 5px;">✓ ${run.created} Created</span>
							<span class="badge" style="background: #17a2b8; margin-right: 5px;">↻ ${run.updated} Updated</span>
							<span class="badge" style="background: #ffc107; margin-right: 5px;">⊘ ${run.skipped} Skipped</span>
							<span class="badge" style="background: #dc3545; margin-right: 5px;">✗ ${run.failed} Failed</span>
							<span class="badge" style="background: #6c757d;">⏱ ${run.duration_sec}s</span>
						</div>
					</div>
				</div>
			`;
        }
    });

    // Parse progress data if available (supports both "97%" and "123/456" formats)
    let progress_html = '';


    if (analysis.latest_progress) {
        let current = null;
        let total = null;
        let percentage = null;

        // Try to match fraction format first (e.g., "123/456")
        let fraction_match = analysis.latest_progress.match(/(\d+)\/(\d+)/);
        if (fraction_match) {
            current = parseInt(fraction_match[1]);
            total = parseInt(fraction_match[2]);
            percentage = total > 0 ? Math.round((current / total) * 100) : 0;
        } else {
            // Try to match percentage format (e.g., "97%")
            let percentage_match = analysis.latest_progress.match(/(\d+)%/);
            if (percentage_match) {
                percentage = parseInt(percentage_match[1]);
            }
        }

        if (percentage !== null) {
            // Determine progress bar color based on percentage
            let progress_color = percentage < 30 ? '#dc3545' :
                percentage < 70 ? '#ffc107' :
                    percentage < 100 ? '#17a2b8' : '#28a745';

            // Build display text
            let display_text = current !== null && total !== null
                ? `${current} / ${total} (${percentage}%)`
                : `${percentage}%`;

            progress_html = `
				<div class="row" style="margin-top: 15px;">
					<div class="col-md-12">
						<div style="background: #f8f9fa; padding: 15px; border-radius: 5px;">
							<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
								<strong style="font-size: 13px; color: #666;">📈 Sync Progress</strong>
								<span style="font-size: 12px; color: #666;">${display_text}</span>
							</div>
							<div class="progress" style="height: 25px; background-color: #e9ecef;">
								<div class="progress-bar progress-bar-striped" 
								     role="progressbar" 
								     style="width: ${percentage}%; background-color: ${progress_color}; font-size: 12px; font-weight: bold;"
								     aria-valuenow="${percentage}" 
								     aria-valuemin="0" 
								     aria-valuemax="100">
									${percentage}%
								</div>
							</div>
						</div>
					</div>
				</div>
			`;

        } else {
            console.log('Progress format not recognized - expected "123/456" or "97%"');
        }
    } else {
        console.log('No latest_progress available in analysis');
    }

    return `
		<div style="margin-bottom: 15px;">
			<div class="row">
				<div class="col-md-12">
					<h5 style="margin-bottom: 10px;">📊 Log Summary</h5>
				</div>
			</div>
			<div class="row">
				<div class="col-md-3">
					<div class="text-center" style="padding: 15px; background: #e8f4f8; border-radius: 5px;">
						<div style="font-size: 24px; font-weight: bold; color: #2196F3;">${(analysis.summary.total_records > 0) ? analysis.summary.total_records : analysis.summary.total_entries}</div>
						<div style="font-size: 11px; color: #666; text-transform: uppercase;">${(analysis.summary.total_records > 0) ? 'Total Records' : 'Total Entries'}</div>
					</div>
				</div>
				<div class="col-md-3">
					<div class="text-center" style="padding: 15px; background: #e8f5e9; border-radius: 5px;">
						<div style="font-size: 24px; font-weight: bold; color: #4CAF50;">${analysis.levels.OK}</div>
						<div style="font-size: 11px; color: #666; text-transform: uppercase;">Success</div>
					</div>
				</div>
				<div class="col-md-3">
					<div class="text-center" style="padding: 15px; background: #fff3e0; border-radius: 5px;">
						<div style="font-size: 24px; font-weight: bold; color: #FF9800;">${analysis.levels.WARN}</div>
						<div style="font-size: 11px; color: #666; text-transform: uppercase;">Warnings</div>
					</div>
				</div>
				<div class="col-md-3">
					<div class="text-center" style="padding: 15px; background: #ffebee; border-radius: 5px;">
						<div style="font-size: 24px; font-weight: bold; color: #F44336;">${analysis.levels.ERROR}</div>
						<div style="font-size: 11px; color: #666; text-transform: uppercase;">Errors</div>
					</div>
				</div>
			</div>
			${progress_html}
			${runs_html ? '<div class="row" style="margin-top: 15px;">' + runs_html + '</div>' : ''}
		</div>
	`;
}

function update_log_display(dialog, log_entries, analysis) {
    let filters = {
        run_id: dialog.get_value('run_filter'),
        level: dialog.get_value('level_filter'),
        event: dialog.get_value('event_filter')
    };

    let filtered_entries = log_entries.filter(entry => {
        if (filters.run_id !== 'All Runs' && entry.run_id !== filters.run_id) return false;
        if (filters.level !== 'All Levels' && entry.level !== filters.level) return false;
        if (filters.event !== 'All Events' && entry.event !== filters.event) return false;
        return true;
    });

    dialog.fields_dict.log_content.$wrapper.html(render_log_table(filtered_entries, filters));
}

function render_log_table(log_entries, filters) {
    if (log_entries.length === 0) {
        return `
			<div class="alert alert-info">
				<i class="fa fa-info-circle"></i> No log entries found matching the selected filters.
			</div>
		`;
    }

    let html = `
		<div style="margin-top: 15px;">
			<div style="margin-bottom: 10px;">
				<strong>Showing ${log_entries.length} entries</strong>
			</div>
			<div class="table-responsive" style="max-height: 500px; overflow-y: auto;">
				<table class="table table-bordered table-sm" style="font-size: 12px;">
					<thead style="position: sticky; top: 0; background: white; z-index: 10;">
						<tr>
							<th style="width: 80px;">#</th>
							<th style="width: 140px;">Timestamp</th>
							<th style="width: 60px;">Level</th>
							<th style="width: 150px;">Event</th>
							<th style="width: 200px;">ID</th>
							<th>Details</th>
						</tr>
					</thead>
					<tbody>
	`;

    let total_entries = log_entries.length;

    // Group entries by ID to assign group indices
    let id_to_group_index = {};
    let group_counter = 0;
    let total_groups = 0;

    // First pass: assign group indices to unique IDs
    log_entries.forEach((entry) => {
        let entry_id = entry.id || entry.source || 'unknown';
        if (!id_to_group_index.hasOwnProperty(entry_id)) {
            group_counter++;
            id_to_group_index[entry_id] = group_counter;
        }
    });

    total_groups = group_counter;

    // Second pass: render table rows with group indices
    let current_group_index = null;
    let group_row_count = 0;

    log_entries.forEach((entry, idx) => {
        let level_badge = get_level_badge(entry.level);
        let event_badge = get_event_badge(entry.event);
        let details = get_entry_details(entry);

        let entry_id = entry.id || entry.source || 'unknown';
        let group_index = id_to_group_index[entry_id];

        // Check if we're starting a new group
        let is_first_in_group = (group_index !== current_group_index);
        if (is_first_in_group) {
            current_group_index = group_index;
            group_row_count = log_entries.filter(e => (e.id || e.source || 'unknown') === entry_id).length;
        }

        let index_display = is_first_in_group
            ? `${group_index}/${total_groups}`
            : '';

        // Add rowspan for the first row in a group
        let index_cell = is_first_in_group && group_row_count > 1
            ? `<td rowspan="${group_row_count}" style="vertical-align: middle; background: #f8f9fa;"><small style="color: #666; font-weight: bold;">${index_display}</small></td>`
            : is_first_in_group
                ? `<td style="background: #f8f9fa;"><small style="color: #666; font-weight: bold;">${index_display}</small></td>`
                : '';

        html += `
			<tr style="${get_row_style(entry.level)}">
				${index_cell}
				<td><small>${entry.timestamp || '-'}</small></td>
				<td>${level_badge}</td>
				<td>${event_badge}</td>
				<td><code style="font-size: 10px; word-break: break-all;">${entry.id || entry.source || '-'}</code></td>
				<td>${details}</td>
			</tr>
		`;
    });

    html += `
					</tbody>
				</table>
			</div>
		</div>
	`;

    return html;
}

function get_level_badge(level) {
    const badges = {
        'INFO': '<span class="badge" style="background: #2196F3;">INFO</span>',
        'OK': '<span class="badge" style="background: #4CAF50;">OK</span>',
        'WARN': '<span class="badge" style="background: #FF9800;">WARN</span>',
        'ERROR': '<span class="badge" style="background: #F44336;">ERROR</span>'
    };
    return badges[level] || `<span class="badge" style="background: #999;">${level || 'N/A'}</span>`;
}

function get_event_badge(event) {
    const colors = {
        'IMPORT_START': '#9C27B0',
        'IMPORT_END': '#9C27B0',
        'RECORD_START': '#607D8B',
        'RECORD_SUCCESS': '#4CAF50',
        'RECORD_SKIPPED': '#FF9800',
        'RECORD_FAILED': '#F44336'
    };
    let color = colors[event] || '#999';
    return `<span class="badge" style="background: ${color}; font-size: 10px;">${event || 'N/A'}</span>`;
}

function get_row_style(level) {
    if (level === 'ERROR') return 'background-color: #ffebee;';
    if (level === 'WARN') return 'background-color: #fff3e0;';
    if (level === 'OK') return 'background-color: #e8f5e9;';
    return '';
}

function get_entry_details(entry) {
    let details = [];

    // Add relevant fields based on event type
    if (entry.action) {
        details.push(`<strong>Action:</strong> ${entry.action}`);
    }
    if (entry.reason) {
        details.push(`<strong>Reason:</strong> ${entry.reason}`);
    }
    if (entry.error) {
        details.push(`<strong>Error:</strong> <span style="color: #F44336;">${frappe.utils.escape_html(entry.error)}</span>`);
    }
    if (entry.duration_ms) {
        details.push(`<strong>Duration:</strong> ${entry.duration_ms}ms`);
    }
    if (entry.progress) {
        details.push(`<strong>Progress:</strong> ${entry.progress}`);
    }
    if (entry.total) {
        details.push(`<strong>Total:</strong> ${entry.total}`);
    }
    if (entry.processed) {
        details.push(`<strong>Processed:</strong> ${entry.processed}`);
    }
    if (entry.created) {
        details.push(`<strong>Created:</strong> ${entry.created}`);
    }
    if (entry.updated) {
        details.push(`<strong>Updated:</strong> ${entry.updated}`);
    }
    if (entry.skipped) {
        details.push(`<strong>Skipped:</strong> ${entry.skipped}`);
    }
    if (entry.failed) {
        details.push(`<strong>Failed:</strong> ${entry.failed}`);
    }
    if (entry.duration_sec) {
        details.push(`<strong>Duration:</strong> ${entry.duration_sec}s`);
    }

    // If no specific details, show raw line if available
    if (details.length === 0 && entry.raw_line) {
        return `<code style="font-size: 10px;">${frappe.utils.escape_html(entry.raw_line)}</code>`;
    }

    return details.join(' | ') || '-';
}

function export_log_to_csv(filename, log_entries) {
    let csv = 'Timestamp,Level,Event,Run ID,Source,ID,Action,Reason,Error,Duration (ms),Progress\n';

    log_entries.forEach(entry => {
        let row = [
            entry.timestamp || '',
            entry.level || '',
            entry.event || '',
            entry.run_id || '',
            entry.source || '',
            entry.id || '',
            entry.action || '',
            entry.reason || '',
            (entry.error || '').replace(/"/g, '""'),
            entry.duration_ms || '',
            entry.progress || ''
        ];
        csv += row.map(field => `"${field}"`).join(',') + '\n';
    });

    let dataStr = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
    let downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", filename.replace('.log', '.csv'));
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();

    frappe.show_alert({ message: __('Log exported to CSV'), indicator: 'green' }, 3);
}
