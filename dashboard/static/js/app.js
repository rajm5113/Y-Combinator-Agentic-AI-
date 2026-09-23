/**
 * YC Founder Outreach Dashboard — Main Application Logic
 * Pure Vanilla JavaScript SPA with 3s Polling and Human-in-the-Loop Review.
 */

const state = {
  currentView: 'overview',
  currentLeadId: null,
  leadIds: [], // array of outreach_ids currently in view for sequential next/prev
  currentLeadIndex: -1,
  filters: {
    status: '',
    tier: '',
    batch: '',
    q: '',
    page: 1,
    per_page: 25,
  },
  stats: null,
  pipelinePollTimer: null,
  activeLeadDetail: null,
};

// ─── Initialization ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupNavigation();
  setupFilterListeners();
  setupPipelineForm();
  setupBlacklistForm();
  loadStats();
  loadBatches();
  checkHealth();
  setInterval(checkHealth, 15000);
  loadLastPipelineConfig();
});

// ─── Toast Notifications ──────────────────────────────────────
function showToast(message, type = 'info', duration = 3200) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

// ─── Copy to Clipboard ─────────────────────────────────────────
async function copyToClipboard(text, label = 'Message') {
  if (!text) {
    showToast(`No ${label} content to copy`, 'warning');
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    showToast(`✓ ${label} copied to clipboard!`, 'success');
  } catch (err) {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast(`✓ ${label} copied to clipboard!`, 'success');
  }
}

// ─── Navigation ───
function setupNavigation() {
  document.querySelectorAll('[data-view-target]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-view-target');
      switchView(target);
    });
  });
}

function switchView(viewName) {
  state.currentView = viewName;

  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  const targetView = document.getElementById(`view-${viewName}`);
  if (targetView) targetView.classList.add('active');

  document.querySelectorAll('.nav-item button').forEach((btn) => {
    if (btn.getAttribute('data-view-target') === viewName) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  if (viewName === 'overview') {
    loadStats();
  } else if (viewName === 'leads') {
    loadLeads();
  } else if (viewName === 'blacklist') {
    loadBlacklist();
  } else if (viewName === 'pipeline') {
    checkPipelineStatus();
    loadLastPipelineConfig();
    loadPipelineHistory();
    loadLogs();
    loadBackups();
  }
}

// ─── Overview & Stats ──────────────────────────────────────────
async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) throw new Error('Failed to fetch stats');
    const data = await res.json();
    state.stats = data;

    // Update Nav review counter
    const navCounter = document.getElementById('nav-review-counter');
    if (navCounter) {
      const count = data.needs_review || (data.outreach_draft + (data.outreach_review || 0));
      navCounter.textContent = count;
      navCounter.style.display = count > 0 ? 'inline-block' : 'none';
    }

    // Update metric cards
    setText('metric-startups', data.total_startups);
    setText('metric-founders', data.total_founders);
    setText('metric-evaluated', data.total_evaluated);
    setText('metric-fit-pending', data.fit_pending || 0);
    setText('metric-high-fit', data.fit_high);
    setText('metric-medium-fit', data.fit_medium);
    setText('metric-low-fit', data.fit_low || 0);
    setText('metric-drafts', data.total_drafts);
    setText('metric-needs-review', data.needs_review || data.outreach_draft);
    setText('metric-approved', data.outreach_approved);
    setText('metric-sent', data.outreach_sent);
    setText('metric-replied', data.outreach_replied);
    setText('metric-blacklisted', data.blacklisted);

    // Overview review banner
    const ovBanner = document.getElementById('overview-review-banner');
    if (ovBanner) {
      const reviewCount = data.needs_review || data.outreach_draft;
      if (reviewCount > 0) {
        ovBanner.style.display = 'flex';
        setText('overview-review-count', reviewCount);
      } else {
        ovBanner.style.display = 'none';
      }
    }
  } catch (err) {
    console.error('loadStats error:', err);
  }
}

async function loadBatches() {
  try {
    const res = await fetch('/api/batches');
    if (!res.ok) return;
    const batches = await res.json();

    const select = document.getElementById('filter-batch');
    if (select) {
      select.innerHTML = '<option value="">All Batches</option>';
      batches.forEach((b) => {
        select.innerHTML += `<option value="${escapeHtml(b.batch)}">${escapeHtml(b.batch)} (${b.count})</option>`;
      });
    }

    const pipeBatch = document.getElementById('pipeline-batch-select');
    if (pipeBatch) {
      const current = pipeBatch.value || 'Fall 2026';
      pipeBatch.innerHTML = '';
      batches.forEach((b) => {
        pipeBatch.innerHTML += `<option value="${escapeHtml(b.batch)}">${escapeHtml(b.batch)} (${b.count} stored)</option>`;
      });
      pipeBatch.value = batches.some((b) => b.batch === current) ? current : (batches[0]?.batch || 'Fall 2026');
    }
  } catch (err) {
    console.error('loadBatches error:', err);
  }
}

// ─── Leads List & Filters ──────────────────────────────────────
function setupFilterListeners() {
  // Status tab buttons
  document.querySelectorAll('[data-status-filter]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-status-filter]').forEach((b) => b.classList.remove('btn-primary'));
      document.querySelectorAll('[data-status-filter]').forEach((b) => b.classList.add('btn-secondary'));
      btn.classList.remove('btn-secondary');
      btn.classList.add('btn-primary');

      state.filters.status = btn.getAttribute('data-status-filter');
      state.filters.page = 1;
      loadLeads();
    });
  });

  const tierSelect = document.getElementById('filter-tier');
  if (tierSelect) {
    tierSelect.addEventListener('change', (e) => {
      state.filters.tier = e.target.value;
      state.filters.page = 1;
      loadLeads();
    });
  }

  const batchSelect = document.getElementById('filter-batch');
  if (batchSelect) {
    batchSelect.addEventListener('change', (e) => {
      state.filters.batch = e.target.value;
      state.filters.page = 1;
      loadLeads();
    });
  }

  const searchInput = document.getElementById('filter-search');
  if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        state.filters.q = e.target.value.trim();
        state.filters.page = 1;
        loadLeads();
      }, 300);
    });
  }

  const prevBtn = document.getElementById('leads-prev-page');
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (state.filters.page > 1) {
        state.filters.page--;
        loadLeads();
      }
    });
  }

  const nextBtn = document.getElementById('leads-next-page');
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      state.filters.page++;
      loadLeads();
    });
  }
}

async function loadLeads() {
  const tableBody = document.getElementById('leads-table-body');
  if (!tableBody) return;

  tableBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">Loading leads...</td></tr>';

  try {
    const params = new URLSearchParams();
    if (state.filters.status) params.set('status', state.filters.status);
    if (state.filters.tier) params.set('tier', state.filters.tier);
    if (state.filters.batch) params.set('batch', state.filters.batch);
    if (state.filters.q) params.set('q', state.filters.q);
    params.set('page', state.filters.page);
    params.set('per_page', state.filters.per_page);

    const res = await fetch(`/api/leads?${params.toString()}`);
    if (!res.ok) throw new Error('Failed to load leads');
    const data = await res.json();

    state.leadIds = data.leads.map((l) => l.outreach_id);

    // Update banner in leads view
    const leadsBanner = document.getElementById('leads-review-banner');
    if (leadsBanner) {
      if (state.filters.status === 'pending_review' || state.filters.status === 'draft') {
        leadsBanner.style.display = 'flex';
        setText('leads-review-count', data.total);
      } else {
        leadsBanner.style.display = 'none';
      }
    }

    if (data.leads.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 3rem;">No leads found matching current filters.</td></tr>`;
      setText('leads-page-info', `Page ${data.page} of 1 (0 leads)`);
      return;
    }

    tableBody.innerHTML = data.leads.map((lead) => {
      const tierClass = lead.fit_tier === 'HIGH' ? 'badge-high' : lead.fit_tier === 'MEDIUM' ? 'badge-medium' : 'badge-low';
      const statusClass = `badge-${lead.outreach_status}`;
      return `
        <tr>
          <td>
            <strong>${escapeHtml(lead.startup_name)}</strong>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(lead.batch)}</div>
            <div style="font-size: 0.72rem; color: var(--text-secondary);">${escapeHtml([lead.primary_location_city, lead.primary_location_country].filter(Boolean).join(', ') || 'Location not harvested')}</div>
          </td>
          <td>
            <div>${escapeHtml(lead.founder_name)}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(lead.founder_title || 'Founder')}</div>
          </td>
          <td>
            <span class="badge ${tierClass}">${lead.fit_score} ${escapeHtml(lead.fit_tier)}</span>
          </td>
          <td>
            <span class="badge badge-status ${statusClass}">${escapeHtml(lead.outreach_status)}</span>
          </td>
          <td>
            ${lead.has_drafts ? '<span style="color: var(--accent-success); font-size: 0.8rem;">✓ 3 Channels</span>' : '<span style="color: var(--text-muted); font-size: 0.8rem;">Not generated</span>'}
          </td>
          <td>
            <button class="btn btn-sm btn-primary" onclick="openLeadDetail(${lead.outreach_id})">
              Review Lead →
            </button>
          </td>
        </tr>
      `;
    }).join('');

    const totalPages = Math.ceil(data.total / data.per_page) || 1;
    setText('leads-page-info', `Page ${data.page} of ${totalPages} (${data.total} total leads)`);

    const prevBtn = document.getElementById('leads-prev-page');
    if (prevBtn) prevBtn.disabled = data.page <= 1;

    const nextBtn = document.getElementById('leads-next-page');
    if (nextBtn) nextBtn.disabled = data.page >= totalPages;

  } catch (err) {
    tableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--accent-danger); padding: 2rem;">Error: ${err.message}</td></tr>`;
  }
}

function startSequentialReview() {
  if (state.leadIds.length > 0) {
    openLeadDetail(state.leadIds[0]);
  } else {
    showToast('No leads available in current view', 'warning');
  }
}

// ─── Lead Detail & Human Review View ───────────────────────────
async function openLeadDetail(outreachId) {
  state.currentLeadId = outreachId;
  state.currentLeadIndex = state.leadIds.indexOf(outreachId);

  switchView('detail');
  const detailContainer = document.getElementById('lead-detail-content');
  detailContainer.innerHTML = '<div style="text-align: center; padding: 4rem; color: var(--text-muted);">Loading lead dossier...</div>';

  // Update Next/Prev buttons
  updateSequentialNavButtons();

  try {
    const res = await fetch(`/api/leads/${outreachId}`);
    if (!res.ok) throw new Error('Could not load lead detail');
    const lead = await res.json();
    state.activeLeadDetail = lead;
    renderLeadDetail(lead);
  } catch (err) {
    detailContainer.innerHTML = `<div class="card" style="color: var(--accent-danger);">Failed to load lead: ${err.message}</div>`;
  }
}

function updateSequentialNavButtons() {
  const prevBtn = document.getElementById('detail-nav-prev');
  const nextBtn = document.getElementById('detail-nav-next');
  const counter = document.getElementById('detail-nav-counter');

  if (state.currentLeadIndex >= 0 && state.leadIds.length > 0) {
    if (prevBtn) prevBtn.disabled = state.currentLeadIndex <= 0;
    if (nextBtn) nextBtn.disabled = state.currentLeadIndex >= state.leadIds.length - 1;
    if (counter) counter.textContent = `Lead ${state.currentLeadIndex + 1} of ${state.leadIds.length}`;
  } else {
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    if (counter) counter.textContent = '';
  }
}

function navigateSequential(direction) {
  const newIndex = state.currentLeadIndex + direction;
  if (newIndex >= 0 && newIndex < state.leadIds.length) {
    openLeadDetail(state.leadIds[newIndex]);
  }
}

function renderLeadDetail(lead) {
  const container = document.getElementById('lead-detail-content');
  if (!container) return;

  const tierClass = lead.fit_tier === 'HIGH' ? 'badge-high' : lead.fit_tier === 'MEDIUM' ? 'badge-medium' : 'badge-low';
  const status = lead.outreach_status || 'draft';
  const linkedinChars = (lead.linkedin_note || '').length;

  // Build the Human-in-the-Loop Action Banner
  let flowBannerHtml = '';
  if (status === 'discovered') {
    flowBannerHtml = `
      <div class="flow-banner discovered">
        <div class="flow-title">
          <span>🔎 Status: Discovered — Awaiting Qualification</span>
          <span class="badge badge-status badge-discovered">Discovered</span>
        </div>
        <div class="flow-desc">This founder was discovered and saved to the lead queue. Fit evaluation or message drafting may not be complete yet. You can inspect the company and founder dossier now. Message generation remains gated on fit qualification.</div>
        <div class="flow-actions">
          <button class="btn btn-outline" style="color: var(--accent-danger);" onclick="confirmBlacklistCompany()">🚫 Blacklist Company</button>
        </div>
      </div>
    `;
  } else if (status === 'draft' || status === 'review') {
    flowBannerHtml = `
      <div class="flow-banner draft">
        <div class="flow-title">
          <span>⚡ Status: Needs Human Review</span>
          <span class="badge badge-status badge-draft">Draft</span>
        </div>
        <div class="flow-desc">Inspect company fit and message drafts. Edit or regenerate if needed, then approve to unlock manual sending.</div>
        <div class="flow-actions">
          <button class="btn btn-success" onclick="updateLeadStatus('approved')">✓ Approve Lead</button>
          <button class="btn btn-secondary" onclick="updateLeadStatus('rejected')">✗ Reject Lead</button>
          <button class="btn btn-outline" style="color: var(--accent-danger);" onclick="confirmBlacklistCompany()">🚫 Blacklist Company</button>
        </div>
      </div>
    `;
  } else if (status === 'approved') {
    flowBannerHtml = `
      <div class="flow-banner approved">
        <div class="flow-title">
          <span>✓ Status: Approved — Ready for Manual Delivery</span>
          <span class="badge badge-status badge-approved">Approved</span>
        </div>
        <div class="flow-desc">
          <strong>Next Action:</strong> You manually deliver the message to the founder via LinkedIn or Email.
          Click below to open LinkedIn or copy the note, send it, then mark as sent.
        </div>
        <div class="flow-actions">
          ${lead.linkedin_url ? `<a href="${escapeHtml(lead.linkedin_url)}" target="_blank" class="btn btn-primary">↗ Open Founder's LinkedIn</a>` : ''}
          <button class="btn btn-secondary" onclick="copyToClipboard(document.getElementById('edit-linkedin-note').value, 'LinkedIn Note')">📋 Copy LinkedIn Note</button>
          <button class="btn btn-secondary" onclick="copyToClipboard('Subject: ' + document.getElementById('edit-cold-email-subject').value + '\\n\\n' + document.getElementById('edit-cold-email-body').value, 'Cold Email')">📋 Copy Cold Email</button>
          <button class="btn btn-success" onclick="updateLeadStatus('sent')">🚀 Mark as Sent</button>
          <button class="btn btn-outline btn-sm" onclick="updateLeadStatus('draft')">Revert to Draft</button>
        </div>
      </div>
    `;
  } else if (status === 'sent') {
    flowBannerHtml = `
      <div class="flow-banner sent">
        <div class="flow-title">
          <span>🚀 Status: Sent — Awaiting Reply</span>
          <span class="badge badge-status badge-sent">Sent</span>
        </div>
        <div class="flow-desc">Outreach was manually sent. When the founder replies, mark it here to log conversion.</div>
        <div class="flow-actions">
          <button class="btn btn-primary" onclick="updateLeadStatus('replied')">💬 Mark as Replied</button>
          <button class="btn btn-outline btn-sm" onclick="updateLeadStatus('approved')">Back to Approved</button>
        </div>
      </div>
    `;
  } else if (status === 'replied') {
    flowBannerHtml = `
      <div class="flow-banner replied">
        <div class="flow-title">
          <span>🎉 Status: Founder Replied!</span>
          <span class="badge badge-status badge-replied">Replied</span>
        </div>
        <div class="flow-desc">Conversion logged! Check your inbox or LinkedIn conversation with ${escapeHtml(lead.founder_name)}.</div>
      </div>
    `;
  } else if (status === 'rejected') {
    flowBannerHtml = `
      <div class="flow-banner rejected">
        <div class="flow-title">
          <span>✗ Status: Rejected</span>
          <span class="badge badge-status badge-rejected">Rejected</span>
        </div>
        <div class="flow-desc">This lead was marked as not suitable for outreach.</div>
        <div class="flow-actions">
          <button class="btn btn-secondary btn-sm" onclick="updateLeadStatus('draft')">Re-open as Draft</button>
        </div>
      </div>
    `;
  } else if (status === 'blacklisted') {
    flowBannerHtml = `
      <div class="flow-banner" style="border-left-color: #000;">
        <div class="flow-title">
          <span>🚫 Status: Blacklisted</span>
          <span class="badge badge-status badge-blacklisted">Blacklisted</span>
        </div>
        <div class="flow-desc">This company or founder is on the permanent 'Never Contact Again' blacklist.</div>
      </div>
    `;
  }

  // Tags & Jobs HTML
  const tagsHtml = (lead.tags || []).map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join(' ');
  const jobsHtml = (lead.jobs_data || []).map((j) => {
    const title = j.title || j.role || 'Open Role';
    const loc = j.location ? ` • ${j.location}` : '';
    return `<div style="font-size: 0.8rem; padding: 0.35rem 0; border-bottom: 1px solid var(--border-subtle); color: var(--text-secondary);">${escapeHtml(title)}${escapeHtml(loc)}</div>`;
  }).join('') || '<div style="font-size: 0.8rem; color: var(--text-muted);">No open jobs parsed</div>';

  // Match rationale bullets
  const rationaleHtml = (lead.match_rationale || []).map((r) => `<li style="margin-bottom: 0.35rem;">${escapeHtml(r)}</li>`).join('') || '<li>No specific match points recorded</li>';

  container.innerHTML = `
    <!-- Top Flow Banner -->
    ${flowBannerHtml}

    <div class="detail-grid">
      <!-- Left Column: Company & Founder Dossier -->
      <div>
        <!-- Startup Card -->
        <div class="card">
          <div class="card-header">
            <div>
              <h2 style="font-size: 1.35rem; font-weight: 700;">${escapeHtml(lead.startup_name)}</h2>
              <span class="tag" style="margin-top: 0.25rem;">${escapeHtml(lead.batch)}</span>
              ${lead.website ? `<a href="${escapeHtml(lead.website)}" target="_blank" style="font-size: 0.8rem; color: var(--accent-primary); margin-left: 0.5rem; text-decoration: none;">Visit Website ↗</a>` : ''}
            </div>
            <span class="badge ${tierClass}">${lead.fit_score} / 100</span>
          </div>

          <p style="font-size: 0.95rem; font-weight: 500; color: var(--text-primary); margin-bottom: 0.75rem;">
            ${escapeHtml(lead.one_liner || 'No one-liner provided')}
          </p>

          <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 1rem;">
            <strong>Current location:</strong>
            ${escapeHtml([lead.primary_location_city, lead.primary_location_country].filter(Boolean).join(', ') || 'Location not harvested')}
          </div>

          <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1rem; max-height: 120px; overflow-y: auto;">
            ${escapeHtml(lead.long_description || '')}
          </p>

          <div style="margin-bottom: 1rem;">
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Tags & Industry</div>
            <div>${tagsHtml || '<span style="color: var(--text-muted); font-size: 0.8rem;">None</span>'}</div>
          </div>
          <div style="margin-bottom: 1rem;">
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Employment Location</div>
            <div style="font-size: 0.85rem; color: var(--text-secondary);">
              <strong>${lead.employment_location_verified ? 'Verified employment signal' : 'Not yet verified'}</strong>
              <div>Current office: ${escapeHtml((lead.office_locations || []).map(l => [l.city, l.country].filter(Boolean).join(', ')).filter(Boolean).join(' • ') || 'Not found')}</div>
              <div>Active jobs: ${escapeHtml((lead.job_locations || []).map(l => [l.city, l.country].filter(Boolean).join(', ')).filter(Boolean).join(' • ') || 'Not found')}</div>
              <div>YC profile location: ${escapeHtml((lead.yc_profile_locations || []).map(l => [l.city, l.country].filter(Boolean).join(', ')).filter(Boolean).join(' • ') || 'Not listed')}</div>
            </div>
          </div>


          <div>
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Open Jobs (${(lead.jobs_data || []).length})</div>
            ${jobsHtml}
          </div>
        </div>

        <!-- Founder Card -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Founder Dossier</h3>
            ${lead.linkedin_url ? `<a href="${escapeHtml(lead.linkedin_url)}" target="_blank" class="btn btn-sm btn-outline">LinkedIn ↗</a>` : ''}
          </div>

          <div style="font-size: 1.1rem; font-weight: 700; color: var(--text-primary);">${escapeHtml(lead.founder_name)}</div>
          <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.75rem;">${escapeHtml(lead.founder_title || 'Co-founder')}</div>

          <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.75rem;">
            ${escapeHtml(lead.founder_bio || 'No founder bio available')}
          </p>
        </div>

        <!-- Fit Evaluation Card -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Fit & Angle Rationale</h3>
            <span class="badge ${tierClass}">${escapeHtml(lead.fit_tier)} FIT</span>
          </div>

          <div style="margin-bottom: 1rem;">
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Match Rationales</div>
            <ul style="font-size: 0.85rem; color: var(--text-secondary); padding-left: 1.25rem;">
              ${rationaleHtml}
            </ul>
          </div>

          <div style="margin-bottom: 1rem;">
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Location Evidence</div>
            ${(lead.location_evidence || []).slice(0, 5).map(ev => `
              <div style="font-size: 0.78rem; color: var(--text-secondary); padding: 0.3rem 0; border-bottom: 1px solid var(--border-subtle);">
                <strong>${escapeHtml(ev.kind || ev.source || 'Source')}</strong>
                ${ev.url ? ` — <a href="${escapeHtml(ev.url)}" target="_blank" style="color: var(--accent-primary);">source ↗</a>` : ''}
                ${ev.snippet ? `<div style="color: var(--text-muted); margin-top: 0.2rem;">${escapeHtml(ev.snippet)}</div>` : ''}
              </div>
            `).join('') || '<span style="color: var(--text-muted); font-size: 0.8rem;">No current office evidence captured yet.</span>'}
          </div>

          <div>
            <div style="font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.35rem;">Recommended Contribution Angle</div>
            <div style="font-size: 0.85rem; color: #93c5fd; background: rgba(59, 130, 246, 0.1); padding: 0.75rem; border-radius: var(--radius-sm); border-left: 3px solid var(--accent-primary);">
              ${escapeHtml(lead.contribution_angle || 'Standard infrastructure & founder acceleration')}
            </div>
          </div>
        </div>
      </div>

      <!-- Right Column: Multi-Channel Message Drafts & Per-Channel Regenerate -->
      <div>
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Multi-Channel Outreach Drafts</h3>
            <button class="btn btn-sm btn-outline" onclick="regenerateChannel('all')">
              🔄 Regenerate All
            </button>
          </div>

          <!-- Channel 1: LinkedIn Note -->
          <div class="channel-box">
            <div class="channel-header">
              <span class="channel-name">
                <span style="color: #38bdf8;">■</span> LinkedIn Connection Note
              </span>
              <span id="linkedin-counter" class="char-counter ${linkedinChars > 300 ? 'danger' : linkedinChars > 280 ? 'warning' : ''}">
                ${linkedinChars} / 300 chars
              </span>
            </div>
            <textarea
              id="edit-linkedin-note"
              class="channel-textarea"
              maxlength="300"
              oninput="handleLinkedInInput(this)"
            >${escapeHtml(lead.linkedin_note || '')}</textarea>
            <div class="channel-actions">
              <button class="btn btn-sm btn-secondary" onclick="saveDraftField('linkedin_note')">Save Edit</button>
              <button class="btn btn-sm btn-outline" onclick="regenerateChannel('linkedin_note')">🔄 Regenerate</button>
              <button class="btn btn-sm btn-primary" onclick="copyToClipboard(document.getElementById('edit-linkedin-note').value, 'LinkedIn Note')">📋 Copy</button>
            </div>
          </div>

          <!-- Channel 2: YC Job Note -->
          <div class="channel-box">
            <div class="channel-header">
              <span class="channel-name">
                <span style="color: #f97316;">■</span> YC Startup Job Note
              </span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">~120-150 words</span>
            </div>
            <textarea
              id="edit-yc-job-note"
              class="channel-textarea"
              style="min-height: 120px;"
            >${escapeHtml(lead.yc_job_note || '')}</textarea>
            <div class="channel-actions">
              <button class="btn btn-sm btn-secondary" onclick="saveDraftField('yc_job_note')">Save Edit</button>
              <button class="btn btn-sm btn-outline" onclick="regenerateChannel('yc_job_note')">🔄 Regenerate</button>
              <button class="btn btn-sm btn-primary" onclick="copyToClipboard(document.getElementById('edit-yc-job-note').value, 'YC Job Note')">📋 Copy</button>
            </div>
          </div>

          <!-- Channel 3: Cold Email -->
          <div class="channel-box">
            <div class="channel-header">
              <span class="channel-name">
                <span style="color: #a855f7;">■</span> Cold Email
              </span>
              <span style="font-size: 0.75rem; color: var(--text-muted);">Subject + 3 Paragraphs</span>
            </div>
            <div style="margin-bottom: 0.5rem;">
              <label style="font-size: 0.75rem; color: var(--text-muted); display: block; margin-bottom: 0.25rem;">Subject Line (< 8 words)</label>
              <input
                id="edit-cold-email-subject"
                type="text"
                class="text-input"
                value="${escapeHtml(lead.cold_email_subject || '')}"
              />
            </div>
            <div>
              <label style="font-size: 0.75rem; color: var(--text-muted); display: block; margin-bottom: 0.25rem;">Email Body</label>
              <textarea
                id="edit-cold-email-body"
                class="channel-textarea"
                style="min-height: 180px;"
              >${escapeHtml(lead.cold_email_body || '')}</textarea>
            </div>
            <div class="channel-actions">
              <button class="btn btn-sm btn-secondary" onclick="saveColdEmailDraft()">Save Edit</button>
              <button class="btn btn-sm btn-outline" onclick="regenerateChannel('cold_email')">🔄 Regenerate</button>
              <button class="btn btn-sm btn-primary" onclick="copyToClipboard('Subject: ' + document.getElementById('edit-cold-email-subject').value + '\\n\\n' + document.getElementById('edit-cold-email-body').value, 'Cold Email')">📋 Copy Email</button>
            </div>
          </div>

          <!-- Reviewer Internal Notes -->
          <div style="margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid var(--border-subtle);">
            <label style="font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">Reviewer Notes / Feedback</label>
            <textarea
              id="edit-lead-notes"
              class="channel-textarea"
              style="min-height: 60px;"
              placeholder="Add internal feedback or rationale..."
            >${escapeHtml(lead.notes || '')}</textarea>
            <div style="display: flex; justify-content: flex-end; margin-top: 0.5rem;">
              <button class="btn btn-sm btn-secondary" onclick="saveReviewerNotes()">Save Notes</button>
            </div>
          </div>

        </div>
      </div>
    </div>
  `;
}

function handleLinkedInInput(textarea) {
  const count = textarea.value.length;
  const counter = document.getElementById('linkedin-counter');
  if (!counter) return;

  counter.textContent = `${count} / 300 chars`;
  counter.classList.remove('warning', 'danger');
  if (count > 300) {
    counter.classList.add('danger');
  } else if (count > 280) {
    counter.classList.add('warning');
  }
}

// ─── Draft Saving & Updating ───────────────────────────────────
async function saveDraftField(field) {
  if (!state.currentLeadId) return;

  let value = '';
  if (field === 'linkedin_note') {
    value = document.getElementById('edit-linkedin-note').value;
    if (value.length > 300) {
      showToast('LinkedIn note exceeds 300 characters limit', 'error');
      return;
    }
  } else if (field === 'yc_job_note') {
    value = document.getElementById('edit-yc-job-note').value;
  }

  try {
    const res = await fetch(`/api/leads/${state.currentLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    });
    if (!res.ok) throw new Error('Save failed');
    showToast(`✓ ${field.replace('_', ' ')} saved`, 'success');
  } catch (err) {
    showToast(`Failed to save: ${err.message}`, 'error');
  }
}

async function saveColdEmailDraft() {
  if (!state.currentLeadId) return;
  const subject = document.getElementById('edit-cold-email-subject').value;
  const body = document.getElementById('edit-cold-email-body').value;

  try {
    const res = await fetch(`/api/leads/${state.currentLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cold_email_subject: subject,
        cold_email_body: body,
      }),
    });
    if (!res.ok) throw new Error('Save failed');
    showToast('✓ Cold email saved', 'success');
  } catch (err) {
    showToast(`Failed to save: ${err.message}`, 'error');
  }
}

async function saveReviewerNotes() {
  if (!state.currentLeadId) return;
  const notes = document.getElementById('edit-lead-notes').value;

  try {
    const res = await fetch(`/api/leads/${state.currentLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes }),
    });
    if (!res.ok) throw new Error('Save failed');
    showToast('✓ Notes saved', 'success');
  } catch (err) {
    showToast(`Failed to save notes: ${err.message}`, 'error');
  }
}

// ─── Per-Channel Message Regeneration ──────────────────────────
async function regenerateChannel(channel = 'all') {
  if (!state.currentLeadId) return;

  const label = channel === 'all' ? 'all channels' : channel.replace('_', ' ');
  showToast(`Regenerating ${label}... please wait`, 'info', 4000);

  try {
    const res = await fetch(`/api/leads/${state.currentLeadId}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Regeneration failed');
    }

    const updated = await res.json();
    state.activeLeadDetail = updated;
    renderLeadDetail(updated);
    showToast(`✓ Regenerated ${label} successfully!`, 'success');
  } catch (err) {
    showToast(`Regeneration failed: ${err.message}`, 'error');
  }
}

// ─── Lead Status State Machine ─────────────────────────────────
async function updateLeadStatus(newStatus) {
  if (!state.currentLeadId) return;

  const notesEl = document.getElementById('edit-lead-notes');
  const notes = notesEl ? notesEl.value : '';

  try {
    const res = await fetch(`/api/leads/${state.currentLeadId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus, notes }),
    });
    if (!res.ok) throw new Error('Status update failed');

    showToast(`✓ Status updated to ${newStatus.toUpperCase()}`, 'success');

    // Reload active lead detail
    const detailRes = await fetch(`/api/leads/${state.currentLeadId}`);
    if (detailRes.ok) {
      const lead = await detailRes.json();
      state.activeLeadDetail = lead;
      renderLeadDetail(lead);
    }

    // Refresh overview metrics in background
    loadStats();
  } catch (err) {
    showToast(`Failed to update status: ${err.message}`, 'error');
  }
}

function confirmBlacklistCompany() {
  if (!state.activeLeadDetail) return;
  const company = state.activeLeadDetail.startup_name;
  if (confirm(`Are you sure you want to permanently blacklist "${company}" from all outreach?`)) {
    updateLeadStatus('blacklisted');
  }
}

// ─── Blacklist Management ──────────────────────────────────────
async function loadBlacklist() {
  const tbody = document.getElementById('blacklist-table-body');
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">Loading blacklist...</td></tr>';

  try {
    const res = await fetch('/api/blacklist');
    if (!res.ok) throw new Error('Failed to fetch blacklist');
    const entries = await res.json();

    if (entries.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">No entries on blacklist.</td></tr>';
      return;
    }

    tbody.innerHTML = entries.map((e) => `
      <tr>
        <td><span class="tag">${escapeHtml(e.identifier_type)}</span></td>
        <td><strong>${escapeHtml(e.identifier_value)}</strong></td>
        <td style="color: var(--text-secondary);">${escapeHtml(e.reason)}</td>
        <td style="color: var(--text-muted); font-size: 0.75rem;">${escapeHtml(e.created_at || '')}</td>
        <td>
          <button class="btn btn-sm btn-outline" style="color: var(--accent-danger);" onclick="removeBlacklistEntry(${e.id})">
            Remove
          </button>
        </td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--accent-danger); padding: 2rem;">Error: ${err.message}</td></tr>`;
  }
}

async function removeBlacklistEntry(id) {
  if (!confirm('Remove this entry from the blacklist?')) return;
  try {
    const res = await fetch(`/api/blacklist/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete blacklist entry');
    showToast('✓ Removed from blacklist', 'success');
    loadBlacklist();
    loadStats();
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  }
}

function setupBlacklistForm() {
  const form = document.getElementById('blacklist-add-form');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const type = document.getElementById('bl-type').value;
    const value = document.getElementById('bl-value').value.trim();
    const reason = document.getElementById('bl-reason').value.trim();

    if (!value || !reason) {
      showToast('Please provide value and reason', 'warning');
      return;
    }

    try {
      const res = await fetch('/api/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          identifier_type: type,
          identifier_value: value,
          reason: reason,
        }),
      });
      if (!res.ok) throw new Error('Failed to add to blacklist');
      showToast('✓ Added to blacklist', 'success');
      form.reset();
      loadBlacklist();
      loadStats();
    } catch (err) {
      showToast(`Error: ${err.message}`, 'error');
    }
  });
}

// ─── Pipeline Trigger & 1s Polling ─────────────────────────────
function setupPipelineForm() {
  const form = document.getElementById('pipeline-run-form');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const batch = document.getElementById('pipeline-batch-select').value;
    const limit = parseInt(document.getElementById('pipeline-limit').value, 10) || 5;
    const minScore = parseInt(document.getElementById('pipeline-min-score').value, 10) || 50;
    const concurrency = parseInt(document.getElementById('pipeline-concurrency').value, 10) || 5;
    const dryRun = document.getElementById('pipeline-dry-run').checked;

    // Immediate visual deflection on button click
    const statusBadge = document.getElementById('pipeline-status-badge');
    const progressText = document.getElementById('pipeline-progress-text');
    const pbar = document.getElementById('pipeline-progress-bar');
    const runBtn = document.getElementById('btn-run-pipeline');
    const cancelBtn = document.getElementById('btn-cancel-pipeline');

    if (statusBadge) {
      statusBadge.textContent = 'RUNNING';
      statusBadge.className = 'badge badge-warning';
    }
    if (progressText) progressText.textContent = dryRun ? 'Triggering Discovery (Dry Run)...' : 'Starting Pipeline Run...';
    if (pbar) pbar.classList.add('indeterminate');
    if (runBtn) runBtn.disabled = true;
    if (cancelBtn) cancelBtn.style.display = 'inline-block';

    try {
      const res = await fetch('/api/pipeline/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          batches: [batch],
          limit: limit,
          min_fit_score: minScore,
          max_concurrency: concurrency,
          dry_run: dryRun,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Pipeline trigger failed');
      }

      showToast('🚀 Pipeline started in background!', 'success');
      startPipelinePolling();
    } catch (err) {
      if (statusBadge) {
        statusBadge.textContent = 'IDLE';
        statusBadge.className = 'badge badge-secondary';
      }
      if (pbar) pbar.classList.remove('indeterminate');
      if (runBtn) runBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';
      showToast(`Error: ${err.message}`, 'error');
    }
  });
}

function startPipelinePolling() {
  if (state.pipelinePollTimer) clearInterval(state.pipelinePollTimer);
  setTimeout(checkPipelineStatus, 200);
  // Fast 1-second polling interval for responsive feedback
  state.pipelinePollTimer = setInterval(checkPipelineStatus, 1000);
}

async function checkPipelineStatus() {
  try {
    const res = await fetch('/api/pipeline/status');
    if (!res.ok) return;
    const data = await res.json();

    const statusBadge = document.getElementById('pipeline-status-badge');
    const progressText = document.getElementById('pipeline-progress-text');
    const pbar = document.getElementById('pipeline-progress-bar');
    const reportCard = document.getElementById('pipeline-last-report');
    const runBtn = document.getElementById('btn-run-pipeline');
    const cancelBtn = document.getElementById('btn-cancel-pipeline');

    if (data.is_running) {
      if (statusBadge) {
        statusBadge.textContent = 'RUNNING';
        statusBadge.className = 'badge badge-warning';
      }
      if (progressText) progressText.textContent = data.progress || 'Executing stages...';
      if (pbar) pbar.classList.add('indeterminate');
      if (runBtn) runBtn.disabled = true;
      if (cancelBtn) cancelBtn.style.display = 'inline-block';
      loadLogs();
    } else {
      const progLower = (data.progress || '').toLowerCase();
      if (statusBadge) {
        if (progLower.includes('fail') || progLower.includes('error')) {
          statusBadge.textContent = 'FAILED';
          statusBadge.className = 'badge badge-danger';
        } else if (progLower.includes('cancel')) {
          statusBadge.textContent = 'CANCELLED';
          statusBadge.className = 'badge badge-warning';
        } else if (progLower.includes('complete') || progLower.includes('finish') || progLower.includes('success')) {
          statusBadge.textContent = 'COMPLETED';
          statusBadge.className = 'badge badge-success';
        } else {
          statusBadge.textContent = 'IDLE';
          statusBadge.className = 'badge badge-secondary';
        }
      }
      if (progressText) progressText.textContent = data.progress || 'No pipeline run active';
      if (pbar) pbar.classList.remove('indeterminate');
      if (runBtn) runBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';

      // If finished, stop polling
      if (state.pipelinePollTimer) {
        clearInterval(state.pipelinePollTimer);
        state.pipelinePollTimer = null;
        loadStats();
        loadLastPipelineConfig();
        loadPipelineHistory();
        loadLogs();
      }
    }

    if (data.last_report && reportCard) {
      reportCard.style.display = 'block';
      const rep = data.last_report;
      setText('report-duration', `${(rep.total_duration_seconds || 0).toFixed(1)}s`);
      setText('report-discovered', rep.total_startups_discovered || 0);
      setText('report-founders', rep.total_founders_extracted || 0);
      setText('report-evaluated', rep.total_fit_evaluated || 0);
      setText('report-location-matched', rep.total_location_matched || 0);
      setText('report-qualified', rep.total_qualified || 0);
      setText('report-drafts', rep.total_drafts_generated || 0);
    }
  } catch (err) {
    console.error('checkPipelineStatus error:', err);
  }
}

// ─── Phase 6: System Health Check ──────────────────────────────
async function checkHealth() {
  const dot = document.getElementById('health-dot');
  const text = document.getElementById('health-text');
  const badge = document.getElementById('nav-health-status');
  if (!dot || !text) return;

  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('Health check request failed');
    const data = await res.json();

    dot.className = `health-dot dot-${data.status}`;
    text.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);

    const compInfo = Object.entries(data.components || {})
      .map(([k, v]) => `${k.toUpperCase()}: ${v.status} (${v.latency_ms ? v.latency_ms.toFixed(1) + 'ms' : 'N/A'})`)
      .join('\n');
    badge.title = `Overall Status: ${data.status}\n${compInfo}`;
  } catch (err) {
    dot.className = 'health-dot dot-unhealthy';
    text.textContent = 'Degraded';
    badge.title = `Health check failed: ${err.message}`;
  }
}

// ─── Phase 6: Remember Last Config & Quick Run Again ───────────
async function loadLastPipelineConfig() {
  const card = document.getElementById('quick-run-card');
  const details = document.getElementById('quick-run-details');
  if (!card || !details) return;

  try {
    const res = await fetch('/api/pipeline/last-config');
    if (!res.ok) return;
    const config = await res.json();
    state.lastPipelineConfig = config;

    if (config.batch || config.started_at) {
      card.style.display = 'flex';
      const batch = config.batch || 'Fall 2026';
      const limit = config.startup_limit || 5;
      const minScore = config.min_fit_score !== undefined ? config.min_fit_score : 50;
      const concurrency = config.max_concurrency || 5;
      const dryRun = config.dry_run ? ' [Dry Run]' : '';
      details.textContent = `Batch: ${batch} • India / All cities • Priority: Bengaluru → Delhi NCR → Gurugram • Limit: ${limit} • Min Score: ${minScore} • Concurrency: ${concurrency}${dryRun}`;
    }
  } catch (err) {
    console.debug('Failed to load last pipeline config:', err);
  }
}

async function triggerQuickRunAgain() {
  if (!state.lastPipelineConfig) {
    showToast('No previous configuration found', 'warning');
    return;
  }
  const config = state.lastPipelineConfig;
  showToast('⚡ Launching 1-click rerun with last configuration...', 'info');

  // Immediate visual deflection
  const statusBadge = document.getElementById('pipeline-status-badge');
  const progressText = document.getElementById('pipeline-progress-text');
  const pbar = document.getElementById('pipeline-progress-bar');
  const runBtn = document.getElementById('btn-run-pipeline');
  const cancelBtn = document.getElementById('btn-cancel-pipeline');

  if (statusBadge) {
    statusBadge.textContent = 'RUNNING';
    statusBadge.className = 'badge badge-warning';
  }
  if (progressText) progressText.textContent = config.dry_run ? 'Re-running Discovery (Dry Run)...' : 'Re-running Full Pipeline...';
  if (pbar) pbar.classList.add('indeterminate');
  if (runBtn) runBtn.disabled = true;
  if (cancelBtn) cancelBtn.style.display = 'inline-block';

  try {
    const res = await fetch('/api/pipeline/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        batches: [config.batch || 'Fall 2026'],
        limit: config.startup_limit || 5,
        min_fit_score: config.min_fit_score || 50,
        max_concurrency: config.max_concurrency || 5,
        dry_run: !!config.dry_run,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Pipeline trigger failed');
    }
    showToast('🚀 Pipeline re-run started in background!', 'success');
    startPipelinePolling();
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  }
}

// ─── Phase 6: Cancellation ─────────────────────────────────────
async function cancelPipelineRun() {
  if (!confirm('Are you sure you want to stop the active pipeline execution?')) return;
  try {
    const res = await fetch('/api/pipeline/cancel', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      showToast('⏹ Cancellation signal sent. Stopping pipeline...', 'info');
      checkPipelineStatus();
    } else {
      showToast(data.message || 'No pipeline running', 'warning');
    }
  } catch (err) {
    showToast(`Failed to cancel: ${err.message}`, 'error');
  }
}

// ─── Phase 6: Run History ──────────────────────────────────────
async function loadPipelineHistory() {
  const tbody = document.getElementById('pipeline-history-body');
  if (!tbody) return;

  try {
    const res = await fetch('/api/pipeline/history?limit=15');
    if (!res.ok) return;
    const history = await res.json();

    if (!history || history.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">No execution history recorded yet.</td></tr>';
      return;
    }

    tbody.innerHTML = history.map((item) => {
      const stats = item.stats_json || {};
      const statsStr = `D:${stats.discovered || 0} F:${stats.fit_evaluated || 0}/${(stats.fit_evaluated || 0) + (stats.fit_failed || 0)} Q:${stats.qualified || 0} M:${stats.drafts || 0}`;
      const statusClass = item.status === 'completed' ? 'badge-success' : (item.status === 'cancelled' ? 'badge-warning' : (item.status === 'running' ? 'badge-warning' : 'badge-danger'));
      const duration = item.duration_seconds ? `${item.duration_seconds.toFixed(1)}s` : '-';
      const started = item.started_at ? item.started_at.split('.')[0].replace('T', ' ') : '-';
      return `
        <tr>
          <td><code style="font-size: 0.75rem; background: var(--bg-surface-elevated); padding: 0.2rem 0.4rem; border-radius: 4px;">${escapeHtml(item.session_id)}</code></td>
          <td><strong>${escapeHtml(item.batch)}</strong></td>
          <td style="font-size: 0.8rem; color: var(--text-secondary);">Limit ${item.startup_limit} • Min ${item.min_fit_score}</td>
          <td><span class="badge ${statusClass}">${escapeHtml(item.status.toUpperCase())}</span></td>
          <td style="font-size: 0.8rem;">${duration}</td>
          <td style="font-size: 0.8rem; color: var(--text-secondary);">${statsStr}</td>
          <td style="font-size: 0.75rem; color: var(--text-muted);">${started}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('loadPipelineHistory error:', err);
  }
}

// ─── Phase 6: Live Logging Terminal ────────────────────────────
async function loadLogs() {
  const terminal = document.getElementById('log-terminal');
  if (!terminal) return;

  const levelSelect = document.getElementById('log-level-filter');
  const level = levelSelect ? levelSelect.value : '';
  const url = level ? `/api/logs?limit=80&level=${encodeURIComponent(level)}` : '/api/logs?limit=80';

  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    const logs = data.logs || [];

    if (logs.length === 0) {
      terminal.innerHTML = '<div class="log-terminal-placeholder">No logs matching filter.</div>';
      return;
    }

    terminal.innerHTML = logs.map((log) => {
      const time = log.timestamp ? log.timestamp.split('T')[1]?.split('.')[0] || log.timestamp : '';
      return `
        <div class="log-entry">
          <span class="log-timestamp">[${time}]</span>
          <span class="log-level ${log.level}">${log.level}</span>
          <span class="log-msg">${escapeHtml(log.message)}</span>
        </div>
      `;
    }).join('');

    terminal.scrollTop = terminal.scrollHeight;
  } catch (err) {
    console.debug('loadLogs error:', err);
  }
}

// ─── Phase 6: Database Backups & Snapshots ──────────────────────
async function createBackup() {
  showToast('💾 Creating database snapshot...', 'info');
  try {
    const res = await fetch('/api/backup', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      showToast(`✓ Snapshot saved: ${data.filename}`, 'success');
      loadBackups();
    } else {
      showToast(`Backup failed: ${data.error || data.message}`, 'error');
    }
  } catch (err) {
    showToast(`Backup error: ${err.message}`, 'error');
  }
}

async function loadBackups() {
  const tbody = document.getElementById('backups-table-body');
  if (!tbody) return;

  try {
    const res = await fetch('/api/backups');
    if (!res.ok) return;
    const backups = await res.json();

    if (!backups || backups.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">No backups created yet.</td></tr>';
      return;
    }

    tbody.innerHTML = backups.map((b) => {
      const counts = b.table_counts || {};
      const countsStr = Object.entries(counts).map(([k, v]) => `${k}:${v}`).join(' | ');
      const sizeKb = (b.size_bytes / 1024).toFixed(1) + ' KB';
      const created = b.created_at ? b.created_at.split('.')[0].replace('T', ' ') : '-';
      return `
        <tr>
          <td><strong style="font-size: 0.85rem;">${escapeHtml(b.filename)}</strong></td>
          <td style="font-size: 0.75rem; color: var(--text-muted);">${created}</td>
          <td style="font-size: 0.8rem;">${sizeKb}</td>
          <td style="font-size: 0.8rem; color: var(--text-secondary);">${escapeHtml(countsStr)}</td>
          <td>
            <button class="btn btn-sm btn-outline" onclick="restoreBackup('${escapeHtml(b.filename)}')">
              Restore
            </button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('loadBackups error:', err);
  }
}

async function restoreBackup(filename) {
  if (!confirm(`Are you sure you want to restore snapshot "${filename}"?\nWARNING: Current table rows will be replaced with snapshot data.`)) return;
  showToast(`Restoring ${filename}...`, 'info');

  try {
    const res = await fetch('/api/backups/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    });
    const data = await res.json();
    if (data.success) {
      showToast('✓ Database restored successfully!', 'success');
      loadStats();
      loadPipelineHistory();
      loadBackups();
    } else {
      showToast(`Restore failed: ${data.error || data.detail}`, 'error');
    }
  } catch (err) {
    showToast(`Restore error: ${err.message}`, 'error');
  }
}

// ─── Export Helper ─────────────────────────────────────────────
function exportLeads(format) {
  const minScore = document.getElementById('export-min-score')?.value || 50;
  window.location.href = `/api/export?format=${format}&min_score=${minScore}`;
}

// ─── DOM Utility Helpers ───────────────────────────────────────
function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
