/**
 * app.js — Audit Intelligence Platform frontend
 *
 * Security: All user-derived content is rendered via textContent / DOM APIs,
 *           NEVER via innerHTML. HTML is only used for safe static templates.
 *
 * XSS-safe helper:
 *   esc(str) → HTML-escaped string for insertion into innerHTML where needed.
 */

'use strict';

// ── XSS-safe escape ──────────────────────────────────────────────────────────
function esc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#x27;');
}

// ── State ────────────────────────────────────────────────────────────────────
const state = {
    sessionId:   null,
    currentAudit: null,
    analyzing:   false,
    startTime:   null,
    etaTimer:    null,
};

// Pipeline step definitions (stage → label, emoji, weight for progress bar)
const STEPS = [
    { id: 'searching',       label: 'Searching Screener.in',    icon: '🔎', w: 5  },
    { id: 'found',           label: 'Company found',             icon: '✅', w: 5  },
    { id: 'finding_reports', label: 'Finding annual reports',    icon: '📂', w: 10 },
    { id: 'reports_found',   label: 'Reports located',           icon: '✅', w: 5  },
    { id: 'downloading',     label: 'Downloading PDF',           icon: '⬇️', w: 20 },
    { id: 'downloaded',      label: 'PDF ready',                 icon: '✅', w: 5  },
    { id: 'ingesting',       label: 'Indexing document',         icon: '🧠', w: 20 },
    { id: 'ingested',        label: 'Document indexed',          icon: '✅', w: 5  },
    { id: 'summarizing',     label: 'Building company profile',  icon: '📊', w: 5  },
    { id: 'extracting',      label: 'Extracting audit report',   icon: '⚙️', w: 20 },
];

const TOTAL_W = STEPS.reduce((s, x) => s + x.w, 0);


// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    loadHistory();
    loadStats();

    // Keyboard bindings
    document.getElementById('searchInput').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !state.analyzing) startAnalysis();
    });
    document.getElementById('qaInput').addEventListener('keydown', e => {
        if (e.key === 'Enter') askQuestion();
    });
});


// ── Health check ─────────────────────────────────────────────────────────────
async function checkHealth() {
    const dot  = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    try {
        const res  = await fetch('/api/health');
        const data = await res.json();
        if (data.status === 'ok') {
            dot.className  = 'status-dot ok';
            text.textContent = 'System Ready';
        } else {
            dot.className  = 'status-dot warn';
            text.textContent = 'API key missing';
        }
    } catch {
        dot.className  = 'status-dot error';
        text.textContent = 'Server offline';
    }
}


// ── View switching ────────────────────────────────────────────────────────────
function switchView(view) {
    document.querySelectorAll('.view').forEach(v => {
        v.hidden = true;
        v.classList.remove('active');
    });
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

    const viewEl = document.getElementById(`view${view.charAt(0).toUpperCase() + view.slice(1)}`);
    const navBtn = document.getElementById(`nav${view.charAt(0).toUpperCase() + view.slice(1)}`);

    if (viewEl) { viewEl.hidden = false; viewEl.classList.add('active'); }
    if (navBtn) navBtn.classList.add('active');

    if (view === 'history') renderHistoryTable();
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}


// ── Stats ─────────────────────────────────────────────────────────────────────
async function loadStats() {
    try {
        const res  = await fetch('/api/stats');
        const json = await res.json();
        if (json.status === 'success') {
            document.getElementById('statTotalN').textContent  = json.data.complete ?? '—';
            document.getElementById('statUniqueN').textContent = json.data.unique_companies ?? '—';
        }
    } catch { /* non-critical */ }
}


// ── History ───────────────────────────────────────────────────────────────────
let _historyData = [];

async function loadHistory() {
    try {
        const res  = await fetch('/api/history?limit=30');
        const json = await res.json();
        _historyData = json.history || [];
        renderHistorySidebar(_historyData);
    } catch { /* non-critical */ }
}

function renderHistorySidebar(items) {
    const list = document.getElementById('historyList');
    if (!items.length) {
        list.innerHTML = '';
        const empty = document.createElement('div');
        empty.className = 'history-empty';
        empty.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/>
            </svg>`;
        const t = document.createElement('span');
        t.textContent = 'No analyses yet';
        empty.appendChild(t);
        list.appendChild(empty);
        return;
    }

    list.innerHTML = '';
    items.slice(0, 15).forEach(item => {
        const div = document.createElement('div');
        div.className = 'history-item';
        div.setAttribute('role', 'button');
        div.setAttribute('tabindex', '0');
        div.setAttribute('aria-label', `Load analysis for ${item.company_name}`);

        const icon = document.createElement('div');
        icon.className = 'hi-icon';
        icon.textContent = '📊';

        const body = document.createElement('div');
        body.className = 'hi-body';

        const name = document.createElement('div');
        name.className = 'hi-name';
        name.textContent = item.company_name;

        const date = document.createElement('div');
        date.className = 'hi-date';
        date.textContent = formatDate(item.created_at);

        body.appendChild(name);
        body.appendChild(date);
        div.appendChild(icon);
        div.appendChild(body);

        div.addEventListener('click', () => loadPastAnalysis(item.id));
        div.addEventListener('keydown', e => { if (e.key === 'Enter') loadPastAnalysis(item.id); });
        list.appendChild(div);
    });
}

function renderHistoryTable() {
    const wrap = document.getElementById('historyTableWrap');
    if (!_historyData.length) {
        wrap.innerHTML = '';
        const p = document.createElement('p');
        p.style.cssText = 'padding:32px;color:var(--c-muted);text-align:center';
        p.textContent = 'No analyses yet. Search for a company to begin.';
        wrap.appendChild(p);
        return;
    }

    const table = document.createElement('table');
    table.className = 'h-table history-table-wrap';

    const thead = document.createElement('thead');
    thead.innerHTML = `<tr>
        <th>#</th><th>Company</th><th>Report</th><th>Status</th><th>Date</th><th></th>
    </tr>`;
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    _historyData.forEach(item => {
        const tr = document.createElement('tr');

        const idTd = document.createElement('td');
        idTd.textContent = item.id;

        const nameTd = document.createElement('td');
        nameTd.style.fontWeight = '500';
        nameTd.textContent = item.company_name;

        const reportTd = document.createElement('td');
        reportTd.style.cssText = 'max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--c-text2)';
        reportTd.textContent = item.report_title || '—';

        const statusTd = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `status-pill ${item.status === 'complete' ? 'ok' : 'warn'}`;
        badge.textContent = item.status === 'complete' ? '✓ Done' : item.status;
        statusTd.appendChild(badge);

        const dateTd = document.createElement('td');
        dateTd.style.color = 'var(--c-muted)';
        dateTd.textContent = formatDate(item.created_at);

        const actionTd = document.createElement('td');
        const btn = document.createElement('button');
        btn.className = 'h-table-btn';
        btn.textContent = 'Load';
        btn.addEventListener('click', () => {
            loadPastAnalysis(item.id);
            switchView('analyze');
        });
        actionTd.appendChild(btn);

        tr.replaceChildren(idTd, nameTd, reportTd, statusTd, dateTd, actionTd);
        tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    wrap.innerHTML = '';
    wrap.appendChild(table);
}

async function loadPastAnalysis(id) {
    try {
        const res  = await fetch(`/api/analysis/${id}`);
        const json = await res.json();
        if (json.status === 'success' && json.data && json.data.result_json) {
            const result = JSON.parse(json.data.result_json);
            renderResults({
                company:      { name: json.data.company_name },
                audit_report: result,
                summary:      {},
                session_id:   null,
            });
            switchView('analyze');

            // Highlight active in sidebar
            document.querySelectorAll('.history-item').forEach((el, i) => {
                el.classList.toggle('active', _historyData[i]?.id === id);
            });
        } else {
            showError('Could not load analysis data');
        }
    } catch (err) {
        showError('Failed to load analysis: ' + err.message);
    }
}


// ── Analysis ──────────────────────────────────────────────────────────────────
function quickSearch(name) {
    document.getElementById('searchInput').value = name;
    startAnalysis();
}

function startAnalysis() {
    if (state.analyzing) return;

    const input   = document.getElementById('searchInput');
    const company = input.value.trim();
    if (!company) {
        input.focus();
        showError('Please enter a company name');
        return;
    }

    state.analyzing = true;
    state.startTime = Date.now();

    hideError();
    hideResults();
    hideQA();
    showPipeline(company);
    setBtnLoading(true);
    renderSteps();
    startEtaTimer();

    fetch('/api/analyze', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ company }),
    })
    .then(resp => {
        if (!resp.ok && !resp.body) {
            return resp.json().then(d => { throw new Error(d.error || 'Request failed'); });
        }
        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        function read() {
            reader.read().then(({ done, value }) => {
                if (done) { finishAnalysis(); return; }

                buf += decoder.decode(value, { stream: true });
                const lines = buf.split('\n');
                buf = lines.pop();

                lines.forEach(line => {
                    if (!line.startsWith('data: ')) return;
                    try {
                        handleEvent(JSON.parse(line.slice(6)));
                    } catch { /* skip malformed */ }
                });

                read();
            }).catch(err => {
                finishAnalysis();
                showError('Stream error: ' + err.message);
            });
        }
        read();
    })
    .catch(err => {
        finishAnalysis();
        showError('Connection failed: ' + err.message);
    });
}

function finishAnalysis() {
    state.analyzing = false;
    setBtnLoading(false);
    stopEtaTimer();
}

function handleEvent(event) {
    if (event.type === 'progress') {
        updateStep(event.stage, event.message);
    } else if (event.type === 'result') {
        finishAnalysis();
        hidePipeline();
        renderResults(event.data);
        loadHistory();
        loadStats();
    } else if (event.type === 'error') {
        finishAnalysis();
        hidePipeline();
        showError(event.message);
    }
}


// ── Pipeline UI ───────────────────────────────────────────────────────────────
function showPipeline(company) {
    document.getElementById('pipelineSection').hidden = false;
    document.getElementById('pipelineCompany').textContent = `Analysing: ${company}`;
    document.getElementById('heroSection').style.opacity = '0.4';
}

function hidePipeline() {
    document.getElementById('pipelineSection').hidden = true;
    document.getElementById('heroSection').style.opacity = '1';
}

function renderSteps() {
    const container = document.getElementById('pipelineSteps');
    container.innerHTML = '';
    STEPS.forEach(step => {
        const div = document.createElement('div');
        div.className = 'ps-step';
        div.id = `step-${step.id}`;

        const icon = document.createElement('div');
        icon.className = 'ps-icon';
        icon.textContent = '○';

        const msg = document.createElement('div');
        msg.className = 'ps-msg';
        msg.textContent = step.label;

        const time = document.createElement('div');
        time.className = 'ps-time';
        time.id = `time-${step.id}`;

        div.appendChild(icon);
        div.appendChild(msg);
        div.appendChild(time);
        container.appendChild(div);
    });
    document.getElementById('pipelineBarFill').style.width = '0%';
}

function updateStep(stageId, message) {
    const allSteps  = document.querySelectorAll('.ps-step');
    let found       = false;
    let doneWeight  = 0;
    let currentIdx  = 0;

    STEPS.forEach((s, i) => {
        if (s.id === stageId) { currentIdx = i; }
    });

    allSteps.forEach((el, i) => {
        const stepDef = STEPS[i];
        if (!stepDef) return;

        if (stepDef.id === stageId) {
            found = true;
            el.className = 'ps-step active';
            el.querySelector('.ps-icon').innerHTML = '<div class="ps-spinner"></div>';
            el.querySelector('.ps-msg').textContent = message;
        } else if (!found) {
            if (el.className !== 'ps-step done') {
                el.className = 'ps-step done';
                el.querySelector('.ps-icon').textContent = '✓';
                el.querySelector('.ps-time').textContent =
                    `${((Date.now() - state.startTime) / 1000).toFixed(1)}s`;
            }
            doneWeight += (stepDef?.w || 0);
        }
    });

    // Update progress bar
    const pct = Math.min(95, Math.round((doneWeight / TOTAL_W) * 100));
    document.getElementById('pipelineBarFill').style.width = pct + '%';
}

// ETA timer
function startEtaTimer() {
    const etaEl = document.getElementById('pipelineEta');
    state.etaTimer = setInterval(() => {
        const elapsed = ((Date.now() - state.startTime) / 1000).toFixed(0);
        etaEl.textContent = `${elapsed}s elapsed · typical run: ~50s`;
    }, 1000);
}

function stopEtaTimer() {
    if (state.etaTimer) { clearInterval(state.etaTimer); state.etaTimer = null; }
}


// ── Results rendering ─────────────────────────────────────────────────────────
function renderResults(data) {
    state.currentAudit = data.audit_report || {};
    state.sessionId    = data.session_id || null;

    const audit   = state.currentAudit;
    const company = data.company || {};
    const summary = data.summary || {};

    // Header
    document.getElementById('resultsCompany').textContent =
        audit.company_name || company.name || 'Analysis Results';

    const metaEl = document.getElementById('resultsMeta');
    const metaParts = [];
    if (audit.financial_year_end) metaParts.push(`FY: ${audit.financial_year_end}`);
    if (audit.currency)           metaParts.push(audit.currency);
    if (summary.industry)         metaParts.push(summary.industry);
    metaEl.textContent = metaParts.join(' · ');

    // Build tabs
    buildOverviewTab(audit, summary);
    buildKAMTab(audit);
    buildDetailsTab(audit);
    buildSignatureTab(audit);

    document.getElementById('jsonPre').textContent =
        JSON.stringify(audit, null, 2);

    // Show results
    document.getElementById('resultsSection').hidden = false;

    if (state.sessionId) {
        document.getElementById('qaSection').hidden = false;
        document.getElementById('qaMessages').innerHTML = '';
    }

    // Jump to results
    document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function buildOverviewTab(audit, summary) {
    const panel = document.getElementById('tabOverview');
    panel.innerHTML = '';

    // Top stat cards
    const grid1 = mkEl('div', 'cards-grid');

    grid1.appendChild(infoCard('Company', audit.company_name || '—'));
    grid1.appendChild(infoCard('Financial Year', audit.financial_year_end || '—'));
    grid1.appendChild(infoCard('Report Type', audit.report_type || "Independent Auditor's Report"));

    if (summary.revenue) grid1.appendChild(infoCard('Revenue', summary.revenue));
    if (summary.auditor) grid1.appendChild(infoCard('Auditor', summary.auditor));

    panel.appendChild(grid1);

    // Opinion highlight card
    if (audit.auditor_opinion) {
        const op    = audit.auditor_opinion;
        const opType = (op.type || 'unknown').toLowerCase();
        const card  = mkEl('div', 'r-card full');
        const lbl   = mkEl('div', 'r-card-label');
        lbl.textContent = "Auditor's Opinion";

        const badge = mkEl('div', `opinion-badge ${opType}`);
        badge.textContent = `${opinionIcon(opType)} ${op.type || 'Unknown'}`;

        const desc = mkEl('div', 'r-card-desc');
        desc.textContent = op.summary || '';

        card.appendChild(lbl);
        card.appendChild(badge);
        card.appendChild(desc);

        const wrap = mkEl('div', 'cards-grid');
        wrap.appendChild(card);
        panel.appendChild(wrap);
    }

    // Basis for opinion
    if (audit.basis_for_opinion) {
        panel.appendChild(fullCard('Basis for Opinion', audit.basis_for_opinion));
    }

    // Status row — going concern + IFC + CARO
    const stGrid = mkEl('div', 'cards-grid');
    let hasStatus = false;

    if (audit.going_concern) {
        hasStatus = true;
        const gc    = audit.going_concern;
        const card  = mkEl('div', 'r-card');
        const lbl   = mkEl('div', 'r-card-label'); lbl.textContent = 'Going Concern';
        const pill  = mkEl('div', gc.material_uncertainty ? 'status-pill danger' : 'status-pill ok');
        pill.textContent = gc.material_uncertainty ? '⚠️ Material Uncertainty' : '✅ No Uncertainty';
        const desc  = mkEl('div', 'r-card-desc');
        if (gc.details) desc.textContent = gc.details;
        card.appendChild(lbl); card.appendChild(pill); card.appendChild(desc);
        stGrid.appendChild(card);
    }

    if (audit.internal_financial_controls) {
        hasStatus = true;
        const ifc   = audit.internal_financial_controls;
        const ifcT  = (ifc.opinion_type || '').toLowerCase();
        const card  = mkEl('div', 'r-card');
        const lbl   = mkEl('div', 'r-card-label'); lbl.textContent = 'Internal Financial Controls';
        const badge = mkEl('div', `opinion-badge ${ifcT}`); badge.textContent = ifc.opinion_type || '—';
        const desc  = mkEl('div', 'r-card-desc');
        if (ifc.summary) desc.textContent = ifc.summary;
        card.appendChild(lbl); card.appendChild(badge); card.appendChild(desc);
        stGrid.appendChild(card);
    }

    if (audit.caro_compliance) {
        hasStatus = true;
        const caro  = audit.caro_compliance;
        const card  = mkEl('div', 'r-card');
        const lbl   = mkEl('div', 'r-card-label'); lbl.textContent = 'CARO Compliance';
        const pill  = mkEl('div', caro.applicable ? 'status-pill ok' : 'status-pill');
        pill.textContent = caro.applicable ? '✅ Applicable' : '— Not Referenced';
        const desc  = mkEl('div', 'r-card-desc');
        if (caro.details) desc.textContent = caro.details;
        card.appendChild(lbl); card.appendChild(pill); card.appendChild(desc);
        stGrid.appendChild(card);
    }

    if (hasStatus) panel.appendChild(stGrid);

    // Summary highlights
    if (summary.key_highlights && summary.key_highlights.length) {
        const card = mkEl('div', 'r-card full');
        const lbl  = mkEl('div', 'r-card-label'); lbl.textContent = 'Key Highlights';
        const ul   = mkEl('ul', '');
        ul.style.cssText = 'padding-left:18px;margin-top:8px;display:flex;flex-direction:column;gap:4px';
        summary.key_highlights.forEach(h => {
            const li = document.createElement('li');
            li.style.cssText = 'font-size:13px;color:var(--c-text2);line-height:1.6';
            li.textContent = h;
            ul.appendChild(li);
        });
        card.appendChild(lbl); card.appendChild(ul);
        const w = mkEl('div', 'cards-grid'); w.appendChild(card);
        panel.appendChild(w);
    }
}

function buildKAMTab(audit) {
    const panel = document.getElementById('tabKam');
    panel.innerHTML = '';

    const kams = audit.key_audit_matters || [];
    if (!kams.length) {
        const p = mkEl('p', '');
        p.style.cssText = 'color:var(--c-muted);padding:24px';
        p.textContent = 'No Key Audit Matters identified in the extracted data.';
        panel.appendChild(p);
        return;
    }

    const list = mkEl('div', 'kam-list');
    kams.forEach((kam, i) => {
        const item  = mkEl('div', 'kam-item');

        const num   = mkEl('div', 'kam-num');
        num.textContent = i + 1;

        const title = mkEl('div', 'kam-title');
        title.textContent = kam.title || 'Key Audit Matter';

        item.appendChild(num);
        item.appendChild(title);

        if (kam.description) {
            const sec = mkEl('div', 'kam-section');
            const lbl = mkEl('div', 'kam-section-label'); lbl.textContent = 'Why It Was Key';
            const txt = mkEl('div', 'kam-section-text');  txt.textContent = kam.description;
            sec.appendChild(lbl); sec.appendChild(txt);
            item.appendChild(sec);
        }

        if (kam.audit_response) {
            const sec = mkEl('div', 'kam-section');
            const lbl = mkEl('div', 'kam-section-label'); lbl.textContent = 'Audit Response';
            const txt = mkEl('div', 'kam-section-text');  txt.textContent = kam.audit_response;
            sec.appendChild(lbl); sec.appendChild(txt);
            item.appendChild(sec);
        }

        list.appendChild(item);
    });
    panel.appendChild(list);
}

function buildDetailsTab(audit) {
    const panel = document.getElementById('tabDetails');
    panel.innerHTML = '';

    const fields = [
        ['Emphasis of Matter',      audit.emphasis_of_matter],
        ['Other Matter',            audit.other_matter],
        ['Other Information',       audit.other_information],
        ["Management's Responsibilities", audit.management_responsibilities],
        ["Auditor's Responsibilities",    audit.auditor_responsibilities],
        ['Other Legal Requirements',      audit.other_legal_requirements],
    ];

    const grid = mkEl('div', 'cards-grid');
    let added = false;
    fields.forEach(([label, value]) => {
        if (!value) return;
        added = true;
        const card = mkEl('div', 'r-card');
        const lbl  = mkEl('div', 'r-card-label'); lbl.textContent = label;
        const desc = mkEl('div', 'r-card-desc');  desc.textContent = value;
        card.appendChild(lbl); card.appendChild(desc);
        grid.appendChild(card);
    });

    if (added) {
        panel.appendChild(grid);
    } else {
        const p = mkEl('p', '');
        p.style.cssText = 'color:var(--c-muted);padding:24px';
        p.textContent = 'No additional details extracted.';
        panel.appendChild(p);
    }
}

function buildSignatureTab(audit) {
    const panel = document.getElementById('tabSignature');
    panel.innerHTML = '';

    const sig = audit.signature_block;
    if (!sig) {
        const p = mkEl('p', '');
        p.style.cssText = 'color:var(--c-muted);padding:24px';
        p.textContent = 'Signature block not found in the extracted data.';
        panel.appendChild(p);
        return;
    }

    const card  = mkEl('div', 'r-card full');
    const lbl   = mkEl('div', 'r-card-label'); lbl.textContent = 'Signature Block';
    const grid  = mkEl('div', 'sig-grid');

    const sigFields = [
        ['Audit Firm',         sig.audit_firm],
        ['FRN',                sig.firm_registration_number],
        ['Signing Partner',    sig.partner_name],
        ['Membership No.',     sig.membership_number],
        ['UDIN',               sig.udin],
        ['Report Date',        sig.report_date],
        ['Place',              sig.place],
    ];

    sigFields.forEach(([fl, fv]) => {
        if (!fv) return;
        const field = mkEl('div', 'sig-field');
        const flEl  = mkEl('div', 'sig-label'); flEl.textContent = fl;
        const fvEl  = mkEl('div', 'sig-value'); fvEl.textContent = fv;
        field.appendChild(flEl);
        field.appendChild(fvEl);
        grid.appendChild(field);
    });

    card.appendChild(lbl);
    card.appendChild(grid);
    panel.appendChild(card);
}

// Tab switching
function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tab);
    });
    document.querySelectorAll('.tab-panel').forEach(p => {
        p.hidden = (p.id !== `tab${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
        if (!p.hidden) p.classList.add('active');
        else p.classList.remove('active');
    });
}


// ── Q&A ───────────────────────────────────────────────────────────────────────
async function askQuestion() {
    const input    = document.getElementById('qaInput');
    const question = input.value.trim();
    const btn      = document.getElementById('qaSend');

    if (!question || !state.sessionId) {
        if (!state.sessionId) showToast('No active session — please analyse a company first', 'error');
        return;
    }

    input.value  = '';
    btn.disabled = true;

    addQaMsg('user', question);
    const loadingEl = addQaLoading();

    try {
        const res  = await fetch('/api/ask', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ session_id: state.sessionId, question }),
        });
        const data = await res.json();
        loadingEl.remove();
        addQaMsg('assistant', data.answer || data.error || 'No response received');
    } catch (err) {
        loadingEl.remove();
        addQaMsg('assistant', 'Error: ' + err.message);
    } finally {
        btn.disabled = false;
        input.focus();
    }
}

function addQaMsg(role, content) {
    const container = document.getElementById('qaMessages');
    const div  = mkEl('div', `qa-message ${role}`);
    const lbl  = mkEl('div', 'qa-msg-label');
    lbl.textContent = role === 'user' ? 'You' : 'AI Analyst';
    const body = mkEl('div', '');
    body.textContent = content;   // textContent = XSS safe
    div.appendChild(lbl);
    div.appendChild(body);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function addQaLoading() {
    const container = document.getElementById('qaMessages');
    const div  = mkEl('div', 'qa-message assistant');
    const lbl  = mkEl('div', 'qa-msg-label'); lbl.textContent = 'AI Analyst';
    const dots = document.createElement('div');
    dots.className = 'qa-msg-loading';
    dots.innerHTML = '<div class="qa-dots"><span></span><span></span><span></span></div><span>Thinking…</span>';
    div.appendChild(lbl);
    div.appendChild(dots);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}


// ── JSON actions ──────────────────────────────────────────────────────────────
async function copyJson() {
    const text = JSON.stringify(state.currentAudit, null, 2);
    try {
        await navigator.clipboard.writeText(text);
        showToast('JSON copied to clipboard', 'success');
        const btn = document.getElementById('copyBtn');
        btn.textContent = '✅ Copied!';
        setTimeout(() => { btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg> Copy JSON`; }, 2000);
    } catch {
        showToast('Could not copy — use Ctrl+C in JSON tab', 'error');
    }
}

function downloadJson() {
    const text = JSON.stringify(state.currentAudit, null, 2);
    const blob = new Blob([text], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `audit_${(state.currentAudit?.company_name || 'report').replace(/\s+/g, '_')}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('Downloading JSON…', 'success');
}


// ── UI helpers ────────────────────────────────────────────────────────────────
function resetView() {
    state.currentAudit = null;
    state.sessionId    = null;
    hideResults();
    hideQA();
    hideError();
    hidePipeline();
    document.getElementById('heroSection').style.opacity = '1';
    document.getElementById('searchInput').value = '';
    document.getElementById('searchInput').focus();
    document.getElementById('qaMessages').innerHTML = '';
}

function showError(msg) {
    const banner = document.getElementById('errorBanner');
    document.getElementById('errorMsg').textContent = msg;
    banner.hidden = false;
    banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideError() {
    document.getElementById('errorBanner').hidden = true;
}

function hideResults() {
    document.getElementById('resultsSection').hidden = true;
}

function hideQA() {
    document.getElementById('qaSection').hidden = true;
}

function setBtnLoading(loading) {
    const btn   = document.getElementById('analyzeBtn');
    const label = btn.querySelector('.btn-label');
    const spin  = btn.querySelector('.btn-spinner');
    btn.disabled = loading;
    label.hidden = loading;
    spin.hidden  = !loading;
}

function showToast(msg, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = mkEl('div', `toast ${type}`);
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3200);
}


// ── DOM factory helpers ───────────────────────────────────────────────────────
function mkEl(tag, cls) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
}

function infoCard(label, value) {
    const card = mkEl('div', 'r-card');
    const lbl  = mkEl('div', 'r-card-label'); lbl.textContent = label;
    const val  = mkEl('div', 'r-card-value'); val.textContent = value;
    card.appendChild(lbl); card.appendChild(val);
    return card;
}

function fullCard(label, text) {
    const card = mkEl('div', 'r-card full');
    const lbl  = mkEl('div', 'r-card-label'); lbl.textContent = label;
    const desc = mkEl('div', 'r-card-desc');  desc.textContent = text;
    card.appendChild(lbl); card.appendChild(desc);
    const wrap = mkEl('div', 'cards-grid'); wrap.appendChild(card);
    return wrap;
}

function opinionIcon(type) {
    return { unmodified: '✅', qualified: '⚠️', adverse: '❌', disclaimer: '🚫' }[type] || '❓';
}

function formatDate(iso) {
    if (!iso) return '—';
    try {
        return new Intl.DateTimeFormat('en-IN', {
            day: 'numeric', month: 'short', year: 'numeric',
        }).format(new Date(iso));
    } catch { return iso.slice(0, 10); }
}
