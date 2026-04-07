const API_BASE = window.location.origin;

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
  viewSettings: document.getElementById('viewSettings'),
  submissionsTable: document.getElementById('submissionsTable'),
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

  els.viewJobs.hidden = view !== 'jobs';
  els.viewSubmissions.hidden = view !== 'submissions';
  els.viewSettings.hidden = view !== 'settings';

  if (view === 'jobs') {
    els.pageTitle.textContent = 'Jobs';
    els.pageSubtitle.textContent = 'Browse open roles and submit resumes per job.';
  }
  if (view === 'submissions') {
    els.pageTitle.textContent = 'Submissions';
    els.pageSubtitle.textContent = 'Recent candidate submissions.';
    loadSubmissions();
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
  
  els.jobDetailModal.hidden = false;
  loadJobDetailCandidates(activeJobId);
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
  const rto = els.candRTO.value;
  const summary = els.candSummary.value.trim();
  const file = els.candResume.files && els.candResume.files[0];

  if (!activeJobId) return showAlert('error', 'No job selected.');
  if (!name) return showAlert('error', 'Candidate name is required.');
  if (!email) return showAlert('error', 'Email is required.');
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
    const res = await apiPostForm('/api/candidates/submit', fd);
    showAlert('ok', `Submitted successfully. Candidate ID: ${res.candidate_id || 'N/A'}`);
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
  els.jobsGrid.innerHTML = '<div class="loading-jobs">Loading jobs from Ceipal API...<br><small>This may take up to a minute on first load</small></div>';
  els.jobsEmpty.hidden = true;
  try {
    const data = await apiGet('/api/jobs');
    allJobs = data.jobs || [];
    renderJobs();
  } catch (e) {
    allJobs = [];
    renderJobs();
  }
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
    const data = await apiGet('/api/candidates');
    const items = data.candidates || [];
    if (!items.length) {
      els.submissionsTable.innerHTML = '<div class="empty">No submissions yet.</div>';
      return;
    }

    const rows = items.map(c => `
      <tr>
        <td>${c.id || ''}</td>
        <td>${c.name || ''}</td>
        <td>${c.email || ''}</td>
        <td>${c.phone || ''}</td>
        <td>${c.job_id || ''}</td>
        <td>${c.submitted_date || ''}</td>
        <td>${c.status || ''}</td>
        <td><button class="btn btn--secondary view-resume-btn" data-path="${c.resume_path || ''}">View Resume</button></td>
      </tr>
    `).join('');

    els.submissionsTable.innerHTML = `
      <div class="panel">
        <div class="panel__title">Submissions (${items.length})</div>
        <div class="panel__desc">Latest candidate entries captured by the backend. Click "View Resume" to open the resume file.</div>
        <div class="code table-container">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Job</th>
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
  } catch (e) {
    els.submissionsTable.innerHTML = `<div class="empty">Failed to load submissions.</div>`;
  }
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

// init
(async function init() {
  setView('jobs');
  await loadCeipalStatus();
  await loadJobs();
})();
