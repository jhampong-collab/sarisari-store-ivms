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
  function renderStores() {
    const query = search.value.trim().toLocaleLowerCase();
    const filtered = stores.filter(store =>
      store.name.toLocaleLowerCase().includes(query) || store.id.toLocaleLowerCase().includes(query));
    body.replaceChildren();
    visibleStores.textContent = String(filtered.length);
    for (const store of filtered) {
      const tr = document.createElement('tr');
      for (const value of [store.name, store.id, String(store.member_count)]) {
        const td = document.createElement('td');
        td.textContent = value;
        if (value === store.id) td.className = 'store-id';
        if (value === String(store.member_count)) td.classList.add('number');
        tr.appendChild(td);
      }
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
      if (!Array.isArray(result) || !result.every(row => row && typeof row.id === 'string' && typeof row.name === 'string' && Number.isInteger(row.member_count))) {
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
    await loadStores();
  } catch (error) {
    status.textContent = 'Unable to verify access. Please check your connection and refresh.';
  }
})();
