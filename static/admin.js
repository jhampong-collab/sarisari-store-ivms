"use strict";
(async function initializeSuperAdmin() {
  const status = document.getElementById('status');
  const content = document.getElementById('content');
  const signout = document.getElementById('signout');
  signout.addEventListener('click', () => {
    sessionStorage.removeItem('sari_v2_test_token');
    window.location.replace('/');
  });
  const token = sessionStorage.getItem('sari_v2_test_token');
  if (!token) {
    status.textContent = 'Please sign in to access platform administration.';
    window.location.replace('/');
    return;
  }
  try {
    const response = await fetch('/api/v2/admin/check', {
      method: 'GET',
      cache: 'no-store',
      headers: {
        'Authorization': 'Bearer ' + token,
        'X-Requested-With': 'sari-app'
      }
    });
    if (!response.ok) {
      status.textContent = response.status === 403
        ? 'Super Admin access denied.'
        : 'Unable to verify administrator access. Please sign in again.';
      return;
    }
    const result = await response.json();
    if (result.ok !== true || result.is_super_admin !== true) {
      status.textContent = 'Super Admin access could not be confirmed.';
      return;
    }
    status.textContent = 'Welcome to INTENSITY Platform Administration.';
    content.hidden = false;
  } catch (error) {
    status.textContent = 'Unable to verify access. Please check your connection and refresh.';
  }
})();
