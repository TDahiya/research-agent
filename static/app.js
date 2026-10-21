/* === State === */
let taskId = null;
let ws = null;
let isRunning = false;
let uploadedPapers = [];

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

/* === Theme === */
function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
}

$('#themeToggle').addEventListener('click', () => {
    const current = document.documentElement.getAttribute('data-theme');
    const isDark = current === 'dark' || (!current && window.matchMedia('(prefers-color-scheme: dark)').matches);
    const next = isDark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
});

initTheme();

/* === Auto-resize textarea === */
const queryInput = $('#queryInput');
queryInput.addEventListener('input', () => {
    queryInput.style.height = 'auto';
    queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + 'px';
});

/* === File upload === */
const uploadZone = $('#uploadZone');
const fileInput = $('#fileInput');
const attachBtn = $('#attachBtn');

uploadZone.addEventListener('click', () => fileInput.click());
attachBtn.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (files.length) uploadFiles(files);
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length) {
        uploadFiles(Array.from(fileInput.files));
        fileInput.value = '';
    }
});

async function uploadFiles(files) {
    uploadZone.classList.add('uploading');
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));

    try {
        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.papers) {
            data.papers.forEach(p => {
                if (!p.error) {
                    uploadedPapers.push(p);
                }
            });
            renderPapers();
        }
    } catch (e) {
        console.error('Upload failed:', e);
    } finally {
        uploadZone.classList.remove('uploading');
    }
}

function renderPapers() {
    const list = $('#papersList');
    const inputPapers = $('#inputPapers');
    const chips = uploadedPapers.map(p => `
        <span class="paper-chip">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
            </svg>
            ${escHtml(p.name)}
            <span class="chip-info">${p.pages}p, ${p.chunks} chunks</span>
        </span>
    `).join('');
    list.innerHTML = chips;
    inputPapers.innerHTML = chips;
}

/* === Pipeline === */
function resetPipeline() {
    $$('.pipeline-step').forEach(s => s.classList.remove('active', 'complete'));
    $('#pipeline').classList.add('visible');
}

function setActiveStep(agent) {
    const steps = ['retriever', 'summarizer', 'fact_checker', 'human_review'];
    const idx = steps.indexOf(agent);
    $$('.pipeline-step').forEach((el) => {
        const stepName = el.dataset.step;
        el.classList.remove('active');
        if (steps.indexOf(stepName) < idx) {
            el.classList.add('complete');
        } else if (stepName === agent) {
            el.classList.add('active');
        }
    });
}

function completeStep(agent) {
    const el = $(`.pipeline-step[data-step="${agent}"]`);
    if (el) {
        el.classList.remove('active');
        el.classList.add('complete');
    }
}

function completeAllSteps() {
    $$('.pipeline-step').forEach(s => {
        s.classList.remove('active');
        s.classList.add('complete');
    });
}

/* === Messages === */
function addMessage(html, className = '') {
    const div = document.createElement('div');
    div.className = `message ${className}`.trim();
    div.innerHTML = html;
    $('#messages').appendChild(div);
    scrollToBottom();
    return div;
}

function addAgentMessage(agent, content, extra = '') {
    return addMessage(`
        <div class="message-agent">
            <span class="agent-badge ${agent}">${formatAgent(agent)}</span>
        </div>
        <div class="message-content">${content}</div>
        ${extra}
    `);
}

function addLoadingMessage(agent) {
    const div = addAgentMessage(agent,
        `<div class="loading-dots"><span></span><span></span><span></span></div>`
    );
    div.id = `loading-${agent}`;
    return div;
}

function removeLoading(agent) {
    const el = $(`#loading-${agent}`);
    if (el) el.remove();
}

function formatAgent(agent) {
    return {
        retriever: 'Retriever',
        summarizer: 'Gap Analyzer',
        fact_checker: 'Fact Checker',
        human_review: 'Review'
    }[agent] || agent;
}

function scrollToBottom() {
    const area = $('#chatArea');
    area.scrollTop = area.scrollHeight;
}

/* === Source cards === */
function renderSources(sources) {
    if (!sources || !sources.length) return '';
    const cards = sources.slice(0, 10).map(s => {
        const isPaper = s.source_type === 'paper';
        const tag = isPaper ? 'div' : 'a';
        const href = isPaper ? '' : ` href="${escHtml(s.url)}" target="_blank" rel="noopener"`;
        return `
            <${tag} class="source-card"${href}>
                <span class="source-type ${s.source_type}">${s.source_type}</span>
                <div class="source-title">${escHtml(s.title)}</div>
                <div class="source-snippet">${escHtml(s.snippet || '')}</div>
            </${tag}>
        `;
    }).join('');
    return `<div class="sources-grid">${cards}</div>`;
}

/* === Claims === */
function renderClaims(claims) {
    if (!claims || !claims.length) return '';
    const items = claims.map(c => {
        const conf = (c.confidence || 0);
        const pct = Math.round(conf * 100);
        const level = conf >= 0.8 ? 'high' : conf >= 0.6 ? 'medium' : 'low';
        const color = level === 'high' ? 'var(--green)' : level === 'medium' ? 'var(--yellow)' : 'var(--red)';
        const contradictions = (c.contradictions && c.contradictions.length && c.contradictions[0])
            ? `<div class="claim-contradictions">${c.contradictions.join('; ')}</div>` : '';
        return `
            <div class="claim-item">
                <div class="claim-header">
                    <span class="claim-text">${escHtml(c.text)}</span>
                    <span class="confidence-badge confidence-${level}">${pct}%</span>
                </div>
                <div class="confidence-bar">
                    <div class="confidence-fill" style="width:${pct}%;background:${color}"></div>
                </div>
                ${contradictions}
            </div>
        `;
    }).join('');
    return `<div class="claims-list">${items}</div>`;
}

/* === Markdown (minimal) === */
function renderMarkdown(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/^\- (.+)$/gm, '<li>$1</li>')
        .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
        .replace(/\[(\d+)\]/g, '<sup class="citation">[$1]</sup>')
        .replace(/\[P(\d+)\]/g, '<sup class="citation paper-cite">[P$1]</sup>')
        .replace(/\n{2,}/g, '</p><p>')
        .replace(/\n/g, '<br>')
        .replace(/^/, '<p>').replace(/$/, '</p>')
        .replace(/<p><h/g, '<h').replace(/<\/h(\d)><\/p>/g, '</h$1>')
        .replace(/<p><ul>/g, '<ul>').replace(/<\/ul><\/p>/g, '</ul>')
        .replace(/<p><\/p>/g, '');
}

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
}

/* === API === */
async function sendQuery() {
    const query = queryInput.value.trim();
    if (!query || isRunning) return;

    isRunning = true;
    $('#sendBtn').disabled = true;
    $('#welcome').style.display = 'none';
    $('#messages').innerHTML = '';
    resetPipeline();

    addMessage(`<div class="user-message">${escHtml(query)}</div>`);
    if (uploadedPapers.length) {
        addMessage(`<div class="user-message" style="font-size:0.8rem;padding:8px 12px;">
            Papers loaded: ${uploadedPapers.map(p => escHtml(p.name)).join(', ')}
        </div>`);
    }
    addLoadingMessage('retriever');
    setActiveStep('retriever');

    try {
        const res = await fetch('/api/research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        taskId = data.task_id;
        connectWS(taskId);
    } catch (e) {
        removeLoading('retriever');
        addMessage(`<div class="error-message">Failed to start research: ${escHtml(e.message)}</div>`);
        isRunning = false;
        $('#sendBtn').disabled = false;
    }

    queryInput.value = '';
    queryInput.style.height = 'auto';
}

function connectWS(id) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/${id}`);
    ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
    ws.onclose = () => { if (isRunning) setTimeout(() => pollStatus(id), 2000); };
    ws.onerror = () => {};
}

let lastAgent = null;

function handleMessage(msg) {
    switch (msg.type) {
        case 'agent_update': {
            const agent = msg.agent;
            const data = msg.data || {};

            if (lastAgent && lastAgent !== agent) {
                removeLoading(lastAgent);
                completeStep(lastAgent);
            }
            removeLoading(agent);
            setActiveStep(agent);

            let content = '';
            let extra = '';

            if (data.messages && data.messages.length) {
                const last = data.messages[data.messages.length - 1];
                content = escHtml(last.content || '');
            }

            if (agent === 'retriever' && data.sources) {
                const paperCount = data.sources.filter(s => s.source_type === 'paper').length;
                const webCount = data.sources.length - paperCount;
                content = `Found ${data.sources.length} sources`;
                if (paperCount) content += ` (${webCount} web/academic, ${paperCount} from papers)`;
                extra = renderSources(data.sources);
            } else if (agent === 'summarizer' && data.summary) {
                content = renderMarkdown(data.summary);
            } else if (agent === 'fact_checker' && data.claims) {
                const passed = data.fact_check_passed;
                content = `Verified ${data.claims.length} claims. ${passed ? 'All checks passed.' : 'Some claims need stronger evidence — routing back for more sources.'}`;
                extra = renderClaims(data.claims);
            }

            if (content) addAgentMessage(agent, content, extra);

            // If fact check failed and iteration allows, show loading for next agent
            if (agent === 'fact_checker' && data.fact_check_passed === false) {
                addLoadingMessage('retriever');
            }

            lastAgent = agent;
            break;
        }

        case 'human_review_required':
            removeLoading('human_review');
            if (lastAgent) completeStep(lastAgent);
            setActiveStep('human_review');
            $('#approvalBar').classList.add('visible');
            scrollToBottom();
            break;

        case 'complete': {
            removeLoading(lastAgent);
            completeAllSteps();
            $('#approvalBar').classList.remove('visible');

            const data = msg.data || {};
            let content = data.summary ? renderMarkdown(data.summary) : '';

            addMessage(`
                <div class="final-result">
                    <div class="final-badge">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                        Gap Analysis Complete
                    </div>
                    <div class="message-content">${content}</div>
                </div>
            `);

            if (data.claims) addMessage(renderClaims(data.claims));
            if (data.sources) {
                addMessage(`<div class="message-agent"><span class="agent-badge retriever">Sources</span></div>${renderSources(data.sources)}`);
            }

            isRunning = false;
            $('#sendBtn').disabled = false;
            break;
        }

        case 'error':
            removeLoading(lastAgent);
            addMessage(`<div class="error-message">${escHtml(msg.message)}</div>`);
            isRunning = false;
            $('#sendBtn').disabled = false;
            break;
    }
}

async function pollStatus(id) {
    if (!isRunning) return;
    try {
        const res = await fetch(`/api/status/${id}`);
        const data = await res.json();
        if (data.status === 'complete') handleMessage({ type: 'complete', data: data.result });
        else if (data.status === 'error') handleMessage({ type: 'error', message: data.result?.error || 'Unknown error' });
        else setTimeout(() => pollStatus(id), 3000);
    } catch { setTimeout(() => pollStatus(id), 5000); }
}

/* === Human approval === */
function approve() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'human_decision', approved: true }));
    }
    $('#approvalBar').classList.remove('visible');
    addAgentMessage('human_review', 'Approved — finalizing research output.');
    completeStep('human_review');
}

function showReviseInput() {
    $('#reviseInput').classList.add('visible');
    $('#feedbackText').focus();
}

function submitRevision() {
    const feedback = $('#feedbackText').value.trim();
    if (!feedback) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'human_decision', approved: false, feedback }));
    }
    $('#approvalBar').classList.remove('visible');
    $('#reviseInput').classList.remove('visible');
    $('#feedbackText').value = '';
    addAgentMessage('human_review', `Revision requested: "${escHtml(feedback)}"`);
    resetPipeline();
    addLoadingMessage('retriever');
    setActiveStep('retriever');
    lastAgent = 'retriever';
}

/* === Input === */
function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendQuery();
    }
}

function useSuggestion(btn) {
    queryInput.value = btn.textContent;
    sendQuery();
}
