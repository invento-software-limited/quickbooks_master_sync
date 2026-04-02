frappe.pages["quickbooks-balance-comparison"].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "QuickBooks Balance Comparison",
		single_column: true,
	});

	// Add refresh button
	page.add_inner_button(
		__("Refresh"),
		function () {
			show_comparison_form(page);
		},
		__("Actions")
	);

	// Add export button if data exists
	page.add_inner_button(
		__("Export to Excel"),
		function () {
			export_to_excel();
		},
		__("Actions")
	);

	let comparison_data = null;

	// Get data from route
	let route = frappe.get_route();
	if (route[2] && route[2].result) {
		try {
			comparison_data = JSON.parse(decodeURIComponent(route[2].result));
		} catch (e) {
			console.error("Error parsing comparison data:", e);
		}
	}

	// If no data in route, try to get from frappe.route_options
	if (!comparison_data && frappe.route_options) {
		if (frappe.route_options.result) {
			try {
				comparison_data =
					typeof frappe.route_options.result === "string"
						? JSON.parse(frappe.route_options.result)
						: frappe.route_options.result;
			} catch (e) {
				console.error("Error parsing comparison data:", e);
			}
		}
	}

	if (comparison_data) {
		render_comparison_results(page, comparison_data);
	} else {
		// Show form to run comparison
		show_comparison_form(page);
	}
};

function export_to_excel() {
	if (!window.balance_comparison_datatables) {
		frappe.msgprint(__("No data to export. Please run comparison first."));
		return;
	}

	// Get active tab
	let active_tab = $(".nav-tabs .nav-link.active").attr("href");
	let container_id = active_tab.replace("#", "") + "-table";

	if (window.balance_comparison_datatables[container_id]) {
		window.balance_comparison_datatables[container_id].export();
	} else {
		frappe.msgprint(__("No data available in current tab."));
	}
}

function show_comparison_form(page) {
	let form = $(`
		<div class="balance-comparison-form" style="max-width: 600px; margin: 20px auto;">
			<div class="card">
				<div class="card-body">
					<h5>Run Balance Comparison</h5>
					<div id="comparison-form-fields"></div>
					<button class="btn btn-primary btn-sm" id="run-comparison-btn" style="margin-top: 15px;">
						Compare Balances
					</button>
				</div>
			</div>
		</div>
	`).appendTo(page.body);

	let fields = [
		{
			label: "Company",
			fieldtype: "Link",
			fieldname: "company",
			options: "Company",
			reqd: 1,
			default:
				frappe.route_options && frappe.route_options.company
					? frappe.route_options.company
					: frappe.defaults.get_user_default("company"),
		},
		{
			label: "As of / End Date",
			fieldtype: "Date",
			fieldname: "as_of_date",
			reqd: 1,
			default:
				frappe.route_options && frappe.route_options.as_of_date
					? frappe.route_options.as_of_date
					: frappe.datetime.get_today(),
		},
		{
			label: "Tolerance",
			fieldtype: "Float",
			fieldname: "tolerance",
			default: 0.01,
			description: "Maximum allowed difference between balances",
		},
	];

	let form_wrapper = new frappe.ui.FieldGroup({
		fields: fields,
		body: $("#comparison-form-fields"),
	});
	form_wrapper.make();

	$("#run-comparison-btn").on("click", function () {
		let values = form_wrapper.get_values();
		if (!values.company) {
			frappe.msgprint(__("Please select a company"));
			return;
		}

		frappe.call({
			method: "quickbooks_master_sync.quickbooks_master_sync.api.compare_quickbooks_erpnext_balances",
			args: {
				company_name: values.company,
				as_of_date: values.as_of_date,
				tolerance: values.tolerance || 0.01,
			},
			freeze: true,
			freeze_message: __("Comparing balances... Please wait..."),
			callback: function (r) {
				if (r.message && r.message.success && r.message.result) {
					render_comparison_results(page, r.message.result);
				} else {
					frappe.msgprint({
						title: __("Error"),
						message:
							r.message && r.message.error
								? r.message.error
								: __("Failed to compare balances."),
						indicator: "red",
					});
				}
			},
		});
	});

	// Auto-run if requested via route options (and not already rendering result)
	if (frappe.route_options && frappe.route_options.run && frappe.route_options.company) {
		// Wait a bit for form to be ready
		setTimeout(() => {
			$("#run-comparison-btn").click();
		}, 500);
	}
}

function render_comparison_results(
	page,
	data,
	is_date_wise = false,
	show_only_mismatched = false,
	account_info = null
) {
	let rows_to_render = data.date_wise || [];
	if (is_date_wise && show_only_mismatched && data.date_wise) {
		rows_to_render = data.date_wise.filter((row) => !row.matched);
	}
	let summary = data.summary || {};

	// Clear previous content
	page.body.empty();

	// Render summary or specialized view
	let summary_html = "";
	if (!is_date_wise) {
		summary_html = `
			<div class="summary-cards" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0;">
				<div class="card">
					<div class="card-body text-center">
						<h4>${summary.matched_accounts || 0}</h4>
						<p class="text-muted mb-0">Matched</p>
					</div>
				</div>
				<div class="card">
					<div class="card-body text-center">
						<h4 class="text-warning">${summary.mismatched_accounts || 0}</h4>
						<p class="text-muted mb-0">Mismatched</p>
					</div>
				</div>
				<div class="card">
					<div class="card-body text-center">
						<h4 class="text-info">${summary.qb_only_accounts || 0}</h4>
						<p class="text-muted mb-0">QB Only</p>
					</div>
				</div>
				<div class="card">
					<div class="card-body text-center">
						<h4 class="text-info">${summary.erp_only_accounts || 0}</h4>
						<p class="text-muted mb-0">ERP Only</p>
					</div>
				</div>
			</div>

			<div class="row">
				<div class="col-md-12">
					<table class="table table-bordered table-sm">
						<thead>
							<tr>
								<th>Category</th>
								<th class="text-right">QuickBooks</th>
								<th class="text-right">ERPNext</th>
								<th class="text-right">Difference</th>
							</tr>
						</thead>
						<tbody>
							${render_category_row(
								"Assets",
								summary.total_asset_qb,
								summary.total_asset_erp,
								summary.total_asset_diff,
								summary.tolerance
							)}
							${render_category_row(
								"Liabilities",
								summary.total_liability_qb,
								summary.total_liability_erp,
								summary.total_liability_diff,
								summary.tolerance
							)}
							${render_category_row(
								"Equity",
								summary.total_equity_qb,
								summary.total_equity_erp,
								summary.total_equity_diff,
								summary.tolerance
							)}
							${render_category_row(
								"Income",
								summary.total_income_qb,
								summary.total_income_erp,
								summary.total_income_diff,
								summary.tolerance
							)}
							${render_category_row(
								"Expenses",
								summary.total_expense_qb,
								summary.total_expense_erp,
								summary.total_expense_diff,
								summary.tolerance
							)}
							${
								(summary.total_uncategorized_qb || 0) !== 0
									? render_category_row(
											"Uncategorized (QB Only)",
											summary.total_uncategorized_qb,
											summary.total_uncategorized_erp,
											summary.total_uncategorized_diff,
											summary.tolerance
									  )
									: ""
							}
						</tbody>
					</table>
				</div>
			</div>
		`;
	} else {
		let erp_total = 0;
		let qb_total = 0;
		let mismatched_days = 0;

		(data.date_wise || []).forEach((row) => {
			erp_total += (parseFloat(row.erp_debit) || 0) - (parseFloat(row.erp_credit) || 0);
			qb_total += (parseFloat(row.qb_debit) || 0) - (parseFloat(row.qb_credit) || 0);
			if (!row.matched) mismatched_days++;
		});

		let diff_total = erp_total - qb_total;
		let diff_class =
			Math.abs(diff_total) > (summary.tolerance || 0.01) ? "text-danger" : "text-success";

		summary_html = `
			<div class="date-wise-header" style="margin-bottom: 20px;">
				<h5 class="text-muted">${__("Date-Wise Balance Comparison")}</h5>
				<h4 style="margin-bottom: 5px;">${
					account_info ? account_info.account_name : __("Unknown Account")
				}</h4>
				<p class="text-muted"><small>${__("QuickBooks ID")}: ${
			account_info ? account_info.qb_id : __("N/A")
		}</small></p>
			</div>

			<div class="summary-cards" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0;">
				<div class="card">
					<div class="card-body text-center">
						<h4 class="text-primary">${format_currency(erp_total)}</h4>
						<p class="text-muted mb-0">ERPNext Net</p>
					</div>
				</div>
				<div class="card">
					<div class="card-body text-center">
						<h4 class="text-primary">${format_currency(qb_total)}</h4>
						<p class="text-muted mb-0">QuickBooks Net</p>
					</div>
				</div>
				<div class="card">
					<div class="card-body text-center">
						<h4 class="${diff_class}">${format_currency(diff_total)}</h4>
						<p class="text-muted mb-0">Difference (Net)</p>
					</div>
				</div>
				<div class="card mismatched-days-card" style="cursor: pointer; ${
					show_only_mismatched ? "border: 2px solid var(--red);" : ""
				}">
					<div class="card-body text-center">
						<h4 class="${mismatched_days > 0 ? "text-danger" : "text-success"}">${mismatched_days}</h4>
						<p class="text-muted mb-0">${
							show_only_mismatched
								? __("Showing Mismatched Only")
								: __("Mismatched Days")
						}</p>
					</div>
				</div>
			</div>
		`;
	}

	function render_category_row(title, qb_val, erp_val, diff_val, tolerance) {
		let diff_class =
			Math.abs(diff_val || 0) > (tolerance || 0.01)
				? "text-danger font-weight-bold"
				: "text-success";
		let diff_note = diff_val < 0 ? ' <small class="text-muted">(QB < ERP)</small>' : "";

		return `
        <tr>
            <td><strong>${title}</strong></td>
            <td class="text-right">${format_currency(Math.abs(qb_val || 0))}</td>
            <td class="text-right">${format_currency(Math.abs(erp_val || 0))}</td>
            <td class="text-right ${diff_class}">
                ${format_currency(Math.abs(diff_val || 0))}
                ${diff_note}
            </td>
        </tr>
    `;
	}

	// Add summary and tabs container
	let tabs_html = is_date_wise
		? `
		<li class="nav-item">
			<a class="nav-link active" data-toggle="tab" data-target="#date-wise-tab" href="javascript:void(0);" role="tab" aria-controls="date-wise-tab" aria-selected="true">Date-Wise Comparison</a>
		</li>
	`
		: `
		<li class="nav-item">
			<a class="nav-link active" data-toggle="tab" data-target="#supplier-tab" href="javascript:void(0);" role="tab" aria-controls="supplier-tab" aria-selected="false">Suppliers</a>
		</li>
		<li class="nav-item">
			<a class="nav-link" data-toggle="tab" data-target="#customer-tab" href="javascript:void(0);" role="tab" aria-controls="customer-tab" aria-selected="false">Customers</a>
		</li>
		<li class="nav-item">
			<a class="nav-link " data-toggle="tab" data-target="#matched-tab" href="javascript:void(0);" role="tab" aria-controls="matched-tab" aria-selected="true">Matched (<span id="matched-count">${
				summary.matched_accounts || 0
			}</span>)</a>
		</li>
		<li class="nav-item">
			<a class="nav-link" data-toggle="tab" data-target="#mismatched-tab" href="javascript:void(0);" role="tab" aria-controls="mismatched-tab" aria-selected="false">Mismatched (<span id="mismatched-count">${
				summary.mismatched_accounts || 0
			}</span>)</a>
		</li>
		<li class="nav-item">
			<a class="nav-link" data-toggle="tab" data-target="#qb-only-tab" href="javascript:void(0);" role="tab" aria-controls="qb-only-tab" aria-selected="false">QuickBooks Only (<span id="qb-only-count">${
				summary.qb_only_accounts || 0
			}</span>)</a>
		</li>
		<li class="nav-item">
			<a class="nav-link" data-toggle="tab" data-target="#erp-only-tab" href="javascript:void(0);" role="tab" aria-controls="erp-only-tab" aria-selected="false">ERPNext Only (<span id="erp-only-count">${
				summary.erp_only_accounts || 0
			}</span>)</a>
		</li>
	`;

	const _premium_styles = `
		<style>
			.qb-premium-container {
				position: relative;
				min-height: 420px;
				overflow: hidden;
				border-radius: 12px;
				border: 1px solid var(--border-color, #e2e8f0);
				background: var(--card-bg, #fff);
				box-shadow: 0 4px 20px rgba(0,0,0,0.06);
				margin-top: 8px;
			}
			.qb-blurred-bg {
				filter: blur(7px);
				opacity: 0.35;
				pointer-events: none;
				user-select: none;
				padding: 12px;
			}
			.qb-premium-gate {
				position: absolute;
				inset: 0;
				display: flex;
				flex-direction: column;
				align-items: center;
				justify-content: center;
				background: rgba(255,255,255,0.70);
				backdrop-filter: blur(5px);
				-webkit-backdrop-filter: blur(5px);
				z-index: 20;
				padding: 2.5rem;
				text-align: center;
			}
			body[data-theme="dark"] .qb-premium-gate {
				background: rgba(18,18,28,0.78);
			}
			.qb-premium-gate .invento-logo-svg {
				margin-bottom: 18px;
				filter: drop-shadow(0 4px 10px rgba(10,100,220,0.18));
			}
			.qb-premium-gate h2 {
				font-size: 1.55rem;
				font-weight: 700;
				letter-spacing: -0.3px;
				margin-bottom: 8px;
				color: var(--text-color, #1a202c);
			}
			.qb-premium-gate p {
				font-size: 1.08rem;
				max-width: 440px;
				color: var(--text-muted, #718096);
				line-height: 1.65;
				margin-bottom: 1.6rem;
			}
			.qb-premium-gate .qb-contact-btn {
				display: inline-flex;
				align-items: center;
				gap: 8px;
				border-radius: 30px;
				padding: 11px 30px;
				font-size: 1rem;
				font-weight: 600;
				background: linear-gradient(135deg, #008B47, #0E7059, #2F3181);
				color: white;
				border: none;
				text-decoration: none;
				box-shadow: 0 4px 14px rgba(0,139,71,0.35);
				transition: transform 0.18s, box-shadow 0.18s;
			}
			.qb-premium-gate .qb-contact-btn:hover {
				transform: translateY(-2px);
				box-shadow: 0 7px 20px rgba(0,139,71,0.45);
				text-decoration: none;
				color: white;
			}
			.qb-premium-gate .qb-badge {
				display: inline-block;
				background: rgba(0,139,71,0.12);
				color: #008B47;
				border-radius: 20px;
				padding: 3px 14px;
				font-size: 0.8rem;
				font-weight: 600;
				letter-spacing: 0.5px;
				margin-bottom: 14px;
				text-transform: uppercase;
			}
		</style>
	`;

	const _premium_gate_html = `
		<div class="qb-premium-gate">
			<div class="qb-badge">Premium Feature</div>
			<img src="/assets/quickbooks_master_sync/img/invento-logo-color.png" class="invento-logo-svg" width="350" height="auto">
			</br><p>This feature is only available in the full version.<br>Contact us to unlock advanced balance comparison.</p>
			<a href="mailto:munim@invento.com.bd" class="qb-contact-btn">
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
				munim@invento.com.bd
			</a>
		</div>
	`;

	let tab_content_html = is_date_wise
		? `
		<div id="date-wise-tab" class="tab-pane fade show active" role="tabpanel" aria-labelledby="date-wise-tab">
			<div id="date-wise-table"></div>
		</div>
	`
		: `
		<div id="matched-tab" class="tab-pane fade show active" role="tabpanel" aria-labelledby="matched-tab">
			<div class="qb-premium-container">
				${_premium_gate_html}
			</div>
		</div>
		<div id="mismatched-tab" class="tab-pane fade" role="tabpanel" aria-labelledby="mismatched-tab">
			<div class="qb-premium-container">
				${_premium_gate_html}
			</div>
		</div>
		<div id="qb-only-tab" class="tab-pane fade" role="tabpanel" aria-labelledby="qb-only-tab">
			<div class="qb-premium-container">
				${_premium_gate_html}
			</div>
		</div>
		<div id="erp-only-tab" class="tab-pane fade" role="tabpanel" aria-labelledby="erp-only-tab">
			<div class="qb-premium-container">
				${_premium_gate_html}
			</div>
		</div>
		<div id="supplier-tab" class="tab-pane fade" role="tabpanel" aria-labelledby="supplier-tab">
			<div id="supplier-table"></div>
		</div>
		<div id="customer-tab" class="tab-pane fade" role="tabpanel" aria-labelledby="customer-tab">
			<div id="customer-table"></div>
		</div>
	`;

	let content_html = `
		<div class="balance-comparison-container">
			${_premium_styles}
			<div id="comparison-summary" class="summary-section">${summary_html}</div>
			<div class="tabs-container" style="margin-top: 20px;">
				<ul class="nav nav-tabs" role="tablist" id="balance-tabs">
					${tabs_html}
				</ul>
				<div class="tab-content" id="balance-tab-content" style="margin-top: 15px;">
					${tab_content_html}
				</div>
			</div>
		</div>
	`;

	page.body.html(content_html);

	// Store table configurations for lazy loading (only non-gated tables)
	let table_configs = {};

	// Track which tables have been rendered
	let rendered_tables = {
		"supplier-table": false,
		"customer-table": false,
		"date-wise-table": false,
	};

	// Helper to fetch and render party data
	function fetch_and_render_party(party_type, table_id) {
		let container = $(`#${table_id}`);
		container.html(
			`<div class="text-center p-5"><div class="spinner-border text-primary" role="status"></div><p class="mt-2">Fetching ${party_type} balances...</p></div>`
		);

		frappe.call({
			method: "quickbooks_master_sync.quickbooks_master_sync.api.compare_party_balances",
			args: {
				party_type: party_type,
				company_name: summary.company || frappe.defaults.get_user_default("company"),
				as_of_date: summary.as_of_date || frappe.datetime.get_today(),
				tolerance: summary.tolerance || 0.01,
			},
			callback: function (r) {
				if (r.message) {
					table_configs[table_id] = {
						data: r.message || [],
						columns: [
							{ fieldname: "qb_id", label: __("QB ID") },
							{ fieldname: "qb_name", label: __("QuickBooks Name") },
							{ fieldname: "erp_name", label: __("ERPNext Name") },
							{
								fieldname: "qb_balance",
								label: __("QB Balance"),
								fieldtype: "Currency",
							},
							{
								fieldname: "erp_balance",
								label: __("ERP Balance"),
								fieldtype: "Currency",
							},
							{
								fieldname: "difference",
								label: __("Difference"),
								fieldtype: "Currency",
							},
						],
						row_formatter: (row) => {
							let erp_link =
								row.erp_name && row.erp_name !== "-"
									? `<a href="/app/${party_type.toLowerCase()}/${encodeURIComponent(
											row.erp_name
									  )}" target="_blank">${row.erp_name}</a>`
									: row.erp_name || "-";

							return {
								qb_id: row.qb_id,
								qb_name: row.qb_name || "-",
								erp_name: erp_link,
								qb_balance: parseFloat(row.qb_balance) || 0,
								erp_balance: parseFloat(row.erp_balance) || 0,
								difference: parseFloat(row.difference) || 0,
								_matched: row.matched,
							};
						},
						table_class: (row) => (row.matched ? "" : "warning"),
					};

					rendered_tables[table_id] = false;
					render_table_if_needed(table_id);
				}
			},
		});
	}

	if (is_date_wise) {
		table_configs["date-wise-table"] = {
			data: rows_to_render,
			columns: [
				{ fieldname: "date", label: __("Date") },
				{ fieldname: "erp_debit", label: __("ERP Debit"), fieldtype: "Currency" },
				{ fieldname: "erp_credit", label: __("ERP Credit"), fieldtype: "Currency" },
				{ fieldname: "qb_debit", label: __("QB Debit"), fieldtype: "Currency" },
				{ fieldname: "qb_credit", label: __("QB Credit"), fieldtype: "Currency" },
				{ fieldname: "debit_diff", label: __("DR Diff"), fieldtype: "Currency" },
				{ fieldname: "credit_diff", label: __("CR Diff"), fieldtype: "Currency" },
				{ fieldname: "actions", label: __("Actions") },
			],
			row_formatter: (row) => {
				let date_link = row.date;
				if (account_info && account_info.erp_account && account_info.company) {
					let gl_url = `/app/query-report/General%20Ledger?from_date=${
						row.date
					}&to_date=${row.date}&account=${encodeURIComponent(
						account_info.erp_account
					)}&company=${encodeURIComponent(account_info.company)}`;
					date_link = `<a href="${gl_url}" target="_blank">${row.date}</a>`;
				}

				return {
					date: date_link,
					erp_debit: row.erp_debit,
					erp_credit: row.erp_credit,
					qb_debit: row.qb_debit,
					qb_credit: row.qb_credit,
					debit_diff: row.debit_diff,
					credit_diff: row.credit_diff,
					_matched: row.matched,
					actions: `<button class="btn btn-xs btn-default view-transactions-btn" data-date="${
						row.date
					}" title="${__(
						"View Daily Transactions"
					)}"><i class="fa fa-list"></i></button>`,
				};
			},
			row_class_formatter: (row) => {
				return row.matched ? "table-success" : "table-danger";
			},
		};
	}

	// Function to render a specific table if not already rendered
	function render_table_if_needed(table_id) {
		// Normalize table_id - remove # if present
		table_id = table_id.replace("#", "");

		if (rendered_tables[table_id]) {
			return; // Already rendered
		}

		if (!table_configs[table_id]) {
			console.error("No config found for table:", table_id);
			return; // No config
		}

		let config = table_configs[table_id];

		// Always render - we're calling this when tab becomes active
		try {
			render_datatable(
				table_id,
				config.data,
				config.columns,
				config.row_formatter,
				config.table_class
			);
			rendered_tables[table_id] = true;
		} catch (error) {
			console.error("Error rendering table:", table_id, error);
		}
	}

	// Initialize tabs immediately after HTML is set
	// Use data-target instead of href to avoid navigation issues
	setTimeout(() => {
		// Initialize Bootstrap tabs properly - scope to page body
		page.body
			.find('#balance-tabs a[data-toggle="tab"]')
			.off("click")
			.on("click", function (e) {
				e.preventDefault();
				e.stopPropagation();
				let target = $(this).data("target");
				if (target && page.body.find(target).length) {
					// Show the target tab
					page.body
						.find('#balance-tabs a[data-toggle="tab"]')
						.removeClass("active")
						.attr("aria-selected", "false");
					page.body.find("#balance-tab-content .tab-pane").removeClass("show active");
					$(this).addClass("active").attr("aria-selected", "true");
					page.body.find(target).addClass("show active");

					// Render table for the newly active tab
					let table_id = target.replace("-tab", "-table").replace("#", ""); // Remove # if present

					if (table_id === "supplier-table" && !rendered_tables[table_id]) {
						fetch_and_render_party("Supplier", table_id);
					} else if (table_id === "customer-table" && !rendered_tables[table_id]) {
						fetch_and_render_party("Customer", table_id);
					} else {
						setTimeout(() => {
							render_table_if_needed(table_id);
						}, 100);
					}
				}
				return false;
			});
	}, 100);

	// Trigger click on the active tab to ensure it renders
	setTimeout(() => {
		let active_tab = page.body.find("#balance-tabs .nav-link.active");
		if (active_tab.length) {
			active_tab.click();
		} else {
			// Fallback if no tab is active (shouldn't happen as we set the first one active in HTML)
			page.body.find("#balance-tabs .nav-link").first().click();
		}
	}, 300);

	page.body
		.off("click", ".mismatched-days-card")
		.on("click", ".mismatched-days-card", function () {
			render_comparison_results(
				page,
				data,
				is_date_wise,
				!show_only_mismatched,
				account_info
			);
		});

	page.body.off("click", ".compare-date-btn").on("click", ".compare-date-btn", function () {
		let account = $(this).data("account");
		let row_data = null;

		// Find the row data to get both ERP and QB IDs
		if (data.matched) {
			row_data = data.matched.find((r) => (r.erp_account || r.qb_id) == account);
		}
		if (!row_data && data.mismatched) {
			row_data = data.mismatched.find((r) => (r.erp_account || r.qb_id) == account);
		}

		if (!row_data) {
			frappe.msgprint(__("Could not find account data for comparison."));
			return;
		}

		let d = new frappe.ui.Dialog({
			title: __("Compare Date-Wise: {0}", [row_data.qb_name || row_data.erp_name]),
			fields: [
				{
					label: __("Company"),
					fieldtype: "Data",
					fieldname: "company",
					default: summary.company || frappe.defaults.get_user_default("company"),
					read_only: 1,
				},
				{
					label: __("Account"),
					fieldtype: "Data",
					fieldname: "account_name",
					default: row_data.qb_name || row_data.erp_name,
					read_only: 1,
				},
				{
					label: __("Start Date"),
					fieldtype: "Date",
					fieldname: "start_date",
					default: "2015-01-01",
					reqd: 1,
				},
				{
					label: __("End Date"),
					fieldtype: "Date",
					fieldname: "end_date",
					default: frappe.datetime.get_today(),
					reqd: 1,
				},
				{
					label: __("Show only differences"),
					fieldtype: "Check",
					fieldname: "show_only_mismatched",
					default: 1,
				},
				{
					label: __("Tolerance"),
					fieldtype: "Float",
					fieldname: "tolerance",
					default: summary.tolerance || 0.01,
				},
			],
			primary_action_label: __("Compare"),
			primary_action(values) {
				d.hide();
				frappe.call({
					method: "quickbooks_master_sync.quickbooks_master_sync.api.compare_quickbooks_erpnext_date_wise_balances",
					args: {
						company_name: values.company,
						start_date: values.start_date,
						end_date: values.end_date,
						erp_account: row_data.erp_account,
						qb_id: row_data.qb_id,
						tolerance: values.tolerance || 0.01,
					},
					freeze: true,
					freeze_message: __("Comparing date-wise balances for account..."),
					callback: function (r) {
						if (r.message && r.message.success && r.message.result) {
							// We need to keep the original data context to allow switching back?
							// For now, let's just render the results view.
							// To allow "Back", we'd need to store the previous view state.
							render_comparison_results(
								page,
								{ date_wise: r.message.result },
								true,
								values.show_only_mismatched,
								{
									account_name: row_data.qb_name || row_data.erp_name,
									qb_id: row_data.qb_id,
									erp_account: row_data.erp_account,
									company: values.company,
								}
							);

							// Add a back button
							page.set_primary_action(__("Back to Summary"), () => {
								render_comparison_results(page, data);
								page.set_primary_action(__("Refresh"), () =>
									show_comparison_form(page)
								);
							});
						} else {
							frappe.msgprint({
								title: __("Error"),
								message:
									r.message && r.message.error
										? r.message.error
										: __("Failed to compare date-wise balances."),
								indicator: "red",
							});
						}
					},
				});
			},
		});
		d.show();
	});

	page.body
		.off("click", ".view-transactions-btn")
		.on("click", ".view-transactions-btn", function () {
			let date = $(this).data("date");
			if (!account_info || !account_info.erp_account || !account_info.qb_id) {
				frappe.msgprint(__("Missing account information for transaction comparison."));
				return;
			}

			frappe.call({
				method: "quickbooks_master_sync.compare_balances.get_daily_transactions_comparison",
				args: {
					company_name:
						account_info.company || frappe.defaults.get_user_default("company"),
					erp_account: account_info.erp_account,
					qb_id: account_info.qb_id,
					date: date,
				},
				freeze: true,
				freeze_message: __("Fetching transactions for {0}...", [date]),
				callback: function (r) {
					if (r.message) {
						let refresh_fn = () => {
							// Trigger the compare-date-btn primary action logic again
							// This is a bit tricky, but since we are in render_comparison_results,
							// we can re-call the same frappe.call logic or better, just re-trigger the original comparison
							// However, the comparison parameters are in the Dialog 'd' from compare-date-btn click handler which is closed.
							// A better way is to define a local refresher.

							frappe.call({
								method: "quickbooks_master_sync.quickbooks_master_sync.api.compare_quickbooks_erpnext_date_wise_balances",
								args: {
									company_name: account_info.company,
									start_date: "2015-01-01", // Ideally we'd have the specific range used
									end_date: frappe.datetime.get_today(),
									erp_account: account_info.erp_account,
									qb_id: account_info.qb_id,
									tolerance: 0.01,
									show_only_mismatched: 1,
								},
								freeze: true,
								callback: function (r_refresh) {
									if (r_refresh.message && r_refresh.message.success) {
										render_comparison_results(
											page,
											{ date_wise: r_refresh.message.result },
											true,
											false,
											account_info
										);
									}
								},
							});
						};
						show_transaction_comparison_dialog(
							date,
							account_info.account_name,
							r.message,
							refresh_fn
						);
					}
				},
			});
		});
}

function show_transaction_comparison_dialog(date, account_name, data, refresh_callback) {
	let d = new frappe.ui.Dialog({
		title: `${__("Daily Transactions")}: ${account_name} (${date})`,
		size: "extra-large",
	});

	let erp_txns = data.erp_transactions || [];
	let qb_txns = data.qb_transactions || [];
	let root_type = data.root_type;

	// Pre-calculate net amounts and initialize matched flag
	erp_txns.forEach((t) => {
		t._net = (parseFloat(t.debit) || 0) - (parseFloat(t.credit) || 0);
		t._matched = false;
	});
	qb_txns.forEach((t) => {
		let amt = parseFloat(t.amount) || 0;

		// In QB GL Report "Amount" column:
		// For Liability/Equity/Income (Normal Credit): Positive = Credit, Negative = Debit.
		// ERPNext _net = Debit - Credit.
		// So if Normal Credit: ERPNext _net = (-1) * QB amt.
		// If Normal Debit: ERPNext _net = (1) * QB amt.

		let is_normal_credit = ["Liability", "Equity", "Income"].includes(root_type);
		t._net = is_normal_credit ? -1 * amt : amt;
		t._matched = false;
	});

	// Matching logic (greedy approach to handle multiple transactions with same amount)
	erp_txns.forEach((e) => {
		let match = qb_txns.find((q) => !q._matched && Math.abs(q._net - e._net) < 0.01);
		if (match) {
			e._matched = true;
			match._matched = true;
		}
	});

	let erp_rows = erp_txns
		.map((t) => {
			let slug = t.voucher_type.toLowerCase().replace(/ /g, "-");
			let link = `/app/${slug}/${encodeURIComponent(t.voucher_no)}`;
			let row_class = t._matched ? "table-success" : "table-danger";
			return `
			<tr class="${row_class}">
				<td>${t.voucher_type}</td>
				<td><a href="${link}" target="_blank">${t.voucher_no}</a></td>
				<td>${t.party || ""}</td>
				<td class="text-right">${format_currency(t.debit)}</td>
				<td class="text-right">${format_currency(t.credit)}</td>
			</tr>
		`;
		})
		.join("");

	let qb_rows = qb_txns
		.map((t) => {
			let row_class = t._matched ? "table-success" : "table-danger";
			return `
			<tr class="${row_class}">
				<td>${t.type || ""}</td>
				<td>${t.no || ""}</td>
				<td>${t.name || ""}</td>
				<td>${t.memo || ""}</td>
				<td class="text-right">${format_currency(t.amount)}</td>
				<td class="text-center">
					<button class="btn btn-xs btn-default search-journal-btn"
						data-date="${date}"
						data-account-name="${account_name}"
						data-amount="${t.amount}"
						title="${__("Search in Journal Report")}">
						<i class="fa fa-search"></i>
					</button>
				</td>
			</tr>
		`;
		})
		.join("");

	let html = `
		<div class="row">
			<div class="col-md-6">
				<h6>ERPNext Transactions</h6>
				<div style="max-height: 400px; overflow-y: auto;">
					<table class="table table-bordered table-sm" style="font-size: 0.9em;">
						<thead>
							<tr>
								<th>Type</th>
								<th>No</th>
								<th>Party</th>
								<th class="text-right">Debit</th>
								<th class="text-right">Credit</th>
							</tr>
						</thead>
						<tbody>
							${erp_rows || '<tr><td colspan="5" class="text-center text-muted">No transactions</td></tr>'}
						</tbody>
					</table>
				</div>
			</div>
			<div class="col-md-6">
				<h6>QuickBooks Transactions</h6>
				<div style="max-height: 400px; overflow-y: auto;">
					<table class="table table-bordered table-sm" style="font-size: 0.9em;">
						<thead>
							<tr>
								<th>Type</th>
								<th>No</th>
								<th>Name</th>
								<th>Memo</th>
								<th class="text-right">Amount</th>
								<th class="text-center">Action</th>
							</tr>
						</thead>
						<tbody>
							${qb_rows || '<tr><td colspan="6" class="text-center text-muted">No transactions</td></tr>'}
						</tbody>
					</table>
				</div>
			</div>
		</div>
	`;

	d.set_primary_action(__("Close"), () => d.hide());
	d.$body.html(html);

	d.$body.on("click", ".search-journal-btn", function () {
		let btn = $(this);
		let txn_date = btn.data("date");
		let acc_name = btn.data("account-name");
		let amount = btn.data("amount");

		frappe.call({
			method: "quickbooks_master_sync.compare_balances.get_quickbooks_journal_details",
			args: {
				company_name: frappe.defaults.get_user_default("company"),
				date: txn_date,
				account_name: acc_name,
				amount: amount,
			},
			freeze: true,
			freeze_message: __("Searching in QuickBooks Journal..."),
			callback: function (r) {
				if (r.message && r.message.length > 0) {
					let rows_html = r.message
						.map(
							(res) => `
						<tr>
							<td>${res.date || ""}</td>
							<td>${res.account || ""}</td>
							<td>${res.type || ""}</td>
							<td>${res.no || ""}</td>
							<td>${res.name || ""}</td>
							<td>${res.memo || ""}</td>
							<td class="text-right">${format_currency(res.debit)}</td>
							<td class="text-right">${format_currency(res.credit)}</td>
						</tr>
					`
						)
						.join("");

					let detail_html = `
						<div style="font-size: 0.85em; overflow-x: auto; max-width: 100%;">
							<table class="table table-bordered table-sm" style="min-width: 800px; margin-bottom: 0;">
								<thead>
									<tr>
										<th style="width: 100px;">${__("Date")}</th>
										<th style="width: 200px;">${__("Account")}</th>
										<th style="width: 100px;">${__("Type")}</th>
										<th style="width: 100px;">${__("No")}</th>
										<th style="width: 150px;">${__("Name")}</th>
										<th style="width: 200px;">${__("Memo")}</th>
										<th class="text-right" style="width: 100px;">${__("Debit")}</th>
										<th class="text-right" style="width: 100px;">${__("Credit")}</th>
									</tr>
								</thead>
								<tbody>
									${rows_html}
								</tbody>
							</table>
						</div>
					`;

					let msg_dialog = frappe.msgprint({
						title: __("Related Journal Entries"),
						message: detail_html,
						wide: true,
						primary_action: {
							label: __("Create Journal Entry"),
							action: function (values) {
								frappe.call({
									method: "quickbooks_master_sync.compare_balances.create_journal_entry_from_qb",
									args: {
										rows: r.message,
										company_name: frappe.defaults.get_user_default("company"),
									},
									freeze: true,
									callback: function (r2) {
										if (r2.message) {
											frappe.show_alert({
												message: __(
													"Journal Entry {0} created successfully",
													[r2.message]
												),
												indicator: "green",
											});
											msg_dialog.hide();
											d.hide();
											if (refresh_callback) refresh_callback();
										}
									},
								});
							},
						},
					});
				} else {
					frappe.msgprint(
						__(
							"No related journal entry found in QuickBooks report for this date and amount."
						)
					);
				}
			},
		});
	});

	d.show();
}

function render_datatable(container_id, data, columns, row_formatter, table_class) {
	console.log("Rendering table:", container_id, "with columns:", columns);
	let container = $(`#${container_id}`);

	if (!container.length) {
		console.error("Container not found:", container_id);
		return;
	}

	container.empty();

	if (data.length === 0) {
		container.html(`<div class="alert alert-info">${__("No records found")}</div>`);
		return;
	}

	// Transform data
	let table_data = data.map(row_formatter);

	// Build table HTML
	let table_html = '<table class="table table-bordered table-hover table-striped">';

	// Header
	table_html += '<thead class="thead-light"><tr>';
	columns.forEach((col) => {
		let align_class = col.fieldtype === "Currency" ? "text-right" : "text-left";
		table_html += `<th class="${align_class}">${col.label}</th>`;
	});
	table_html += "</tr></thead>";

	// Body
	table_html += "<tbody>";
	table_data.forEach((row, row_idx) => {
		let row_class = "";
		if (row_formatter) {
			// Recalculate if needed, or use formatted row
			if (container_id === "date-wise-table") {
				row_class = row._matched
					? "table-success text-success"
					: "table-danger text-danger";
			}
		}

		table_html += `<tr class="${row_class}">`;
		columns.forEach((col, col_idx) => {
			let value = row[col.fieldname];
			let align_class = col.fieldtype === "Currency" ? "text-right" : "text-left";
			let cell_class = "";

			// Format currency values
			if (col.fieldtype === "Currency") {
				// Convert to number - handle various input types
				let num_value;
				if (value === null || value === undefined || value === "") {
					num_value = 0;
				} else if (typeof value === "string") {
					num_value = parseFloat(value);
					if (isNaN(num_value)) {
						num_value = 0;
					}
				} else if (typeof value === "number") {
					num_value = isNaN(value) ? 0 : value;
				} else {
					num_value = 0;
				}

				// Format the currency value
				try {
					value = frappe.format(num_value, { fieldtype: "Currency", precision: 2 });
				} catch (e) {
					// Fallback formatting if frappe.format fails
					value = num_value.toLocaleString("en-US", {
						minimumFractionDigits: 2,
						maximumFractionDigits: 2,
					});
				}

				// Color difference column
				if (col.fieldname === "difference") {
					if (row._matched) {
						cell_class = "text-success font-weight-bold";
					} else {
						cell_class = "text-danger font-weight-bold";
					}
				}
			} else {
				// For non-currency fields, use empty string if null/undefined
				if (value === null || value === undefined || value === "NaN") {
					value = "";
				}
			}

			table_html += `<td class="${align_class} ${cell_class}">${value}</td>`;
		});
		table_html += "</tr>";
	});
	table_html += "</tbody>";

	table_html += "</table>";

	container.html(table_html);
}

function format_currency(value) {
	// Simple currency formatting to avoid recursion issues
	if (value === null || value === undefined || isNaN(value)) {
		return "0.00";
	}
	let num = parseFloat(value);
	return num.toLocaleString("en-US", {
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	});
}
