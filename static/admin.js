"use strict";
(async function initializeSuperAdmin() {
  const status = document.getElementById('status');
  const content = document.getElementById('content');
  const signout = document.getElementById('signout');
  const search = document.getElementById('store-search');
  const refresh = document.getElementById('refresh');
  const body = document.getElementById('stores-body');
  const storesStatus = document.getElementById('stores-status');
  const totalStores = document.getElementById('total-stores');
  const visibleStores = document.getElementById('visible-stores');
  const totalMembers = document.getElementById('total-members');
  let stores = [];
  const token = sessionStorage.getItem('sari_v2_test_token');
  const headers = { 'Authorization': 'Bearer ' + token, 'X-Requested-With': 'sari-app' };
  signout.addEventListener('click', () => {
    sessionStorage.removeItem('sari_v2_test_token');
    window.location.replace('/');
  });
  if (!token) {
    status.textContent = 'Please sign in to access platform administration.';
    window.location.replace('/');
    return;
  }
  function showMessage(message) {
    storesStatus.textContent = message;
  }
  async function changeStoreStatus(store) {
    const target = store.status === 'active' ? 'suspended' : 'active';
    const verb = target === 'suspended' ? 'Suspend' : 'Reactivate';
    if (!window.confirm(verb + ' "' + store.name + '"? ' +
      (target === 'suspended' ? 'Its members will lose store API access until reactivated.' : 'Its members will regain access.'))) return;
    const buttons = body.querySelectorAll('button');
    buttons.forEach(button => { button.disabled = true; });
    try {
      
const reason = window.prompt(
  target === 'suspended'
    ? 'Enter reason for suspending "' + store.name + '":'
    : 'Enter reason for reactivating "' + store.name + '":'
);

if (reason === null || !reason.trim()) {
  showMessage('Action cancelled. A reason is required.');
  buttons.forEach(button => { button.disabled = false; });
  return;
}

if (reason.trim().length > 500) {
  showMessage('Reason must be 500 characters or less.');
  buttons.forEach(button => { button.disabled = false; });
  return;
}

const response = await fetch('/api/v2/admin/stores/' + encodeURIComponent(store.id) + '/status', {
  method: 'POST',
  headers: { ...headers, 'Content-Type': 'application/json' },
  body: JSON.stringify({
    status: target,
    reason: reason.trim()
  })
});

      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.ok !== true || result.status !== target) {
        showMessage(result.error || 'Unable to change store status (HTTP ' + response.status + ').');
        buttons.forEach(button => { button.disabled = false; });
        return;
      }
      await Promise.all([loadStores(), loadLogs()]);
      showMessage('Store "' + store.name + '" successfully ' + (target === 'suspended' ? 'suspended.' : 'reactivated.'));
    } catch (error) {
      showMessage('Network error. Refresh to check the current status before trying again.');
      buttons.forEach(button => { button.disabled = false; });
    }
  }
  function renderStores() {
    const query = search.value.trim().toLocaleLowerCase();
    const filtered = stores.filter(store =>
      store.name.toLocaleLowerCase().includes(query) || store.id.toLocaleLowerCase().includes(query));
    body.replaceChildren();
    visibleStores.textContent = String(filtered.length);
    for (const store of filtered) {
      const tr = document.createElement('tr');
      const nameCell = document.createElement('td');
      nameCell.textContent = store.name;
      tr.appendChild(nameCell);
      const idCell = document.createElement('td');
      idCell.textContent = store.id;
      idCell.className = 'store-id';
      tr.appendChild(idCell);
      const membersCell = document.createElement('td');
      membersCell.textContent = String(store.member_count);
      membersCell.className = 'number';
      tr.appendChild(membersCell);
      const statusCell = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = 'status-pill ' + (store.status === 'active' ? 'status-active' : 'status-suspended');
      badge.textContent = store.status === 'active' ? 'Active' : 'Suspended';
      statusCell.appendChild(badge);
      tr.appendChild(statusCell);
      const actionCell = document.createElement('td');
      const action = document.createElement('button');
      action.type = 'button';
      action.className = store.status === 'active' ? 'action-danger' : 'action-success';
      action.textContent = store.status === 'active' ? 'Suspend' : 'Reactivate';
      action.setAttribute('aria-label', action.textContent + ' ' + store.name);
      action.addEventListener('click', () => changeStoreStatus(store));
      actionCell.appendChild(action);
      tr.appendChild(actionCell);
      body.appendChild(tr);
    }
    storesStatus.textContent = filtered.length
      ? 'Showing ' + filtered.length + ' of ' + stores.length + ' stores.'
      : (stores.length ? 'No stores match your search.' : 'No registered stores found.');
  }
  async function loadStores() {
    refresh.disabled = true;
    storesStatus.textContent = 'Loading stores…';
    try {
      const response = await fetch('/api/v2/admin/stores', { method: 'GET', cache: 'no-store', headers });
      if (!response.ok) {
        storesStatus.textContent = response.status === 403
          ? 'Access denied. Your account does not have permission to list stores.'
          : 'Could not load stores (HTTP ' + response.status + '). Please retry.';
        return;
      }
      const result = await response.json();
      if (!Array.isArray(result) || !result.every(row => row && typeof row.id === 'string' && typeof row.name === 'string' && Number.isInteger(row.member_count) && ['active', 'suspended'].includes(row.status))) {
        storesStatus.textContent = 'Unexpected store data returned by the server.';
        return;
      }
      stores = result;
      totalStores.textContent = String(stores.length);
      totalMembers.textContent = String(stores.reduce((sum, store) => sum + store.member_count, 0));
      renderStores();
    } catch (error) {
      storesStatus.textContent = 'Unable to load stores. Check your connection and retry.';
    } finally {
      refresh.disabled = false;
    }
  }

  const logsBody = document.getElementById('logs-body');
  const logsStatus = document.getElementById('logs-status');
  const logsSearch = document.getElementById('logs-search');
  const logsFilter = document.getElementById('logs-filter');
  const refreshLogs = document.getElementById('refresh-logs');
  let logs = [];
  const manilaTime = new Intl.DateTimeFormat('en-PH', {
    timeZone: 'Asia/Manila', year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: true
  });
  function renderLogs() {
    const query = logsSearch.value.trim().toLocaleLowerCase();
    const action = logsFilter.value;
    const filtered = logs.filter(log =>
      (action === 'all' || log.action === action) &&
      [log.store_name, log.reason, log.action, log.previous_status, log.new_status]
        .some(value => String(value || '').toLocaleLowerCase().includes(query)));
    logsBody.replaceChildren();
    for (const log of filtered) {
      const tr = document.createElement('tr');
      const dateCell = document.createElement('td');
      const parsed = new Date(log.created_at);
      dateCell.textContent = Number.isNaN(parsed.getTime()) ? 'Unknown' : manilaTime.format(parsed);
      tr.appendChild(dateCell);
      const storeCell = document.createElement('td');
      storeCell.textContent = log.store_name;
      tr.appendChild(storeCell);
      const actionCell = document.createElement('td');
      const pill = document.createElement('span');
      pill.className = 'status-pill ' + (log.action === 'STORE_SUSPENDED' ? 'status-suspended' : 'status-active');
      pill.textContent = log.action === 'STORE_SUSPENDED' ? 'Suspended' : 'Reactivated';
      actionCell.appendChild(pill);
      tr.appendChild(actionCell);
      const changeCell = document.createElement('td');
      changeCell.textContent = log.previous_status + ' → ' + log.new_status;
      tr.appendChild(changeCell);
      const reasonCell = document.createElement('td');
      reasonCell.className = 'log-reason';
      reasonCell.textContent = log.reason;
      tr.appendChild(reasonCell);
      logsBody.appendChild(tr);
    }
    logsStatus.textContent = filtered.length
      ? 'Showing ' + filtered.length + ' of ' + logs.length + ' recent activity records.'
      : (logs.length ? 'No activity records match your filters.' : 'No activity logs yet.');
  }
  async function loadLogs() {
    refreshLogs.disabled = true;
    logsStatus.textContent = 'Loading activity logs…';
    try {
      const response = await fetch('/api/v2/admin/logs', { method: 'GET', cache: 'no-store', headers });
      if (!response.ok) {
        logsStatus.textContent = response.status === 403 ? 'Super Admin access required.' :
          'Unable to load activity logs (HTTP ' + response.status + ').';
        return;
      }
      const result = await response.json();
      if (!Array.isArray(result) || !result.every(row => row &&
        typeof row.store_name === 'string' && typeof row.reason === 'string' &&
        typeof row.created_at === 'string' &&
        ['STORE_SUSPENDED', 'STORE_REACTIVATED'].includes(row.action))) {
        logsStatus.textContent = 'Unexpected activity log data from the server.';
        return;
      }
      logs = result;
      renderLogs();
    } catch (error) {
      logsStatus.textContent = 'Network error while loading activity logs.';
    } finally {
      refreshLogs.disabled = false;
    }
  }
  logsSearch.addEventListener('input', renderLogs);
  logsFilter.addEventListener('change', renderLogs);
  refreshLogs.addEventListener('click', loadLogs);

  search.addEventListener('input', renderStores);
  refresh.addEventListener('click', loadStores);
  try {
    const response = await fetch('/api/v2/admin/check', { method: 'GET', cache: 'no-store', headers });
    if (!response.ok) {
      status.textContent = response.status === 403 ? 'Super Admin access denied.' : 'Unable to verify administrator access. Please sign in again.';
      return;
    }
    const result = await response.json();
    if (result.ok !== true || result.is_super_admin !== true) {
      status.textContent = 'Super Admin access could not be confirmed.';
      return;
    }
    status.textContent = 'Welcome to INTENSITY Platform Administration.';
    content.hidden = false;
    await Promise.all([loadStores(), loadLogs()]);
  } catch (error) {
    status.textContent = 'Unable to verify access. Please check your connection and refresh.';
  }
})();
