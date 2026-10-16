"use strict";
/* Sari-Sari Store - phone app. Plain JavaScript, no libraries.
   Every piece of data is put on screen with textContent (never innerHTML),
   so a product or customer name can never run as code. */

const CLIENT_VERSION = document.documentElement.dataset.version;
const S = { me: null, tab: "dashboard", reg: null, products: [] };

// ---------------------------------------------------------------- helpers
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) el.append(c);
  return el;
}
const $ = (sel) => document.querySelector(sel);
const can = (action) => !!S.me && S.me.permissions.includes(action);
const peso = (n) => "PHP " + Number(n).toLocaleString("en-PH", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2
});
const fmtDate = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("en-PH", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};

let toastTimer;
function toast(message, isError) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ""; }, 3800);
}

// ---------------------------------------------------------------- talking to the server
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "sari-app" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  const serverVersion = res.headers.get("X-App-Version");
  if (serverVersion && serverVersion !== CLIENT_VERSION) showUpdateBanner();
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Something went wrong (${res.status}).`);
    err.status = res.status;
    err.code = data && data.code;
    if (res.status === 401 && !opts.noRedirect && S.me) {
      S.me = null;
      closeSheet();
      render();
      toast("Please sign in again.", true);
    }
    if (err.code === "must_change" && S.me) { S.me.must_change = true; closeSheet(); render(); }
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------- updates (no reinstalling)
function showUpdateBanner() {
  const banner = $("#update-banner");
  if (!banner.hidden) return;
  banner.replaceChildren(
    h("span", {}, "A new version is available."),
    h("button", { class: "btn btn-primary", onclick: applyUpdate }, "Update"));
  banner.hidden = false;
}
function applyUpdate() {
  if (S.reg && S.reg.waiting) S.reg.waiting.postMessage({ type: "SKIP_WAITING" }); // page reloads itself
  else location.reload();
}
function watchRegistration(reg) {
  if (S.reg === reg) return;
  S.reg = reg;
  const announce = () => { if (reg.waiting && navigator.serviceWorker.controller) showUpdateBanner(); };
  announce();
  reg.addEventListener("updatefound", () => {
    const worker = reg.installing;
    if (worker) worker.addEventListener("statechange", () => { if (worker.state === "installed") announce(); });
  });
}
// Connects to the service worker. Safe to call again later (for example after the app was opened offline).
async function connectServiceWorker() {
  if (!("serviceWorker" in navigator)) return false;
  try { watchRegistration(await navigator.serviceWorker.register("/sw.js", { scope: "/" })); return true; }
  catch (e) { return false; }
}
async function setupUpdates() {
  if (!("serviceWorker" in navigator)) return;
  const hadController = !!navigator.serviceWorker.controller;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController || reloading) return; // first install: nothing to reload
    reloading = true;
    location.reload();
  });
  await connectServiceWorker();
  const check = () => { if (S.reg) S.reg.update().catch(() => {}); else connectServiceWorker(); };
  setInterval(check, 60 * 60 * 1000);
  window.addEventListener("online", check);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") check(); });
}
async function checkForUpdates() {
  toast("Checking for updates\u2026");
  if (!S.reg) await connectServiceWorker();
  if (!S.reg) { location.reload(); return; }
  try { await S.reg.update(); } catch (e) { toast("Can't check right now. Are you online?", true); return; }
  setTimeout(() => {
    if (S.reg.waiting || S.reg.installing) showUpdateBanner();
    else toast(`You're up to date (version ${CLIENT_VERSION}).`);
  }, 1500);
}

// ---------------------------------------------------------------- bottom sheet (pop-up panel)
let sheetOpen = false;
function openSheet(title, ...content) {
  const existing = $(".overlay");
  if (existing) existing.remove(); else history.pushState({ sheet: 1 }, "");
  sheetOpen = true;
  const overlay = h("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) closeSheet(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "sheet-head" },
        h("h2", {}, title),
        h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => closeSheet() }, "\u2715")),
      ...content));
  document.body.append(overlay);
  const first = overlay.querySelector("input:not([type=checkbox]), select");
  if (first) first.focus();
  return overlay;
}
function closeSheet(fromPopState) {
  const overlay = $(".overlay");
  if (overlay) overlay.remove();
  if (sheetOpen) {
    sheetOpen = false;
    if (!fromPopState && history.state && history.state.sheet) history.back();
  }
}
window.addEventListener("popstate", () => { if (sheetOpen) closeSheet(true); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

// ---------------------------------------------------------------- form builder
function buildForm(fields, submitLabel, onSubmit, opts = {}) {
  const error = h("p", { class: "form-error", role: "alert" });
  const inputs = {};
  const form = h("form", { class: "form", novalidate: true });
  for (const f of fields) {
    let input;
    if (f.type === "select") {
      input = h("select", { class: "input", name: f.name, id: "f-" + f.name },
        f.options.map((o) => h("option", { value: o.value }, o.label)));
    } else {
      input = h("input", { class: "input", name: f.name, id: "f-" + f.name, type: f.type || "text",
        inputmode: f.inputmode, autocomplete: f.autocomplete || "off", placeholder: f.placeholder,
        maxlength: f.maxlength, autocapitalize: f.autocapitalize || "off" });
    }
    if (f.value != null) input.value = f.value;
    inputs[f.name] = input;
    form.append(h("div", { class: "field" },
      h("label", { for: "f-" + f.name }, f.label), input, f.help && h("p", { class: "help" }, f.help)));
  }
  const button = h("button", { class: "btn btn-primary btn-block", type: "submit" }, submitLabel);
  form.append(error, button);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    const values = {};
    for (const [k, el] of Object.entries(inputs)) values[k] = el.value;
    button.disabled = true;
    button.textContent = "Please wait\u2026";
    try { await onSubmit(values); }
    catch (ex) { error.textContent = ex.message; }
    finally { button.disabled = false; button.textContent = submitLabel; }
  });
  form.inputs = inputs;
  return form;
}
// two-tap safety for dangerous buttons
function confirmTap(label, confirmLabel, action) {
  const btn = h("button", { class: "btn btn-danger btn-block", type: "button" }, label);
  let armed = false;
  btn.addEventListener("click", () => {
    if (!armed) { armed = true; btn.textContent = confirmLabel; setTimeout(() => { armed = false; btn.textContent = label; }, 4000); }
    else action();
  });
  return btn;
}
const kv = (pairs) => h("dl", { class: "kv" }, pairs.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
const empty = (text) => h("p", { class: "empty" }, text);

// ---------------------------------------------------------------- screens: login and password
function loginView() {
  const form = buildForm([
    { name: "username", label: "Username", autocomplete: "username", autocapitalize: "none" },
    { name: "password", label: "Password", type: "password", autocomplete: "current-password" },
  ], "Sign in", async (v) => {
    S.me = await api("/api/login", { method: "POST", body: v, noRedirect: true });
    render();
  });
  const show = h("input", { type: "checkbox", id: "showpw", onchange: (e) => { form.inputs.password.type = e.target.checked ? "text" : "password"; } });
  form.insertBefore(h("label", { class: "check", for: "showpw" }, show, "Show password"), form.querySelector(".form-error"));
  return h("div", { class: "auth" },
    h("img", { src: "/static/icons/icon-192.png", alt: "" }),
    h("h1", {}, "Sari-Sari Store"),
    h("p", { class: "sub" }, "Sign in to manage your store"),
    form);
}

function changePasswordForm(forced) {
  const fields = [
    { name: "current", label: "Current password", type: "password", autocomplete: "current-password" },
    { name: "next", label: "New password", type: "password", autocomplete: "new-password", help: "At least 6 characters." },
    { name: "again", label: "Type the new password again", type: "password", autocomplete: "new-password" },
  ];
  return buildForm(fields, "Save new password", async (v) => {
    if (v.next !== v.again) throw new Error("The new passwords don't match.");
    S.me = await api("/api/change-password", { method: "POST", body: { current_password: v.current, new_password: v.next } });
    closeSheet();
    toast("Password changed.");
    if (forced) render();
  });
}
function forcedPasswordView() {
  return h("div", { class: "auth" },
    h("h1", {}, "Choose a new password"),
    h("p", { class: "sub" }, `Hi ${S.me.username}! Before you continue, set your own password.`),
    changePasswordForm(true),
    h("button", { class: "btn btn-ghost btn-block spaced", onclick: logout }, "Log out"));
}
async function logout() {
  try { await api("/api/logout", { method: "POST", noRedirect: true }); } catch (e) { /* ignore */ }
  S.me = null; closeSheet(); render();
}

// ---------------------------------------------------------------- shell (header, tabs)
function tabsForRole() {
  const tabs = [];
  if (can("reports")) tabs.push({ id: "dashboard", label: "Home", ico: "⌂" });
  tabs.push({ id: "products", label: "Products", ico: "▣" });
  if (can("cash_sale")) tabs.push({ id: "sales", label: "Sell", ico: "▤" });
  if (can("credit")) tabs.push({ id: "credit", label: "Credit", ico: "◫" });
  if (can("reports")) tabs.push({ id: "reports", label: "Reports", ico: "▥" });
  tabs.push({ id: "more", label: "More", ico: "⚙" });
  return tabs;
}
function shell() {
  const tabs = tabsForRole();
  if (!tabs.some((t) => t.id === S.tab)) S.tab = tabs[0].id;
  const nav = h("nav", { class: "tabs", "aria-label": "Main" }, tabs.map((t) =>
    h("button", { type: "button", "data-tab": t.id, class: t.id === S.tab ? "active" : "", onclick: () => showTab(t.id) },
      h("span", { class: "ico", "aria-hidden": "true" }, t.ico), h("span", {}, t.label))));
  return h("div", { class: "shell" },
    h("header", { class: "topbar" },
      h("div", { class: "brand-mark", "aria-hidden": "true" }, "S"),
      h("div", { class: "brand-copy" }, h("h1", {}, "Sari-Sari"), h("span", {}, "STORE MANAGER · v1.3")),
      h("span", { class: "pill-offline" }, "Offline"),
      h("span", { class: "chip" }, `${S.me.username} · ${S.me.role}`),
      h("button", { class: "icon-btn", "aria-label": "Refresh", onclick: () => showTab(S.tab) }, "↻")),
    h("main", { id: "view" }), nav);
}
function pageHeading(kicker, title, subtitle) {
  return h("div", { class: "page-heading" }, h("span", { class: "eyebrow" }, kicker),
    h("h2", {}, title), subtitle && h("p", { class: "muted" }, subtitle));
}
function quickAction(label, tab, icon) {
  return h("button", { class: "quick-action", onclick: () => showTab(tab) },
    h("span", { class: "quick-icon" }, icon), h("span", {}, label), h("span", { class: "quick-arrow" }, "↗"));
}
async function dashboardView() {
  const [r, products] = await Promise.all([api("/api/reports"), api("/api/products")]);
  S.products = products;
  const low = products.filter(p => p.status === "LOW STOCK");
  const tile = (label, value, ico, cls = "") => h("div", { class: "card metric " + cls },
    h("div", { class: "metric-top" }, h("span", {}, label), h("span", { class: "metric-icon" }, ico)),
    h("div", { class: "metric-value" }, value));
  return h("div", { class: "dashboard" },
    pageHeading("BUSINESS OVERVIEW", "Good day, " + S.me.username + " 👋", "Your store at a glance"),
    h("div", { class: "hero-metric" }, h("span", {}, "TODAY'S CASH SALES"),
      h("strong", {}, peso(r.cash_sales_today)), h("small", {}, `${r.today_transactions} transactions today`)),
    h("div", { class: "metrics-grid" },
      tile("Known gross profit", peso(r.today_profit_known), "↗", "profit"),
      tile("Outstanding credit", peso(r.outstanding), "◫", "credit-metric"),
      tile("Products", String(products.length), "▣"),
      tile("Low stock", String(low.length), "!", low.length ? "attention" : "")),
    !r.today_profit_complete && h("div", { class: "notice-compact" },
      h("span", {}, "ⓘ"), h("span", {}, `Profit excludes ${r.today_profit_missing_cost_units} sold unit(s) without purchase costs.`)),
    h("h2", { class: "section" }, "Quick actions"),
    h("div", { class: "quick-grid" },
      can("cash_sale") && quickAction("New sale", "sales", "＋"), quickAction("Inventory", "products", "▣"),
      can("credit") && quickAction("Customer credit", "credit", "◫"), quickAction("Reports", "reports", "▥")),
    low.length && h("div", { class: "card low-panel" },
      h("div", { class: "row" }, h("strong", {}, "⚠ Restock soon"),
        h("button", { class: "link-button", onclick: () => showTab("products") }, "View products →")),
      ...low.slice(0, 3).map(p => h("div", { class: "row low-line" },
        h("span", {}, p.name), h("span", { class: "badge low" }, `${p.quantity} left`)))));
}
async function salesView() {
  const products = await api("/api/products");
  S.products = products;
  const list = h("div", { class: "list" });
  const search = h("input", { class: "input", type: "search", placeholder: "Find a product to sell…", "aria-label": "Find product to sell" });
  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const rows = products.filter(p => p.quantity > 0 && (!q || [p.name,p.id,p.category].some(v => String(v || "").toLowerCase().includes(q))));
    list.replaceChildren(...(rows.length ? rows.map(p => h("button", { class: "card sell-item", onclick: () => openSell(p) },
      h("div", {}, h("strong", {}, p.name), h("div", { class: "muted small" }, `${p.quantity} in stock · ${p.category || "General"}`)),
      h("span", { class: "sell-price" }, peso(p.price)), h("span", { class: "sell-plus" }, "+"))) : [empty("No available products found.")]));
  };
  search.addEventListener("input", paint); paint();
  return h("div", {}, pageHeading("CHECKOUT", "New sale", "Choose a product to record a cash sale"),
    h("div", { class: "toolbar" }, search), list);
}
async function showTab(id) {
  S.tab = id;
  document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
  const main = $("#view");
  if (!main) return;
  main.replaceChildren(h("p", { class: "empty" }, "Loading\u2026"));
  try {
    const node = await views[id]();
    if (S.tab === id && $("#view")) $("#view").replaceChildren(node);
  } catch (e) {
    if (e.status === 401 || e.status === 403 && e.code === "must_change") return;
    main.replaceChildren(h("div", { class: "empty" }, h("p", {}, e.message),
      h("button", { class: "btn spaced", onclick: () => showTab(id) }, "Try again")));
  }
}

// ---------------------------------------------------------------- Products tab
const statusBadge = (p) => h("span", { class: "badge " + (p.status === "OK" ? "ok" : p.status === "LOW STOCK" ? "low" : "bad") },
  p.status === "OK" ? "In stock" : p.status === "LOW STOCK" ? "Low stock" : "Expired");

async function productsView() {
  S.products = await api("/api/products");
  const list = h("div", { class: "list" });
  const search = h("input", { class: "input", type: "search", placeholder: "Search products\u2026", "aria-label": "Search products" });
  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const rows = S.products.filter((p) => !q || [p.name, p.id, p.category].some((v) => v.toLowerCase().includes(q)));
    list.replaceChildren(...(rows.length ? rows.map(productCard)
      : [empty(S.products.length ? "No products match your search." : "No products yet." + (can("add_product") ? " Tap + Add to start." : ""))]));
  };
  search.addEventListener("input", paint);
  paint();
  const low = S.products.filter((p) => p.status === "LOW STOCK");
  const warning = low.length ? h("div", { class: "card stock-warning" },
    h("div", { class: "strong" }, `⚠ ${low.length} product(s) need restocking`),
    h("div", { class: "small" }, low.slice(0, 5).map((p) => `${p.name}: ${p.quantity} left`).join(" · "))) : null;
  return h("div", {}, pageHeading("INVENTORY", "Products", "Manage stock, prices and product details"), warning, h("div", { class: "toolbar" }, search,
    can("add_product") && h("button", { class: "btn btn-primary", onclick: openAddProduct }, "+ Add")), list);
}
function productCard(p) {
  return h("button", { class: "card", type: "button", onclick: () => openProductSheet(p) },
    h("div", { class: "row" }, h("span", { class: "strong" }, p.name), h("span", { class: "strong" }, peso(p.price))),
    h("div", { class: "row" },
      h("span", { class: "muted small" }, `${p.id}${p.category ? " \u00B7 " + p.category : ""} \u00B7 ${p.quantity} left`),
      statusBadge(p)));
}
function openProductSheet(p) {
  const actions = h("div", { class: "btn-row" },
    can("cash_sale") && h("button", { class: "btn btn-primary", onclick: () => openSell(p) }, "Cash sale"),
    can("restock") && h("button", { class: "btn", onclick: () => openRestock(p) }, "Restock"));
  openSheet(p.name, kv([
    ["Product ID", p.id], ["Category", p.category || "\u2014"], ["Price", peso(p.price)],
    ["In stock", String(p.quantity)], ["Reorder at", String(p.reorder_level)],
    ["Expires", p.expiration || "\u2014"],
    ...(can("add_product") ? [["Purchase cost", p.cost == null ? "Not set" : peso(p.cost)]] : [])]),
    h("p", { class: "spaced" }, statusBadge(p)),
    can("add_product") && h("button", { class: "btn btn-block", onclick: () => openCostEditor(p) }, "Set purchase cost"),
    h("div", { class: "spaced" }, actions));
}
function openCostEditor(p) {
  openSheet(`Purchase cost: ${p.name}`, buildForm([
    { name: "cost", label: "Cost per unit (₱)", inputmode: "decimal", value: p.cost == null ? "" : String(p.cost) }
  ], "Save cost", async (v) => {
    await api(`/api/products/${encodeURIComponent(p.id)}/cost`, { method: "PATCH", body: v });
    closeSheet(); toast("Purchase cost saved. Future sales will use this cost."); showTab("products");
  }));
}
function openSell(p) {
  const form = buildForm([{ name: "quantity", label: `Quantity to sell (${p.quantity} in stock)`, inputmode: "numeric", value: "1" }],
    "Record cash sale", async (v) => {
      const r = await api("/api/sales", { method: "POST", body: { product_id: p.id, quantity: v.quantity } });
      closeSheet(); toast(r.message); showTab("products");
    });
  const total = h("p", { class: "total" });
  const update = () => { const q = parseInt(form.inputs.quantity.value, 10); total.textContent = q > 0 ? `Total: ${peso(q * p.price)}` : ""; };
  form.inputs.quantity.addEventListener("input", update); update();
  form.insertBefore(total, form.querySelector(".form-error"));
  openSheet("Cash sale: " + p.name, form);
}
function openRestock(p) {
  openSheet("Restock: " + p.name, buildForm([{ name: "quantity", label: "Amount to add", inputmode: "numeric" }],
    "Add stock", async (v) => {
      const r = await api(`/api/products/${encodeURIComponent(p.id)}/restock`, { method: "POST", body: { quantity: v.quantity } });
      closeSheet(); toast(`${r.name} now has ${r.quantity} in stock.`); showTab("products");
    }));
}
function openAddProduct() {
  openSheet("Add product", buildForm([
    { name: "id", label: "Product ID", placeholder: "e.g. 001", maxlength: 30 },
    { name: "name", label: "Name", maxlength: 80, autocapitalize: "words" },
    { name: "category", label: "Category (optional)", maxlength: 40, autocapitalize: "words" },
    { name: "price", label: "Price (\u20B1)", inputmode: "decimal" },
    { name: "cost", label: "Purchase cost (₱, optional)", inputmode: "decimal", help: "Needed for profit calculations. Can be added later." },
    { name: "quantity", label: "Starting quantity", inputmode: "numeric" },
    { name: "reorder_level", label: "Reorder level", inputmode: "numeric", value: "10", help: "You get a low-stock warning at or below this number." },
    { name: "expiration", label: "Expiration date (only if it expires)", type: "date" },
  ], "Add product", async (v) => {
    await api("/api/products", { method: "POST", body: v });
    closeSheet(); toast("Product added."); showTab("products");
  }));
}

// ---------------------------------------------------------------- Credit tab
async function creditView() {
  const customers = await api("/api/customers");
  const list = h("div", { class: "list" });
  const search = h("input", { class: "input", type: "search", placeholder: "Search customers\u2026", "aria-label": "Search customers" });
  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const rows = customers.filter((c) => !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q));
    list.replaceChildren(...(rows.length ? rows.map(customerCard)
      : [empty(customers.length ? "No customers match your search." : "No customers yet. Tap + Add.")]));
  };
  search.addEventListener("input", paint);
  paint();
  const owed = customers.reduce((sum, c) => sum + c.balance, 0);
  return h("div", {},
    h("div", { class: "card spaced-bottom" }, h("div", { class: "row" }, h("span", { class: "muted" }, "Total owed to you"),
      h("span", { class: "strong " + (owed > 0 ? "danger" : "") }, peso(owed)))),
    h("div", { class: "toolbar spaced" }, search, h("button", { class: "btn btn-primary", onclick: openAddCustomer }, "+ Add")), list);
}
function customerCard(c) {
  return h("button", { class: "card", type: "button", onclick: () => openCustomerSheet(c) },
    h("div", { class: "row" }, h("span", { class: "strong" }, c.name),
      h("span", { class: "strong " + (c.balance > 0 ? "danger" : "muted") }, peso(c.balance))),
    h("div", { class: "muted small" }, `${c.id}${c.contact ? " \u00B7 " + c.contact : ""}`));
}
function openAddCustomer() {
  openSheet("Add customer", buildForm([
    { name: "name", label: "Name", maxlength: 80, autocapitalize: "words" },
    { name: "contact", label: "Contact number (optional)", inputmode: "tel", maxlength: 40 },
  ], "Add customer", async (v) => {
    await api("/api/customers", { method: "POST", body: v });
    closeSheet(); toast("Customer added."); showTab("credit");
  }));
}
function openCustomerSheet(c) {
  openSheet(c.name, h("p", { class: "muted" }, "Owes you"), h("p", { class: "big " + (c.balance > 0 ? "danger" : "") }, peso(c.balance)),
    h("div", { class: "btn-row spaced" },
      h("button", { class: "btn btn-primary", onclick: () => openCharge(c) }, "Charge (utang)"),
      h("button", { class: "btn", onclick: () => openPayment(c) }, "Payment")),
    h("button", { class: "btn btn-ghost btn-block spaced", onclick: () => openLedger(c) }, "View history"));
}
async function openCharge(c) {
  let products;
  try { products = (await api("/api/products")).filter((p) => p.quantity > 0); }
  catch (e) { toast(e.message, true); return; }
  if (!products.length) { toast("No products in stock to charge.", true); return; }
  const byId = Object.fromEntries(products.map((p) => [p.id, p]));
  const form = buildForm([
    { name: "product_id", label: "Product", type: "select",
      options: products.map((p) => ({ value: p.id, label: `${p.name} \u2013 ${peso(p.price)} (${p.quantity} left)` })) },
    { name: "quantity", label: "Quantity", inputmode: "numeric", value: "1" },
  ], "Charge to " + c.name, async (v) => {
    const r = await api(`/api/customers/${encodeURIComponent(c.id)}/charge`, { method: "POST", body: v });
    closeSheet(); toast(r.message); showTab("credit");
  });
  const total = h("p", { class: "total" });
  const update = () => {
    const q = parseInt(form.inputs.quantity.value, 10), p = byId[form.inputs.product_id.value];
    total.textContent = q > 0 && p ? `Total: ${peso(q * p.price)}` : "";
  };
  form.inputs.quantity.addEventListener("input", update);
  form.inputs.product_id.addEventListener("change", update);
  update();
  form.insertBefore(total, form.querySelector(".form-error"));
  openSheet("Charge: " + c.name, form);
}
function openPayment(c) {
  if (c.balance <= 0) { toast(`${c.name} has no balance to pay.`); return; }
  const form = buildForm([{ name: "amount", label: `Amount paid (balance ${peso(c.balance)})`, inputmode: "decimal" }],
    "Record payment", async (v) => {
      const r = await api(`/api/customers/${encodeURIComponent(c.id)}/payment`, { method: "POST", body: v });
      closeSheet(); toast(r.message); showTab("credit");
    });
  form.insertBefore(h("button", { class: "btn btn-ghost", type: "button",
    onclick: () => { form.inputs.amount.value = c.balance.toFixed(2); } }, "Pay full balance"), form.querySelector(".form-error"));
  openSheet("Payment: " + c.name, form);
}
async function openLedger(c) {
  try {
    const r = await api(`/api/customers/${encodeURIComponent(c.id)}/ledger`);
    openSheet("History: " + c.name,
      h("p", { class: "muted" }, "Current balance"), h("p", { class: "big " + (r.customer.balance > 0 ? "danger" : "") }, peso(r.customer.balance)),
      h("div", { class: "list spaced" }, r.transactions.length ? r.transactions.map((t) =>
        h("div", { class: "card" },
          h("div", { class: "row" }, h("span", { class: "ledger-type " + (t.type === "CHARGE" ? "danger" : "") }, t.type === "CHARGE" ? "CHARGE" : "PAYMENT"),
            h("span", { class: "strong" }, (t.type === "CHARGE" ? "+" : "\u2212") + peso(t.amount))),
          h("div", { class: "small" }, t.description),
          h("div", { class: "muted small" }, `${fmtDate(t.at)} \u00B7 ${t.by}`))) : [empty("No transactions yet.")]));
  } catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- Reports tab
async function reportsView() {
  const r = await api("/api/reports");
  const stat = (label, value, cls, wide) => h("div", { class: "card stat" + (wide ? " wide" : "") }, h("div", { class: "label" }, label), h("div", { class: "value " + (cls || "") }, value));
  const section = (title, rows, render) => [h("h2", { class: "section" }, title),
    h("div", { class: "list" }, rows.length ? rows.map(render) : [empty("Nothing to show.")])];
  const line = (left, right, cls) => h("div", { class: "card row" }, h("span", {}, left), h("span", { class: "strong " + (cls || "") }, right));
  return h("div", {}, pageHeading("ANALYTICS", "Reports", "Sales performance and downloadable records"),
    h("div", { class: "stats" },
      stat("Cash sales today", peso(r.cash_sales_today), "", true),
      stat("Sales transactions today", String(r.today_transactions)),
      stat(r.today_profit_complete ? "Gross profit today" : "Known gross profit today", peso(r.today_profit_known)),
      stat("Cash sales (all time)", peso(r.cash_sales_total)),
      stat("Credit given", peso(r.credit_charged)),
      stat("Payments received", peso(r.credit_paid)),
      stat("Still owed to you", peso(r.outstanding), r.outstanding > 0 ? "danger" : "")),
    !r.today_profit_complete && h("div", { class: "card stock-warning" }, `Profit is incomplete: ${r.today_profit_missing_cost_units} unit(s) sold without recorded purchase cost. Historical costs are not guessed.`),
    h("h2", { class: "section" }, "Export reports (CSV / Excel)"),
    h("div", { class: "export-actions" },
      ...["products", "sales", "credit"].map((kind) => h("a", { class: "btn", href: `/api/export/${kind}.csv`, download: `sari-sari-${kind}.csv` }, `Download ${kind}`))),
    section("Best sellers", r.best_sellers, (b) => line(b.name, `${b.quantity} sold`)),
    section("Customers who owe", r.customers_with_balance, (c) => line(c.name, peso(c.balance), "danger")),
    section("Low stock", r.low_stock, (p) => line(p.name, `${p.quantity} left`)),
    section("Expired", r.expired, (p) => line(p.name, p.expiration, "danger")));
}

// ---------------------------------------------------------------- More tab (users, password, updates)
async function moreView() {
  const items = [
    h("div", { class: "card" }, h("div", { class: "strong" }, S.me.username), h("div", { class: "muted small" }, `Role: ${S.me.role}`)),
  ];
  if (can("manage_users")) items.push(h("button", { class: "btn btn-block", onclick: openUsers }, "Manage users"));
  items.push(
    h("button", { class: "btn btn-block", onclick: () => openSheet("Change password", changePasswordForm(false)) }, "Change my password"),
    h("button", { class: "btn btn-block", onclick: checkForUpdates }, "Check for updates"),
    h("button", { class: "btn btn-block", onclick: logout }, "Log out"),
    h("p", { class: "muted small center spaced" }, `Sari-Sari Store app \u00B7 version ${CLIENT_VERSION}`),
    h("p", { class: "muted small center" }, "Updates install by themselves. You never need to reinstall."));
  return h("div", {}, pageHeading("ACCOUNT", "Settings", "Manage your profile and application"), h("div", { class: "list" }, items));
}
async function openUsers() {
  try {
    const data = await api("/api/users");
    openSheet("Users",
      h("button", { class: "btn btn-primary btn-block", onclick: () => openAddUser(data.allowed_roles) }, "+ Add user"),
      h("div", { class: "list spaced" }, data.users.map((u) =>
        h(u.editable ? "button" : "div", { class: "card row", type: u.editable ? "button" : null, onclick: u.editable ? () => openEditUser(u, data.allowed_roles) : null },
          h("span", { class: "strong" }, u.username), h("span", { class: "chip role-chip" }, u.role + (u.editable ? "" : " \u00B7 locked"))))));
  } catch (e) { toast(e.message, true); }
}
function openAddUser(roles) {
  openSheet("Add user", buildForm([
    { name: "username", label: "Username", autocapitalize: "none", maxlength: 30, help: "Letters, numbers, dots, dashes. At least 3." },
    { name: "password", label: "Temporary password", help: "Tell this to the person. They choose their own password when they first sign in." },
    { name: "role", label: "Role", type: "select", value: "Staff", options: roles.map((r) => ({ value: r, label: r })) },
  ], "Create user", async (v) => {
    await api("/api/users", { method: "POST", body: v });
    toast(`${v.username} added as ${v.role}.`); openUsers();
  }));
}
function openEditUser(u, roles) {
  const roleForm = buildForm([{ name: "role", label: "Role", type: "select", value: u.role, options: roles.map((r) => ({ value: r, label: r })) }],
    "Save role", async (v) => { await api("/api/users/" + encodeURIComponent(u.username), { method: "PATCH", body: { role: v.role } }); toast("Role updated."); openUsers(); });
  const pwForm = buildForm([{ name: "password", label: "New temporary password", help: "They will be signed out and must choose a new password." }],
    "Reset password", async (v) => { await api("/api/users/" + encodeURIComponent(u.username), { method: "PATCH", body: { password: v.password } }); toast("Password reset."); openUsers(); });
  openSheet("Edit " + u.username, roleForm, h("div", { class: "spaced" }, pwForm),
    h("div", { class: "spaced" }, confirmTap("Delete user", "Tap again to delete " + u.username, async () => {
      try { await api("/api/users/" + encodeURIComponent(u.username), { method: "DELETE" }); toast("User deleted."); openUsers(); }
      catch (e) { toast(e.message, true); }
    })));
}

const views = { dashboard: dashboardView, products: productsView, sales: salesView, credit: creditView, reports: reportsView, more: moreView };

// ---------------------------------------------------------------- start
function offlineView(message) {
  return h("div", { class: "auth" }, h("h1", {}, "You're offline"), h("p", { class: "sub" }, message),
    h("button", { class: "btn btn-primary btn-block", onclick: boot }, "Try again"));
}
function render() {
  const app = $("#app");
  app.replaceChildren();
  if (!S.me) app.append(loginView());
  else if (S.me.must_change) app.append(forcedPasswordView());
  else { app.append(shell()); showTab(S.tab); }
}
async function boot() {
  try { S.me = await api("/api/me", { noRedirect: true }); }
  catch (e) {
    if (!e.status) { $("#app").replaceChildren(offlineView(e.message)); return; }
    S.me = null;
  }
  render();
}
const syncOnline = () => document.body.classList.toggle("is-offline", !navigator.onLine);
window.addEventListener("online", () => { syncOnline(); toast("Back online."); });
window.addEventListener("offline", () => { syncOnline(); toast("You're offline. Changes need a connection.", true); });
syncOnline();
setupUpdates();
boot();
