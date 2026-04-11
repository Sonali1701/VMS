const API_BASE = window.location.origin;

// Infinite scroll state
let currentPage = 1;
let isLoadingMore = false;
let hasMoreJobs = true;
let nextStartPage = 26;  // After initial 25 pages

const els = {
  jobsGrid: document.getElementById('jobsGrid'),
  jobsEmpty: document.getElementById('jobsEmpty'),
  searchInput: document.getElementById('searchInput'),
  statusFilter: document.getElementById('statusFilter'),
  refreshBtn: document.getElementById('refreshBtn'),
  ceipalCacheBtn: document.getElementById('ceipalCacheBtn'),
  ceipalTestBtn: document.getElementById('ceipalTestBtn'),
  openDocsBtn: document.getElementById('openDocsBtn'),
  systemJson: document.getElementById('systemJson'),
  systemStatus: document.getElementById('systemStatus'),
  apiBase: document.getElementById('apiBase'),
  pageTitle: document.getElementById('pageTitle'),
  pageSubtitle: document.getElementById('pageSubtitle'),
  viewJobs: document.getElementById('viewJobs'),
  viewSubmissions: document.getElementById('viewSubmissions'),
  viewOffers: document.getElementById('viewOffers'),
  viewDeclines: document.getElementById('viewDeclines'),
  viewStarts: document.getElementById('viewStarts'),
  viewSettings: document.getElementById('viewSettings'),
  viewAuth: document.getElementById('viewAuth'),
  submissionsTable: document.getElementById('submissionsTable'),
  offersTable: document.getElementById('offersTable'),
  declinesTable: document.getElementById('declinesTable'),
  startsTable: document.getElementById('startsTable'),
  // Auth elements
  userInfo: document.getElementById('userInfo'),
  userName: document.getElementById('userName'),
  logoutBtn: document.getElementById('logoutBtn'),
  authTitle: document.getElementById('authTitle'),
  authEmail: document.getElementById('authEmail'),
  authPassword: document.getElementById('authPassword'),
  authFullName: document.getElementById('authFullName'),
  registerFields: document.getElementById('registerFields'),
  authSubmitBtn: document.getElementById('authSubmitBtn'),
  authToggleBtn: document.getElementById('authToggleBtn'),
  authAlert: document.getElementById('authAlert'),
  // Job Detail Modal
  jobDetailModal: document.getElementById('jobDetailModal'),
  jobDetailTitle: document.getElementById('jobDetailTitle'),
  jobDetailJobTitle: document.getElementById('jobDetailJobTitle'),
  jobDetailMeta: document.getElementById('jobDetailMeta'),
  jobDetailFullDesc: document.getElementById('jobDetailFullDesc'),
  jobDetailRequirements: document.getElementById('jobDetailRequirements'),
  jobDetailCandidates: document.getElementById('jobDetailCandidates'),
  jobDetailSubmitBtn: document.getElementById('jobDetailSubmitBtn'),
  // Submit Modal
  submitModal: document.getElementById('submitModal'),
  submitModalTitle: document.getElementById('submitModalTitle'),
  submitJobTitle: document.getElementById('submitJobTitle'),
  submitJobMeta: document.getElementById('submitJobMeta'),
  candName: document.getElementById('candName'),
  candEmail: document.getElementById('candEmail'),
  candPhone: document.getElementById('candPhone'),
  candBillRate: document.getElementById('candBillRate'),
  candLocation: document.getElementById('candLocation'),
  candSkills: document.getElementById('candSkills'),
  candJobTitle: document.getElementById('candJobTitle'),
  candExperience: document.getElementById('candExperience'),
  candStartDate: document.getElementById('candStartDate'),
  candRTO: document.getElementById('candRTO'),
  candSummary: document.getElementById('candSummary'),
  candResume: document.getElementById('candResume'),
  submitAnotherBtn: document.getElementById('submitAnotherBtn'),
  submitCloseBtn: document.getElementById('submitCloseBtn'),
  formAlert: document.getElementById('formAlert')
};

els.apiBase.textContent = `API: ${API_BASE}`;

let allJobs = [];
let activeJobId = null;
let activeJob = null;

// Auth state
let authToken = localStorage.getItem('vms_token') || null;
let currentUser = JSON.parse(localStorage.getItem('vms_user') || 'null');
let isRegisterMode = false;

const ADMIN_EMAIL = 'Admin@radixsol.com';

function isAdmin() {
  return currentUser && currentUser.email && currentUser.email.toLowerCase() === ADMIN_EMAIL.toLowerCase();
}

function showAuthAlert(kind, msg) {
  els.authAlert.hidden = false;
  els.authAlert.className = `alert ${kind === 'ok' ? 'alert--ok' : 'alert--error'}`;
  els.authAlert.textContent = msg;
}

function clearAuthAlert() {
  els.authAlert.hidden = true;
  els.authAlert.textContent = '';
}

function updateAuthUI() {
  if (authToken && currentUser) {
    // Logged in - show user info and logout
    els.userInfo.hidden = false;
    els.userName.textContent = currentUser.full_name || currentUser.email;
    els.viewAuth.hidden = true;
    els.viewJobs.hidden = false;
    
    // Submissions tab visible for all users (vendors see their own, admin sees all)
    const submissionsNav = document.querySelector('[data-view="submissions"]');
    if (submissionsNav) {
      submissionsNav.hidden = false;
    }
  } else {
    // Not logged in - show auth form
    els.userInfo.hidden = true;
    els.viewAuth.hidden = false;
    els.viewJobs.hidden = true;
    els.viewSubmissions.hidden = true;
    els.viewSettings.hidden = true;
  }
}

function logout() {
  authToken = null;
  currentUser = null;
  localStorage.removeItem('vms_token');
  localStorage.removeItem('vms_user');
  updateAuthUI();
}

function toggleAuthMode() {
  isRegisterMode = !isRegisterMode;
  els.authTitle.textContent = isRegisterMode ? 'Register' : 'Login';
  els.authSubmitBtn.textContent = isRegisterMode ? 'Register' : 'Login';
  els.authToggleBtn.textContent = isRegisterMode ? 'Back to Login' : 'Register';
  els.registerFields.hidden = !isRegisterMode;
  clearAuthAlert();
}

async function handleAuthSubmit() {
  clearAuthAlert();
  
  const email = els.authEmail.value.trim();
  const password = els.authPassword.value;
  
  if (!email) return showAuthAlert('error', 'Email is required');
  if (!password) return showAuthAlert('error', 'Password is required');
  
  const body = isRegisterMode 
    ? { email, password, full_name: els.authFullName.value.trim() }
    : { email, password };
  
  const endpoint = isRegisterMode ? '/api/auth/register' : '/api/auth/login';
  
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      // Server returned HTML error page
      throw new Error(text.includes('Internal Server Error') ? 'Server error. Please try again.' : text.slice(0, 100));
    }
    
    if (!res.ok) {
      throw new Error(data.detail || data.message || `Error: ${res.status}`);
    }
    
    // Store token and user
    authToken = data.access_token;
    currentUser = data.user;
    localStorage.setItem('vms_token', authToken);
    localStorage.setItem('vms_user', JSON.stringify(currentUser));
    
    // Clear form
    els.authEmail.value = '';
    els.authPassword.value = '';
    els.authFullName.value = '';
    
    // Show success message briefly then switch to jobs
    showAuthAlert('ok', isRegisterMode ? 'Registered successfully!' : 'Logged in successfully!');
    
    setTimeout(async () => {
      updateAuthUI();
      setView('jobs');  // Redirect to VMS interface
      await loadCeipalStatus();
      await loadJobs();
    }, 800);
    
  } catch (e) {
    showAuthAlert('error', e.message);
  }
}

// Modified API function that includes auth token
async function apiPostFormAuth(path, formData) {
  const headers = {};
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }
  
  const res = await fetch(`${API_BASE}${path}`, { 
    method: 'POST', 
    body: formData,
    headers
  });
  
  if (res.status === 401) {
    // Token expired or invalid
    logout();
    throw new Error('Session expired. Please login again.');
  }
  
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { _raw: text }; }
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.data = json;
    throw err;
  }
  return json;
}

function setStatus(kind, text) {
  const dot = els.systemStatus.querySelector('.status__dot');
  const t = els.systemStatus.querySelector('.status__text');
  dot.classList.remove('status__dot--warn', 'status__dot--ok', 'status__dot--bad');
  if (kind === 'ok') dot.classList.add('status__dot--ok');
  else if (kind === 'bad') dot.classList.add('status__dot--bad');
  else dot.classList.add('status__dot--warn');
  t.textContent = text;
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { _raw: text }; }
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.data = json;
    throw err;
  }
  return json;
}

async function apiGetAuth(path) {
  const headers = {};
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }
  
  const res = await fetch(`${API_BASE}${path}`, { headers });
  
  if (res.status === 401) {
    logout();
    throw new Error('Session expired. Please login again.');
  }
  
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { _raw: text }; }
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.data = json;
    throw err;
  }
  return json;
}

async function apiPostForm(path, formData) {
  const res = await fetch(`${API_BASE}${path}`, { method: 'POST', body: formData });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { _raw: text }; }
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.data = json;
    throw err;
  }
  return json;
}

function normalizeText(v) {
  return (v ?? '').toString().toLowerCase();
}

function jobMatches(job) {
  const q = normalizeText(els.searchInput.value).trim();
  const status = normalizeText(els.statusFilter.value).trim();

  if (q) {
    const hay = [job.title, job.department, job.location, job.employment_type].map(normalizeText).join(' ');
    if (!hay.includes(q)) return false;
  }

  if (status) {
    const s = normalizeText(job.status);
    if (!s.includes(status)) return false;
  }

  return true;
}

function renderJobs() {
  els.jobsGrid.innerHTML = '';
  const filtered = allJobs.filter(jobMatches);

  els.jobsEmpty.hidden = filtered.length !== 0;

  for (const job of filtered) {
    const card = document.createElement('div');
    card.className = 'card';

    const title = document.createElement('div');
    title.className = 'card__title';
    title.textContent = job.title || 'Untitled job';

    const meta = document.createElement('div');
    meta.className = 'card__meta';
    meta.innerHTML = `
      <span class="tag">${job.department || 'Department: N/A'}</span>
      <span class="tag">${job.location || 'Location: N/A'}</span>
      <span class="tag">${job.employment_type || 'Type: N/A'}</span>
      <span class="tag">Status: ${job.status || 'N/A'}</span>
    `;

    const desc = document.createElement('div');
    desc.className = 'card__desc';
    const d = (job.description || '').toString();
    desc.textContent = d.length > 260 ? `${d.slice(0, 260)}…` : d;

    const actions = document.createElement('div');
    actions.className = 'card__actions';

    const btnDetails = document.createElement('button');
    btnDetails.className = 'btn btn--secondary';
    btnDetails.textContent = 'Job detail';
    btnDetails.addEventListener('click', () => openJobDetailModal(job));

    const btnSubmit = document.createElement('button');
    btnSubmit.className = 'btn';
    btnSubmit.textContent = 'Submit resume';
    btnSubmit.addEventListener('click', () => openSubmitModal(job));

    actions.appendChild(btnDetails);
    actions.appendChild(btnSubmit);

    card.appendChild(title);
    card.appendChild(meta);
    card.appendChild(desc);
    card.appendChild(actions);

    els.jobsGrid.appendChild(card);
  }
}

function setView(view) {
  document.querySelectorAll('.nav__item').forEach(btn => {
    btn.classList.toggle('nav__item--active', btn.dataset.view === view);
  });

  els.viewAuth.hidden = view !== 'auth';
  els.viewJobs.hidden = view !== 'jobs';
  els.viewSubmissions.hidden = view !== 'submissions';
  els.viewOffers.hidden = view !== 'offers';
  els.viewDeclines.hidden = view !== 'declines';
  els.viewStarts.hidden = view !== 'starts';
  els.viewSettings.hidden = view !== 'settings';

  if (view === 'auth') {
    els.pageTitle.textContent = 'Authentication';
    els.pageSubtitle.textContent = 'Please login or register to continue.';
  }
  if (view === 'jobs') {
    els.pageTitle.textContent = 'Jobs';
    els.pageSubtitle.textContent = 'Browse open roles and submit resumes per job.';
  }
  if (view === 'submissions') {
    els.pageTitle.textContent = 'Submissions';
    els.pageSubtitle.textContent = 'Recent candidate submissions.';
    loadSubmissions();
  }
  if (view === 'offers') {
    els.pageTitle.textContent = 'Offers';
    els.pageSubtitle.textContent = 'Track candidate offers sent to clients.';
    loadOffers();
  }
  if (view === 'declines') {
    els.pageTitle.textContent = 'Declines';
    els.pageSubtitle.textContent = 'Track declined offers and rejections.';
    loadDeclines();
  }
  if (view === 'starts') {
    els.pageTitle.textContent = 'Starts';
    els.pageSubtitle.textContent = 'Track candidates who have started working.';
    loadStarts();
  }
  if (view === 'settings') {
    els.pageTitle.textContent = 'System';
    els.pageSubtitle.textContent = 'Ceipal connectivity and cached raw responses.';
    loadSystem();
  }
}

function showAlert(kind, msg) {
  els.formAlert.hidden = false;
  els.formAlert.className = `alert ${kind === 'ok' ? 'alert--ok' : 'alert--error'}`;
  els.formAlert.textContent = msg;
}

function clearAlert() {
  els.formAlert.hidden = true;
  els.formAlert.textContent = '';
}

function openJobDetailModal(job) {
  activeJobId = job.id;
  activeJob = job;
  els.jobDetailTitle.textContent = `Job Details • ${job.title || job.id}`;
  els.jobDetailJobTitle.textContent = job.title || 'Selected job';
  
  const parts = [
    job.department ? `Dept: ${job.department}` : null,
    job.location ? `Location: ${job.location}` : null,
    job.employment_type ? `Type: ${job.employment_type}` : null,
    job.status ? `Status: ${job.status}` : null,
    job.salary_range ? `Salary: ${job.salary_range}` : null,
  ].filter(Boolean);
  els.jobDetailMeta.textContent = parts.join(' • ') || '—';
  
  els.jobDetailFullDesc.textContent = job.description || 'No description available.';
  els.jobDetailRequirements.textContent = job.requirements || 'No requirements specified.';
  
  // Show/hide candidates panel based on admin status (only admin sees submitted candidates)
  const candidatesPanel = document.getElementById('jobDetailCandidatesPanel');
  if (candidatesPanel) {
    candidatesPanel.hidden = !isAdmin();
  }
  
  els.jobDetailModal.hidden = false;
  
  // Only load candidates for admin
  if (isAdmin()) {
    loadJobDetailCandidates(activeJobId);
  }
}

function closeJobDetailModal() {
  els.jobDetailModal.hidden = true;
}

function openSubmitModal(job) {
  activeJobId = job.id;
  activeJob = job;
  els.submitModalTitle.textContent = `Submit Resume • ${job.title || job.id}`;
  els.submitJobTitle.textContent = job.title || 'Selected job';
  
  const parts = [
    job.department ? `Dept: ${job.department}` : null,
    job.location ? `Location: ${job.location}` : null,
    job.status ? `Status: ${job.status}` : null,
  ].filter(Boolean);
  els.submitJobMeta.textContent = parts.join(' • ') || '—';
  
  // Clear all fields
  els.candName.value = '';
  els.candEmail.value = '';
  els.candPhone.value = '';
  els.candBillRate.value = '';
  els.candLocation.value = '';
  els.candSkills.value = '';
  els.candJobTitle.value = '';
  els.candExperience.value = '';
  els.candStartDate.value = '';
  els.candRTO.value = '';
  els.candSummary.value = '';
  els.candResume.value = '';
  clearAlert();
  els.submitModal.hidden = false;
}

function closeSubmitModal() {
  els.submitModal.hidden = true;
  activeJobId = null;
  activeJob = null;
}

// Job Detail modal close handlers
els.jobDetailModal.addEventListener('click', (e) => {
  const node = e.target;
  if (!node) return;
  if (node.dataset && node.dataset.close === 'jobDetail') {
    closeJobDetailModal();
    return;
  }
  if (node.classList && node.classList.contains('modal__backdrop')) {
    closeJobDetailModal();
  }
});

// Job Detail Submit button opens Submit Modal
if (els.jobDetailSubmitBtn) {
  els.jobDetailSubmitBtn.addEventListener('click', () => {
    closeJobDetailModal();
    if (activeJob) openSubmitModal(activeJob);
  });
}

// Submit modal close handlers
els.submitModal.addEventListener('click', (e) => {
  const node = e.target;
  if (!node) return;
  if (node.dataset && node.dataset.close === 'submit') {
    closeSubmitModal();
    return;
  }
  if (node.classList && node.classList.contains('modal__backdrop')) {
    closeSubmitModal();
  }
});

// Close on Escape for both modals
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (els.jobDetailModal && !els.jobDetailModal.hidden) closeJobDetailModal();
    if (els.submitModal && !els.submitModal.hidden) closeSubmitModal();
  }
});

async function loadJobDetailCandidates(jobId) {
  if (!els.jobDetailCandidates) return;
  try {
    const data = await apiGet(`/api/candidates/job/${encodeURIComponent(jobId)}`);
    const candidates = data.candidates || [];
    els.jobDetailCandidates.textContent = candidates.length 
      ? JSON.stringify(candidates, null, 2) 
      : 'No submissions yet for this job.';
  } catch (e) {
    els.jobDetailCandidates.textContent = 'Failed to load candidates for this job.';
  }
}

async function submitResume({ closeAfter }) {
  clearAlert();

  const name = els.candName.value.trim();
  const email = els.candEmail.value.trim();
  const phone = els.candPhone.value.trim();
  const billRate = els.candBillRate.value.trim();
  const location = els.candLocation.value.trim();
  const skills = els.candSkills.value.trim();
  const jobTitle = els.candJobTitle.value.trim();
  const experience = els.candExperience.value.trim();
  const startDate = els.candStartDate.value;
  const rto = els.candRTO.value.trim();
  const summary = els.candSummary.value.trim();
  const file = els.candResume.files && els.candResume.files[0];

  if (!activeJobId) return showAlert('error', 'No job selected.');
  if (!name) return showAlert('error', 'Candidate name is required.');
  if (!email) return showAlert('error', 'Email is required.');
  if (!phone) return showAlert('error', 'Phone is required.');
  if (!billRate) return showAlert('error', 'Bill Rate is required.');
  if (!location) return showAlert('error', 'Current Location is required.');
  if (!skills) return showAlert('error', 'Primary Skills is required.');
  if (!jobTitle) return showAlert('error', 'Job Title is required.');
  if (!experience) return showAlert('error', 'Years of Experience is required.');
  if (!startDate) return showAlert('error', 'Tentative Start Date is required.');
  if (!rto) return showAlert('error', 'RTO is required.');
  if (!summary) return showAlert('error', 'Candidate Summary is required.');
  if (!file) return showAlert('error', 'Resume file is required.');

  const fd = new FormData();
  fd.append('candidate_name', name);
  fd.append('email', email);
  fd.append('phone', phone);
  fd.append('bill_rate', billRate);
  fd.append('current_location', location);
  fd.append('primary_skills', skills);
  fd.append('job_title', jobTitle);
  fd.append('years_experience', experience);
  fd.append('tentative_start_date', startDate);
  fd.append('rto', rto);
  fd.append('candidate_summary', summary);
  fd.append('job_id', activeJobId);
  fd.append('resume', file);

  if (els.submitAnotherBtn) els.submitAnotherBtn.disabled = true;
  if (els.submitCloseBtn) els.submitCloseBtn.disabled = true;
  if (els.submitAnotherBtn) els.submitAnotherBtn.textContent = 'Submitting…';
  if (els.submitCloseBtn) els.submitCloseBtn.textContent = 'Submitting…';

  try {
    const res = await apiPostFormAuth('/api/candidates/submit', fd);
    if (closeAfter) {
      showAlert('ok', `Submitted successfully by ${res.submitted_by || 'you'}. Candidate ID: ${res.candidate_id || 'N/A'}`);
    } else {
      showAlert('ok', `Submitted successfully by ${res.submitted_by || 'you'}.`);
    }
    if (closeAfter) {
      setTimeout(() => closeSubmitModal(), 650);
    } else {
      // prepare for another submission
      els.candName.value = '';
      els.candEmail.value = '';
      els.candPhone.value = '';
      els.candBillRate.value = '';
      els.candLocation.value = '';
      els.candSkills.value = '';
      els.candJobTitle.value = '';
      els.candExperience.value = '';
      els.candStartDate.value = '';
      els.candRTO.value = '';
      els.candSummary.value = '';
      els.candResume.value = '';
    }
  } catch (e) {
    const detail = e.data ? JSON.stringify(e.data) : e.message;
    showAlert('error', `Submission failed: ${detail}`);
  } finally {
    if (els.submitAnotherBtn) {
      els.submitAnotherBtn.disabled = false;
      els.submitAnotherBtn.textContent = 'Submit & Add Another';
    }
    if (els.submitCloseBtn) {
      els.submitCloseBtn.disabled = false;
      els.submitCloseBtn.textContent = 'Submit & Close';
    }
  }
}

if (els.submitAnotherBtn) {
  els.submitAnotherBtn.addEventListener('click', () => submitResume({ closeAfter: false }));
}
if (els.submitCloseBtn) {
  els.submitCloseBtn.addEventListener('click', () => submitResume({ closeAfter: true }));
}

async function loadJobs() {
  els.jobsGrid.innerHTML = '<div class="loading-jobs">Loading jobs from Ceipal API...<br><small>Loading first batch...</small></div>';
  els.jobsEmpty.hidden = true;
  
  // Reset infinite scroll state
  nextStartPage = 26;
  hasMoreJobs = true;
  isLoadingMore = false;
  
  try {
    const data = await apiGet('/api/jobs');
    allJobs = data.jobs || [];
    renderJobs();
  } catch (e) {
    allJobs = [];
    renderJobs();
  }
}

async function loadMoreJobs() {
  if (isLoadingMore || !hasMoreJobs) return;
  
  isLoadingMore = true;
  showLoadingSpinner();
  
  try {
    const data = await apiGet(`/api/jobs/load-more?start_page=${nextStartPage}&max_pages=25`);
    const newJobs = data.jobs || [];
    
    if (newJobs.length > 0) {
      allJobs = [...allJobs, ...newJobs];
      nextStartPage += 25;
      renderJobs();
    } else {
      hasMoreJobs = false;
      hideLoadingSpinner();
    }
  } catch (e) {
    console.error('Failed to load more jobs:', e);
    hasMoreJobs = false;
    hideLoadingSpinner();
  } finally {
    isLoadingMore = false;
  }
}

function showLoadingSpinner() {
  let spinner = document.getElementById('loadMoreSpinner');
  if (!spinner) {
    spinner = document.createElement('div');
    spinner.id = 'loadMoreSpinner';
    spinner.className = 'loading-spinner';
    spinner.innerHTML = '<div class="spinner"></div><span>Loading more jobs...</span>';
    spinner.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:10px;padding:20px;color:#6b7280;';
    els.jobsGrid.appendChild(spinner);
  }
  spinner.style.display = 'flex';
}

function hideLoadingSpinner() {
  const spinner = document.getElementById('loadMoreSpinner');
  if (spinner) spinner.style.display = 'none';
}

async function loadCeipalStatus() {
  try {
    const data = await apiGet('/api/ceipal/test');
    if (data.status === 'success') setStatus('ok', 'Ceipal connected');
    else setStatus('bad', `Ceipal failed: ${data.detail || data.message || 'unknown'}`);
  } catch (e) {
    setStatus('bad', 'Backend offline');
  }
}

function openSystemView(text) {
  setView('settings');
  els.systemJson.textContent = text;
}

async function loadSystem() {
  try {
    const cache = await apiGet('/api/ceipal/cache');
    els.systemJson.textContent = JSON.stringify(cache, null, 2);
  } catch (e) {
    els.systemJson.textContent = e.data ? JSON.stringify(e.data, null, 2) : e.message;
  }
}

async function loadSubmissions() {
  try {
    const data = await apiGetAuth('/api/candidates');
    const items = data.candidates || [];
    if (!items.length) {
      els.submissionsTable.innerHTML = '<div class="empty">No submissions yet.</div>';
      return;
    }

    const isUserAdmin = isAdmin();
    
    const rows = items.map(c => `
      <tr data-candidate='${JSON.stringify(c).replace(/'/g, "\\'")}'>
        <td>${c.id || ''}</td>
        <td><a href="#" class="candidate-name-link" style="color: #7c3aed; text-decoration: underline; cursor: pointer;">${c.name || ''}</a></td>
        <td>${c.email || ''}</td>
        <td>${c.phone || ''}</td>
        <td>${c.job_id || ''}</td>
        ${isUserAdmin ? `<td><a href="#" class="vendor-name-link" data-vendor='${JSON.stringify(c.submitted_by || {}).replace(/'/g, "\\'")}' style="color: #7c3aed; text-decoration: underline; cursor: pointer;">${c.submitted_by?.full_name || c.submitted_by_user_id || 'Unknown'}</a></td>` : ''}
        <td>${c.submitted_date || ''}</td>
        <td>${c.status || ''}</td>
        <td><button class="btn btn--secondary view-resume-btn" data-path="${c.resume_path || ''}">View Resume</button></td>
      </tr>
    `).join('');
    
    els.submissionsTable.innerHTML = `
      <div class="panel">
        <div class="panel__title">Submissions (${items.length})</div>
        <div class="panel__desc">${isUserAdmin ? 'All submissions by all vendors.' : 'Your submissions only.'}</div>
        <div class="code table-container">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Job</th>
                ${isUserAdmin ? '<th>Submitted By</th>' : ''}
                <th>Submitted</th>
                <th>Status</th>
                <th>Resume</th>
              </tr>
            </thead>
            <tbody>
              ${rows}
            </tbody>
          </table>
        </div>
      </div>
    `;
    
    // Add event listeners for view resume buttons
    document.querySelectorAll('.view-resume-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const candidateId = e.target.closest('tr').querySelector('td').textContent;
        if (candidateId) {
          // Open the resume download endpoint
          window.open(`${API_BASE}/api/resumes/${candidateId}`, '_blank');
        }
      });
    });
    
    // Add event listeners for candidate name clicks (show candidate details)
    document.querySelectorAll('.candidate-name-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const row = e.target.closest('tr');
        const candidateData = JSON.parse(row.dataset.candidate);
        showCandidateDetailsModal(candidateData);
      });
    });
    
    // Add event listeners for vendor name clicks (show vendor details) - admin only
    document.querySelectorAll('.vendor-name-link').forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const vendorData = JSON.parse(e.target.dataset.vendor);
        showVendorDetailsModal(vendorData);
      });
    });
  } catch (e) {
    els.submissionsTable.innerHTML = `<div class="empty">Failed to load submissions.</div>`;
  }
}

async function loadOffers() {
  // Placeholder for offers data
  els.offersTable.innerHTML = '<div class="empty">Offers feature coming soon. Track candidate offers sent to clients.</div>';
}

async function loadDeclines() {
  // Placeholder for declines data
  els.declinesTable.innerHTML = '<div class="empty">Declines feature coming soon. Track declined offers and rejections.</div>';
}

async function loadStarts() {
  // Placeholder for starts data
  els.startsTable.innerHTML = '<div class="empty">Starts feature coming soon. Track candidates who have started working.</div>';
}

function showCandidateDetailsModal(candidate) {
  const details = `
    <div style="margin-bottom: 16px;"><strong>ID:</strong> ${candidate.id || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Name:</strong> ${candidate.name || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Email:</strong> ${candidate.email || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Phone:</strong> ${candidate.phone || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Job ID:</strong> ${candidate.job_id || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Job Title:</strong> ${candidate.job_title || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Primary Skills:</strong> ${candidate.skills || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Location:</strong> ${candidate.location || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Experience:</strong> ${candidate.experience || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Bill Rate:</strong> ${candidate.bill_rate || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Tentative Start Date:</strong> ${candidate.tentative_start_date || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>RTO (Return to Office):</strong> ${candidate.rto || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Candidate Summary:</strong> ${candidate.candidate_summary || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Status:</strong> ${candidate.status || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Submitted Date:</strong> ${candidate.submitted_date || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Resume:</strong> <button class="btn btn--secondary" onclick="window.open('${API_BASE}/api/resumes/${candidate.id}', '_blank')">View Resume</button></div>
  `;
  
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'candidateDetailModal';
  modal.innerHTML = `
    <div class="modal__overlay" onclick="closeCandidateDetailModal()"></div>
    <div class="modal__content" style="max-width: 600px; max-height: 80vh; overflow-y: auto;">
      <div class="modal__header">
        <h3>Candidate Details</h3>
        <button class="modal__close" onclick="closeCandidateDetailModal()">&times;</button>
      </div>
      <div class="modal__body" style="padding: 20px;">
        ${details}
      </div>
      <div class="modal__footer" style="padding: 16px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end;">
        <button class="btn btn--secondary" onclick="closeCandidateDetailModal()">Close</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

function closeCandidateDetailModal() {
  const modal = document.getElementById('candidateDetailModal');
  if (modal) modal.remove();
}

function showVendorDetailsModal(vendor) {
  const details = `
    <div style="margin-bottom: 16px;"><strong>Name:</strong> ${vendor.full_name || vendor.name || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>Email:</strong> ${vendor.email || 'N/A'}</div>
    <div style="margin-bottom: 16px;"><strong>ID:</strong> ${vendor.id || 'N/A'}</div>
  `;
  
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'vendorDetailModal';
  modal.innerHTML = `
    <div class="modal__overlay" onclick="closeVendorDetailModal()"></div>
    <div class="modal__content" style="max-width: 400px;">
      <div class="modal__header">
        <h3>Vendor Details</h3>
        <button class="modal__close" onclick="closeVendorDetailModal()">&times;</button>
      </div>
      <div class="modal__body" style="padding: 20px;">
        ${details}
      </div>
      <div class="modal__footer" style="padding: 16px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end;">
        <button class="btn btn--secondary" onclick="closeVendorDetailModal()">Close</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

function closeVendorDetailModal() {
  const modal = document.getElementById('vendorDetailModal');
  if (modal) modal.remove();
}

// UI events
els.searchInput.addEventListener('input', renderJobs);
els.statusFilter.addEventListener('change', renderJobs);
els.refreshBtn.addEventListener('click', async () => {
  await loadCeipalStatus();
  await loadJobs();
});
els.ceipalCacheBtn.addEventListener('click', async () => {
  try {
    const cache = await apiGet('/api/ceipal/cache');
    openSystemView(JSON.stringify(cache, null, 2));
  } catch (e) {
    openSystemView(e.data ? JSON.stringify(e.data, null, 2) : e.message);
  }
});

if (els.ceipalTestBtn) {
  els.ceipalTestBtn.addEventListener('click', async () => {
    try {
      const res = await apiGet('/api/ceipal/test');
      els.systemJson.textContent = JSON.stringify(res, null, 2);
      await loadCeipalStatus();
    } catch (e) {
      els.systemJson.textContent = e.data ? JSON.stringify(e.data, null, 2) : e.message;
    }
  });
}

if (els.openDocsBtn) {
  els.openDocsBtn.addEventListener('click', () => window.open(`${API_BASE}/docs`, '_blank'));
}

// Nav
document.querySelectorAll('.nav__item').forEach(btn => {
  btn.addEventListener('click', () => setView(btn.dataset.view));
});

if (els.logoutBtn) {
  els.logoutBtn.addEventListener('click', logout);
}

if (els.authSubmitBtn) {
  els.authSubmitBtn.addEventListener('click', handleAuthSubmit);
}

if (els.authToggleBtn) {
  els.authToggleBtn.addEventListener('click', toggleAuthMode);
}

// Infinite scroll - detect when user reaches bottom of page
window.addEventListener('scroll', () => {
  if (!hasMoreJobs || isLoadingMore) return;
  
  const scrollTop = window.scrollY || document.documentElement.scrollTop;
  const windowHeight = window.innerHeight;
  const documentHeight = document.documentElement.scrollHeight;
  
  // Load more when user scrolls to within 200px of bottom
  if (scrollTop + windowHeight >= documentHeight - 200) {
    loadMoreJobs();
  }
});

// init
(async function init() {
  updateAuthUI();
  
  if (authToken && currentUser) {
    // Already logged in
    setView('jobs');
    await loadCeipalStatus();
    await loadJobs();
  } else {
    // Not logged in - show auth view
    setView('auth');
  }
})();
