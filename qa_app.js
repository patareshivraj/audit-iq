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


