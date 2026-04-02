// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.provide("frappe.ui.form");
frappe.provide("Quickbooks Settings");

frappe.ui.form.on("Quickbooks Settings", {
	setup: function (frm) {
		// Style all sync buttons
		let sync_buttons = [
			"customer_sync",
			"account_sync",
			"supplier_sync",
			"employee_sync",
			"product_sync",
			"company_sync",
			"payment_method_sync",
		];

		sync_buttons.forEach(function (button_name) {
			if (frm.fields_dict[button_name]) {
				$(frm.fields_dict[button_name].wrapper)
					.find("button")
					.removeClass("btn-default")
					.addClass("btn-info btn-xs");
			}
		});
	},

	refresh: function (frm) {
		var me = this;
		var quickbooks_authentication_url = "";

		// Add "Sync Data to Quickbooks" button at the top
		if (!frm.doc.__islocal && frm.doc.enable_quickbooks_online === 1) {
			frm.add_custom_button(
				__("Sync Data to Quickbooks"),
				function () {
					frm.trigger("connect_to_qb");
				},
				__("Actions")
			);
		}

		// Add "Compare Balances" button
		if (!frm.doc.__islocal && frm.doc.enable_quickbooks_online === 1) {
			frm.add_custom_button(
				__("Compare Balances"),
				function () {
					frm.trigger("balance_comparison");
				},
				__("Actions")
			);
		}

		// Add "Delete Company Data" button
		if (!frm.doc.__islocal) {
			frm.add_custom_button(
				__("Delete Company Data"),
				function () {
					show_delete_company_dialog(frm);
				},
				__("Utilities")
			);
		}

		// Add "Reconnect to QuickBooks" button if already connected
		if (!frm.doc.__islocal && frm.doc.realm_id && frm.doc.realm_id != "") {
			frm.add_custom_button(
				__("Reconnect to QuickBooks"),
				function () {
					reconnect_to_quickbooks(frm);
				},
				__("Connection")
			);
		}

		// Set query filter for warehouse in company_settings child table
		if (frm.fields_dict["company_settings"]) {
			frm.fields_dict["company_settings"].grid.get_field("warehouse").get_query = function (
				doc,
				cdt,
				cdn
			) {
				let row = locals[cdt][cdn];
				if (row.company) {
					return {
						filters: {
							is_group: 0,
							company: row.company,
						},
					};
				}
				return {
					filters: {
						name: "",
					},
				};
			};
		}
	},

	connect_to_qb: function (frm) {
		if (
			frm.doc.consumer_key != null &&
			frm.doc.consumer_secret != null &&
			frm.doc.consumer_key.trim() != "" &&
			frm.doc.consumer_secret.trim() != ""
		) {
			return frappe.call({
				method: "quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.quickbooks_authentication_popup",
				args: {
					consumer_key: frm.doc.consumer_key,
					consumer_secret: frm.doc.consumer_secret,
				},
				freeze: true,
				freeze_message: __("Please wait.. connecting to Quickbooks ................"),
				callback: function (r) {
					if (r.message) {
						window.open(
							decodeURIComponent(r.message),
							"Quickbooks",
							"width=800, height=600"
						);
					}
				},
			});
		} else {
			let warnings = [];
			if (!frm.doc.company_settings || frm.doc.company_settings.length === 0) {
				warnings.push(__("Company Settings (at least one company must be configured)"));
			} else {
				// Check if any company has required settings
				let has_settings = false;
				for (let row of frm.doc.company_settings || []) {
					if (row.selling_price_list || row.buying_price_list) {
						has_settings = true;
						break;
					}
				}
				if (!has_settings) {
					warnings.push(
						__(
							"Price Lists (configure at least one company with Selling/Buying Price List)"
						)
					);
				}
			}

			let start_sync = function () {
				// Create and show progress dialog
				let progress_dialog = show_sync_progress_dialog();

				// Listen for progress updates from backend
				let progress_listener = frappe.realtime.on(
					"quickbooks_sync_progress",
					function (data) {
						if (data && data.step) {
							update_sync_progress(
								data.step,
								data.status || "running",
								data.message || ""
							);

							// Close dialog when sync completes or fails
							if (
								data.status === "completed" ||
								(data.step &&
									(data.step === "Sync Completed" ||
										data.step.includes("Failed")))
							) {
								setTimeout(
									function () {
										if (progress_dialog) {
											progress_dialog.hide();
										}
										if (progress_listener) {
											frappe.realtime.off(
												"quickbooks_sync_progress",
												progress_listener
											);
										}
										if (
											data.status === "completed" ||
											data.step === "Sync Completed"
										) {
											frappe.show_alert(
												{
													message: __("Sync completed successfully!"),
													indicator: "green",
												},
												5
											);
										} else {
											frappe.msgprint({
												title: __("Sync Error"),
												message:
													data.message ||
													__("Sync failed. Please check the error log."),
												indicator: "red",
											});
										}
									},
									data.status === "completed" ? 1500 : 1000
								);
							}
						}
					}
				);

				// Start the sync with progress tracking
				return frappe.call({
					method: "quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_resources",
					freeze: true,
					freeze_message: __("Starting QuickBooks Sync..."),
					callback: function (r) {
						// Check if job was queued (background job)
						if (r.message && r.message.job_queued === true) {
							// Job is queued and running in background
							// Keep progress dialog open and wait for real-time updates
							update_sync_progress(
								"Sync Queued",
								"running",
								__("Sync job queued and running in background...")
							);
							// Don't unsubscribe or close dialog - let real-time updates handle completion
							return;
						}

						// If job was NOT queued (immediate execution or error)
						// Unsubscribe from progress updates
						if (progress_listener) {
							frappe.realtime.off("quickbooks_sync_progress", progress_listener);
						}

						// Update final status
						if (r.message && r.message.success === false) {
							update_sync_progress(
								"Sync Failed",
								"error",
								r.message.message || __("Sync failed")
							);
							setTimeout(function () {
								if (progress_dialog) {
									progress_dialog.hide();
								}
								frappe.msgprint({
									title: __("Sync Error"),
									message:
										r.message.message ||
										__("Sync failed. Please check the error log."),
									indicator: "red",
								});
							}, 1000);
						} else {
							// Immediate completion (shouldn't happen with background jobs)
							update_sync_progress(
								"Sync Completed",
								"completed",
								__("All data synced successfully")
							);
							setTimeout(function () {
								if (progress_dialog) {
									progress_dialog.hide();
								}
								frappe.show_alert(
									{
										message: __("Sync completed successfully!"),
										indicator: "green",
									},
									5
								);
							}, 1500);
						}
					},
					error: function (r) {
						// Unsubscribe from progress updates
						if (progress_listener) {
							frappe.realtime.off("quickbooks_sync_progress", progress_listener);
						}

						// Close progress dialog on error
						update_sync_progress(
							"Sync Error",
							"error",
							__("An error occurred during sync")
						);
						setTimeout(function () {
							if (progress_dialog) {
								progress_dialog.hide();
							}
							frappe.msgprint({
								title: __("Sync Error"),
								message: __(
									"An error occurred during sync. Please check the error log."
								),
								indicator: "red",
							});
						}, 1000);
					},
				});
			};

			if (warnings.length > 0) {
				frappe.warn(
					__("Missing Configuration"),
					__(
						"The following fields are recommended but not configured: {0}. Sync may use fallback values. Continue?",
						[warnings.join(", ")]
					),
					start_sync,
					__("Continue"),
					true
				);
			} else {
				return start_sync();
			}
		}
	},

	balance_comparison: function (frm) {
		if (!frm.doc.__islocal && frm.doc.enable_quickbooks_online === 1) {
			show_balance_comparison_dialog(frm);
		} else {
			frappe.msgprint(__("Enable QuickBooks Online and save the settings first."));
		}
	},

	customer_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_customers_only",
			__("Customer")
		);
	},

	fixed_asset_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_fixed_assets_only",
			__("Fixed Asset")
		);
	},

	account_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_accounts_only",
			__("Account")
		);
	},

	employee_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_employees_only",
			__("Employee")
		);
	},

	supplier_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_suppliers_only",
			__("Supplier")
		);
	},

	product_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_products_only",
			__("Product")
		);
	},

	sales_invoice_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_sales_invoices_only",
			__("Sales Invoice")
		);
	},

	credit_memo_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_credit_memos_only",
			__("Credit Memo")
		);
	},

	vendor_credit_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_vendor_credits_only",
			__("Vendor Credit")
		);
	},

	purchase_invoice_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_purchase_invoices_only",
			__("Purchase Invoice")
		);
	},

	payment_entry_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_payment_entries_only",
			__("Payment Entry")
		);
	},

	payment_method_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_payment_methods_only",
			__("Payment Method")
		);
	},

	journal_entry_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_journal_entries_only",
			__("Journal Entry")
		);
	},

	deposits_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_deposits_only",
			__("Deposit")
		);
	},

	transfers_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_transfers_only",
			__("Transfer")
		);
	},

	credit_card_payment_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_credit_card_payments_only",
			__("Credit Card Payment")
		);
	},

	refund_receipt_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_refund_receipts_only",
			__("Refund Receipt")
		);
	},

	sales_receipt_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_sales_receipts_only",
			__("Sales Receipt")
		);
	},

	inventory_adjustment_sync: function (frm) {
		return _start_sync_with_progress(
			frm,
			"quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_inventory_adjustments_only",
			__("Inventory Adjustment")
		);
	},
});

// Show sync progress dialog
function show_sync_progress_dialog() {
	let progress_dialog = new frappe.ui.Dialog({
		title: __("QuickBooks Sync in Progress"),
		fields: [
			{
				fieldtype: "HTML",
				options: `
					<div style="
						display: flex;
						align-items: center;
						gap: 14px;
						background: linear-gradient(135deg, rgba(0,139,71,0.07), rgba(47,49,129,0.08));
						border: 1px solid rgba(14,112,89,0.25);
						border-radius: 10px;
						padding: 14px 18px;
						margin-bottom: 16px;
					">
						<img src="/assets/quickbooks_master_sync/img/invento-logo-color.png" alt="Invento" style="height: 32px; width: auto; flex-shrink: 0;">
						<div style="flex: 1; border-left: 2px solid rgba(14,112,89,0.3); padding-left: 14px;">
							<div style="font-weight: 600; font-size: 0.92rem; color: #0E7059; margin-bottom: 3px;">
								<i class="fa fa-lock" style="margin-right: 5px; font-size: 0.85rem;"></i>Master Data Sync Only
							</div>
							<div style="font-size: 0.83rem; color: #555; line-height: 1.5;">
								Currently syncing <strong>master data only</strong> (Accounts, Customers, Vendors, etc.).<br>
								To sync <strong>transactional data</strong>, please contact
								<a href="mailto:munim@invento.com.bd" style="color: #008B47; font-weight: 600;">munim@invento.com.bd</a>
							</div>
						</div>
					</div>
					<div id="sync_progress_container" style="min-height: 280px; max-height: 460px; overflow-y: auto;">
						<div class="sync-progress-item" style="padding: 10px; border-bottom: 1px solid #ddd;">
							<div style="display: flex; align-items: center;">
								<div class="sync-status-icon" style="margin-right: 10px; width: 20px; text-align: center;">
									<i class="fa fa-spinner fa-spin" style="color: #5e64ff;"></i>
								</div>
								<div style="flex: 1;">
									<div style="font-weight: 500;">Initializing sync...</div>
									<div style="font-size: 11px; color: #8d99a6; margin-top: 2px;">Preparing to sync data from QuickBooks</div>
								</div>
							</div>
						</div>
					</div>
				`,
			},
		],
		primary_action_label: null,
		secondary_action_label: null,
	});

	// Make dialog non-dismissible during sync
	progress_dialog.$wrapper.find(".modal-header .close").hide();

	progress_dialog.show();

	// Store reference for updating progress
	window.sync_progress_dialog = progress_dialog;

	return progress_dialog;
}

// Update sync progress (called from backend via polling or websocket)
function update_sync_progress(step_name, status, message) {
	if (!window.sync_progress_dialog) return;

	let container = window.sync_progress_dialog.$wrapper.find("#sync_progress_container");
	let step_id = "step_" + step_name.replace(/\s+/g, "_").toLowerCase();

	// Check if step already exists
	let existing_step = container.find("#" + step_id);

	if (existing_step.length === 0) {
		// Add new step
		let icon_class = "fa-spinner fa-spin";
		let icon_color = "#5e64ff";
		if (status === "completed") {
			icon_class = "fa-check-circle";
			icon_color = "#28a745";
		} else if (status === "error") {
			icon_class = "fa-times-circle";
			icon_color = "#dc3545";
		}

		let step_html = `
			<div class="sync-progress-item" id="${step_id}" style="padding: 10px; border-bottom: 1px solid #ddd;">
				<div style="display: flex; align-items: center;">
					<div class="sync-status-icon" style="margin-right: 10px; width: 20px; text-align: center;">
						<i class="fa ${icon_class}" style="color: ${icon_color};"></i>
					</div>
					<div style="flex: 1;">
						<div style="font-weight: 500;">${frappe.utils.escape_html(step_name)}</div>
						${
							message
								? `<div style="font-size: 11px; color: #8d99a6; margin-top: 2px;">${frappe.utils.escape_html(
										message
								  )}</div>`
								: ""
						}
					</div>
				</div>
			</div>
		`;
		container.append(step_html);
	} else {
		// Update existing step
		let icon = existing_step.find(".sync-status-icon i");
		if (status === "completed") {
			icon.removeClass("fa-spinner fa-spin")
				.addClass("fa-check-circle")
				.css("color", "#28a745");
		} else if (status === "error") {
			icon.removeClass("fa-spinner fa-spin")
				.addClass("fa-times-circle")
				.css("color", "#dc3545");
		}

		if (message) {
			let message_div = existing_step.find('div[style*="font-size: 11px"]');
			if (message_div.length) {
				message_div.text(message);
			} else {
				existing_step
					.find('div[style*="font-weight: 500"]')
					.after(
						`<div style="font-size: 11px; color: #8d99a6; margin-top: 2px;">${frappe.utils.escape_html(
							message
						)}</div>`
					);
			}
		}
	}

	// Scroll to bottom to show latest progress
	container.scrollTop(container[0].scrollHeight);
}

// Generic function to start individual sync with progress tracking
function _start_sync_with_progress(frm, method, resource_name) {
	if (!frm.doc.__islocal && frm.doc.enable_quickbooks_online === 1) {
		// Create and show progress dialog
		let progress_dialog = show_sync_progress_dialog();

		// Listen for progress updates from backend
		let progress_listener = frappe.realtime.on("quickbooks_sync_progress", function (data) {
			if (data && data.step) {
				update_sync_progress(data.step, data.status || "running", data.message || "");

				// Close dialog when sync completes or fails
				if (
					data.status === "completed" ||
					(data.step &&
						(data.step.includes("Completed") || data.step.includes("Failed")))
				) {
					setTimeout(
						function () {
							if (progress_dialog) {
								progress_dialog.hide();
							}
							if (progress_listener) {
								frappe.realtime.off("quickbooks_sync_progress", progress_listener);
							}
							if (data.status === "completed" || data.step.includes("Completed")) {
								frappe.show_alert(
									{
										message: __("{0} sync completed successfully!", [
											resource_name,
										]),
										indicator: "green",
									},
									5
								);
							} else {
								frappe.msgprint({
									title: __("Sync Error"),
									message:
										data.message ||
										__("Sync failed. Please check the error log."),
									indicator: "red",
								});
							}
						},
						data.status === "completed" ? 1500 : 1000
					);
				}
			}
		});

		// Start the sync with progress tracking (runs synchronously)
		return frappe.call({
			method: method,
			freeze: false, // Don't freeze - we want to see progress updates
			callback: function (r) {
				// Unsubscribe from progress updates
				if (progress_listener) {
					frappe.realtime.off("quickbooks_sync_progress", progress_listener);
				}

				// Update final status
				if (r.message && r.message.success === false) {
					update_sync_progress(
						"Sync Failed",
						"error",
						r.message.message || __("Sync failed")
					);
					setTimeout(function () {
						if (progress_dialog) {
							progress_dialog.hide();
						}
						frappe.msgprint({
							title: __("Sync Error"),
							message:
								r.message.message ||
								__("Sync failed. Please check the error log."),
							indicator: "red",
						});
					}, 1000);
				} else {
					// Sync completed - dialog will be closed by progress listener
					// But if it's not closed yet, close it here
					setTimeout(function () {
						if (progress_dialog && progress_dialog.$wrapper.is(":visible")) {
							progress_dialog.hide();
						}
						if (r.message && r.message.success !== false) {
							frappe.show_alert(
								{
									message: __("{0} sync completed successfully!", [
										resource_name,
									]),
									indicator: "green",
								},
								5
							);
						}
					}, 1500);
				}
			},
			error: function (r) {
				// Unsubscribe from progress updates
				if (progress_listener) {
					frappe.realtime.off("quickbooks_sync_progress", progress_listener);
				}

				// Close progress dialog on error
				update_sync_progress("Sync Error", "error", __("An error occurred during sync"));
				setTimeout(function () {
					if (progress_dialog) {
						progress_dialog.hide();
					}
					frappe.msgprint({
						title: __("Sync Error"),
						message: __("An error occurred during sync. Please check the error log."),
						indicator: "red",
					});
				}, 1000);
			},
		});
	} else {
		frappe.msgprint(__("Enable QuickBooks Online and save the settings first."));
	}
}

function show_balance_comparison_dialog(frm) {
	let d = new frappe.ui.Dialog({
		title: __("Compare QuickBooks & ERPNext Balances"),
		fields: [
			{
				fieldtype: "Link",
				label: __("Company"),
				fieldname: "company",
				options: "Company",
				reqd: 1,
				default: frappe.defaults.get_user_default("company"),
				get_query: function () {
					return {
						filters: {
							disabled: 0,
						},
					};
				},
			},
			{
				fieldtype: "Date",
				label: __("As of Date"),
				fieldname: "as_of_date",
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{
				fieldtype: "Float",
				label: __("Tolerance"),
				fieldname: "tolerance",
				default: 0.01,
				description: __("Maximum allowed difference between balances (default: 0.01)"),
			},
			{
				fieldtype: "Section Break",
			},
			{
				fieldtype: "Button",
				label: __("Compare Balances"),
				fieldname: "compare_btn",
				click: function () {
					let values = d.get_values();
					if (!values.company) {
						frappe.msgprint(__("Please select a company"));
						return;
					}
					run_balance_comparison(
						values.company,
						values.as_of_date,
						values.tolerance || 0.01,
						d
					);
				},
			},
		],
		primary_action_label: null,
	});
	d.show();
}

function run_balance_comparison(company, as_of_date, tolerance, dialog) {
	frappe.call({
		method: "quickbooks_master_sync.quickbooks_master_sync.api.compare_quickbooks_erpnext_balances",
		args: {
			company_name: company,
			as_of_date: as_of_date,
			tolerance: tolerance,
		},
		freeze: true,
		freeze_message: __("Comparing balances... Please wait..."),
		callback: function (r) {
			if (r.message && r.message.success && r.message.result) {
				let result = r.message.result;
				// Open the balance comparison page
				frappe.set_route("page", "quickbooks-balance-comparison", {
					company: company,
					as_of_date: as_of_date,
					tolerance: tolerance,
					result: JSON.stringify(result),
				});
				if (dialog) {
					dialog.hide();
				}
			} else {
				frappe.msgprint({
					title: __("Error"),
					message:
						r.message && r.message.error
							? r.message.error
							: __("Failed to compare balances. Please check the error log."),
					indicator: "red",
				});
			}
		},
		error: function (r) {
			frappe.msgprint({
				title: __("Error"),
				message: __(
					"An error occurred while comparing balances. Please check the error log for details."
				),
				indicator: "red",
			});
		},
	});
}

// Reconnect to QuickBooks - Clear current connection and reconnect
function reconnect_to_quickbooks(frm) {
	const confirm_msg = __(
		"Are you sure you want to disconnect from the current QuickBooks company and reconnect?<br><br><strong>Current Connection:</strong><br>Realm ID: {0}<br><br>This will clear your current connection. You will need to authorize again with QuickBooks to connect to a different company.",
		[frm.doc.realm_id || "N/A"]
	);

	frappe.confirm(confirm_msg, function () {
		// User confirmed - clear connection
		frappe.call({
			method: "quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.clear_quickbooks_connection",
			freeze: true,
			freeze_message: __("Clearing current connection..."),
			callback: function (r) {
				if (r.message && r.message.status === "success") {
					frappe.show_alert(
						{
							message: __("Connection cleared successfully. Please reconnect now."),
							indicator: "green",
						},
						5
					);

					// Reload the form to show cleared fields
					frm.reload_doc();

					// After a short delay, trigger the connection process
					setTimeout(function () {
						if (
							frm.doc.consumer_key != null &&
							frm.doc.consumer_secret != null &&
							frm.doc.consumer_key.trim() != "" &&
							frm.doc.consumer_secret.trim() != ""
						) {
							frappe.confirm(
								__(
									"Connection cleared. Do you want to connect to QuickBooks now?<br><br>You will be able to select a different company."
								),
								function () {
									// Trigger the connection with force_company_selection=true
									// This forces QuickBooks to show the company selection screen
									frappe.call({
										method: "quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.quickbooks_authentication_popup",
										args: {
											consumer_key: frm.doc.consumer_key,
											consumer_secret: frm.doc.consumer_secret,
											force_company_selection: true,
										},
										freeze: true,
										freeze_message: __(
											"Please wait.. connecting to Quickbooks ................"
										),
										callback: function (r) {
											if (r.message) {
												window.open(
													decodeURIComponent(r.message),
													"Quickbooks",
													"width=800, height=600"
												);
											}
										},
									});
								}
							);
						} else {
							frappe.msgprint({
								title: __("Ready to Connect"),
								message: __(
									"Connection cleared. Please enter your Consumer Key and Consumer Secret, then click 'Connect to QuickBooks' to reconnect."
								),
								indicator: "blue",
							});
						}
					}, 1000);
				} else {
					frappe.msgprint({
						title: __("Error"),
						message: __("Failed to clear connection. Please try again."),
						indicator: "red",
					});
				}
			},
		});
	});
}

// Delete Company Data Dialog
function show_delete_company_dialog(frm) {
	// Get session default company
	let default_company = frappe.defaults.get_user_default("company");

	// First, get list of companies with QuickBooks data
	frappe.call({
		method: "quickbooks_master_sync.quickbooks_master_sync.delete_company_data.get_companies_with_quickbooks_data",
		callback: function (r) {
			let d;
			// Function to check and display warning for default company
			let check_default_company = function () {
				let selected_company = d.get_value("company");
				let warning_field = d.fields_dict.default_company_warning;

				if (warning_field && warning_field.wrapper) {
					if (
						default_company &&
						selected_company &&
						selected_company === default_company
					) {
						// Show warning message
						$(warning_field.wrapper).html(`<div class="alert alert-warning">
								<strong>⚠️ Default Company Warning:</strong> The selected company "<strong>${frappe.utils.escape_html(
									selected_company
								)}</strong>" is your session default company.
								Deleting this company's data may affect your current session and other operations.
							</div>`);
					} else {
						// Clear warning message
						$(warning_field.wrapper).html("");
					}
				}
			};

			// Function to validate company matches session default
			let validate_company_match = function () {
				let selected_company = d.get_value("company");
				if (default_company && selected_company && selected_company !== default_company) {
					frappe.msgprint({
						title: __("Company Mismatch"),
						message: __(
							'The selected company "<strong>{0}</strong>" does not match your session default company "<strong>{1}</strong>". Please select the correct company or change your session default company.',
							[
								frappe.utils.escape_html(selected_company),
								frappe.utils.escape_html(default_company),
							]
						),
						indicator: "red",
					});
					return false;
				}
				return true;
			};

			if (r.message && r.message.length > 0) {
				let companies = r.message;

				// Create dialog
				d = new frappe.ui.Dialog({
					title: __("Delete QuickBooks Company Data"),
					fields: [
						{
							fieldtype: "HTML",
							options: `<div class="alert alert-danger">
								<strong>⚠️ Warning:</strong> This action will delete all QuickBooks synced data for the selected company.
								This includes:<br><br>
								<ul>
									<li>All transactions (Sales Invoices, Purchase Invoices, Payment Entries, Journal Entries, etc.)</li>
									<li>Master data (Customers, Suppliers, Items, Employees, Accounts)</li>
									<li>Related addresses and contacts</li>
									<li>QuickBooks sync logs</li>
								</ul>
								<strong>This action CANNOT be undone!</strong>
							</div>`,
						},
						{
							fieldtype: "Select",
							label: __("Select Company"),
							fieldname: "company",
							options: companies,
							reqd: 1,
							description: __(
								"Select the company whose QuickBooks data you want to delete"
							),
						},
						{
							fieldtype: "HTML",
							fieldname: "default_company_warning",
							options: "",
						},
						{
							fieldtype: "Check",
							label: __("Delete Company Document"),
							fieldname: "delete_company",
							default: 0,
							description: __(
								"If checked, the Company document itself will also be deleted (along with all data)"
							),
						},
						{
							fieldtype: "Check",
							label: __("Clear Stock from Warehouses"),
							fieldname: "clear_stock",
							default: 0,
							description: __(
								"If checked, creates a Stock Reconciliation to zero out all stock before deletion"
							),
						},
						{
							fieldtype: "Section Break",
						},
						{
							fieldtype: "HTML",
							options: `<div class="alert alert-info">
								<strong>💡 Tip:</strong> Use "Dry Run" to preview what will be deleted without actually deleting anything.
							</div>`,
						},
						{
							fieldtype: "Section Break",
						},
						{
							fieldtype: "Button",
							label: __("🔍 Dry Run (Preview)"),
							fieldname: "dry_run_btn",
							click: function () {
								let values = d.get_values();
								if (!values.company) {
									frappe.msgprint(__("Please select a company"));
									return;
								}
								if (!validate_company_match()) {
									return;
								}
								run_delete_company_data(
									values.company,
									true,
									values.delete_company || 0,
									values.clear_stock || 0,
									d
								);
							},
						},
						{
							fieldtype: "Column Break",
						},
						{
							fieldtype: "Button",
							label: __("🗑️ Delete Permanently"),
							fieldname: "delete_btn",
							click: function () {
								let values = d.get_values();
								if (!values.company) {
									frappe.msgprint(__("Please select a company"));
									return;
								}
								if (!validate_company_match()) {
									return;
								}

								// Double confirmation for actual deletion
								frappe.confirm(
									__(
										'Are you absolutely sure you want to permanently delete all QuickBooks data for company "{0}"? This action CANNOT be undone!',
										[values.company]
									),
									function () {
										run_delete_company_data(
											values.company,
											false,
											values.delete_company || 0,
											values.clear_stock || 0,
											d
										);
									}
								);
							},
						},
					],
					primary_action_label: null, // Disable default primary action
				});

				d.show();

				// Add change event handler to company field after dialog is shown
				setTimeout(function () {
					if (d.fields_dict.company && d.fields_dict.company.wrapper) {
						$(d.fields_dict.company.wrapper)
							.find("select")
							.on("change", function () {
								check_default_company();
							});
					}
					// Check on initial load if a company is pre-selected
					check_default_company();
				}, 100);
			} else {
				frappe.msgprint(__("No companies with QuickBooks synced data found."));
			}
		},
	});
}

function run_delete_company_data(company, dry_run, delete_company, clear_stock, dialog) {
	let action_text = dry_run ? __("Preview") : __("Delete");

	frappe.call({
		method: "quickbooks_master_sync.quickbooks_master_sync.delete_company_data.delete_company_data",
		args: {
			company_name: company,
			dry_run: dry_run ? 1 : 0,
			delete_company: delete_company,
			clear_stock: clear_stock,
		},
		freeze: true,
		freeze_message: dry_run ? __("Analyzing data...") : __("Deleting data... Please wait..."),
		callback: function (r) {
			if (r.message) {
				let stats = r.message;
				let total = stats.total_records || 0;

				// Build results HTML
				let results_html = `<div style="max-height: 400px; overflow-y: auto;">`;

				if (dry_run) {
					results_html += `<div class="alert alert-info">
						<strong>📊 Dry Run Results</strong><br>
						The following records would be deleted for company <strong>${stats.company}</strong>:
					</div>`;
				} else {
					results_html += `<div class="alert alert-success">
						<strong>✅ Deletion Complete</strong><br>
						Successfully deleted <strong>${total}</strong> records for company <strong>${stats.company}</strong>
					</div>`;
				}

				// Show breakdown by doctype
				results_html += `<table class="table table-bordered table-sm">
					<thead>
						<tr>
							<th>Document Type</th>
							<th class="text-right">Records ${dry_run ? __("to Delete") : __("Deleted")}</th>
						</tr>
					</thead>
					<tbody>`;

				let deleted = stats.deleted || {};
				let has_records = false;

				for (let doctype in deleted) {
					if (deleted[doctype] > 0) {
						has_records = true;
						results_html += `<tr>
							<td>${doctype}</td>
							<td class="text-right"><strong>${deleted[doctype]}</strong></td>
						</tr>`;
					}
				}

				if (!has_records) {
					results_html += `<tr>
						<td colspan="2" class="text-center text-muted">No records found</td>
					</tr>`;
				}

				results_html += `</tbody>
					<tfoot>
						<tr class="font-weight-bold">
							<td>Total</td>
							<td class="text-right">${total}</td>
						</tr>
					</tfoot>
				</table>`;

				// Show warnings if any
				if (stats.warnings && stats.warnings.length > 0) {
					results_html += `<div class="alert alert-info">
						<strong>ℹ️ Warnings:</strong><br>
						${stats.warnings.join("<br>")}
					</div>`;
				}

				// Show errors if any
				if (stats.errors && stats.errors.length > 0) {
					results_html += `<div class="alert alert-warning">
						<strong>⚠️ Errors encountered:</strong><br>
						${stats.errors.join("<br>")}
					</div>`;
				}

				results_html += `</div>`;

				// Show results in a new dialog
				let result_dialog = new frappe.ui.Dialog({
					title: dry_run ? __("Dry Run Results") : __("Deletion Complete"),
					fields: [
						{
							fieldtype: "HTML",
							options: results_html,
						},
					],
					primary_action_label: __("Close"),
					primary_action: function () {
						result_dialog.hide();
						if (!dry_run && dialog) {
							dialog.hide();
						}
					},
				});

				result_dialog.show();

				// Also show a summary message
				if (!dry_run) {
					frappe.show_alert(
						{
							message: __("Successfully deleted {0} records", [total]),
							indicator: "green",
						},
						5
					);
				}
			}
		},
		error: function (r) {
			frappe.msgprint({
				title: __("Error"),
				message: __(
					"An error occurred while processing the request. Please check the error log for details."
				),
				indicator: "red",
			});
		},
	});
}
