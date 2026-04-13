/* ── State ──────────────────────────────────────────────────────────────── */
let fullText = '';

/* ── Boot ───────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  loadModels();
  checkSession();
  prefillSender();
});

const DEFAULT_MODEL = 'gemma3:4b';

async function loadModels() {
  const sel = document.getElementById('model-select');
  try {
    const res = await fetch('/api/models');
    if (!res.ok) throw new Error(await res.text());
    const { models } = await res.json();
    if (models.length) {
      sel.innerHTML = models.map(m =>
        `<option value="${m}"${m === DEFAULT_MODEL ? ' selected' : ''}>${m}</option>`
      ).join('');
      // If default wasn't in the list, keep whatever was auto-selected
    } else {
      sel.innerHTML = '<option value="">No models found — is Ollama running?</option>';
    }
  } catch {
    sel.innerHTML = '<option value="">Cannot reach Ollama</option>';
  }
}

/* ── Pre-fill sender URL from logged-in session ─────────────────────────── */
async function prefillSender() {
  const input = document.getElementById('origin-url');
  if (input.value) return; // don't overwrite if already typed
  try {
    const res  = await fetch('/api/me');
    const data = await res.json();
    if (data.url) input.value = data.url;
  } catch {
    // silently ignore — user can type it manually
  }
}

/* ── Session ────────────────────────────────────────────────────────────── */
async function checkSession() {
  const dot   = document.getElementById('session-dot');
  const label = document.getElementById('session-label');
  try {
    const res  = await fetch('/api/session');
    const data = await res.json();
    if (data.connected) {
      dot.className   = 'session-dot connected';
      label.textContent = 'LinkedIn connected';
    } else {
      dot.className   = 'session-dot disconnected';
      label.textContent = 'Connect LinkedIn';
    }
  } catch {
    dot.className   = 'session-dot disconnected';
    label.textContent = 'Connect LinkedIn';
  }
}

async function handleSessionClick() {
  const dot   = document.getElementById('session-dot');
  const label = document.getElementById('session-label');

  dot.className   = 'session-dot';
  label.textContent = 'Opening browser…';

  try {
    // This call blocks until the user logs in (server-side wait)
    const res = await fetch('/api/login', { method: 'POST' });
    if (res.ok) {
      dot.className   = 'session-dot connected';
      label.textContent = 'LinkedIn connected';
    } else {
      const err = await res.json();
      dot.className   = 'session-dot disconnected';
      label.textContent = 'Login failed';
      alert(`Login error: ${err.detail}`);
    }
  } catch (e) {
    dot.className   = 'session-dot disconnected';
    label.textContent = 'Connect LinkedIn';
    alert(`Error: ${e.message}`);
  }
}

/* ── Analysis entry point ───────────────────────────────────────────────── */
async function startAnalysis() {
  const originUrl = document.getElementById('origin-url').value.trim();
  const destUrl   = document.getElementById('dest-url').value.trim();
  const model     = document.getElementById('model-select').value;

  if (!originUrl || !destUrl) { alert('Enter both LinkedIn profile URLs.'); return; }
  if (!model) { alert('Select a model. Make sure Ollama is running.'); return; }

  resetUI();
  fullText = '';

  setBtn(true);
  show('progress-section');
  setStep(1, 'active');

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ origin_url: originUrl, destination_url: destUrl, model }),
    });

    if (!res.ok) {
      addStatus(`Server error: ${res.status}`, 'warn');
      setBtn(false);
      return;
    }

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const lines = buf.split('\n');
      buf = lines.pop(); // hold incomplete line

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try { handleEvent(JSON.parse(line.slice(6))); }
          catch { /* ignore malformed */ }
        }
      }
    }
  } catch (err) {
    addStatus(`Connection error: ${err.message}`, 'warn');
    setBtn(false);
  }
}

/* ── Event handler ──────────────────────────────────────────────────────── */
function handleEvent(ev) {
  switch (ev.type) {
    case 'status': {
      const step = ev.step || null;
      const isWarn = ev.message.startsWith('⚠️');
      if (step) {
        if (isWarn) {
          setStep(step, 'error', ev.message.replace('⚠️', '').trim());
        } else {
          // Mark previous steps done, activate current
          for (let i = 1; i < step; i++) setStep(i, 'done');
          setStep(step, 'active', ev.message);
          setProgressBar(step);
        }
      }
      break;
    }

    case 'profiles':
      setStep(2, 'done');
      setStep(3, 'active', 'Generating…');
      setProgressBar(3);
      showProfilesBar(ev.origin, ev.destination);
      show('stream-section');
      break;

    case 'token':
      fullText += ev.content;
      appendStream(ev.content);
      break;

    case 'done':
      setStep(3, 'done');
      setProgressBar(3, true);
      finaliseResults(fullText);
      setBtn(false);
      break;

    case 'error':
      setStep(getCurrentStep(), 'error', ev.message);
      setBtn(false);
      break;
  }
}

/* ── Progress bar helpers ────────────────────────────────────────────────── */
let _currentStep = 0;

function setStep(n, state, detail = '') {
  const el = document.getElementById(`step-${n}`);
  if (!el) return;
  el.className = `progress-step ${state}`;
  if (detail) {
    const d = document.getElementById(`step-${n}-detail`);
    if (d) d.textContent = detail;
  }
  if (state === 'active' || state === 'done') _currentStep = Math.max(_currentStep, n);
}

function setProgressBar(step, complete = false) {
  const fill = document.getElementById('progress-bar-fill');
  if (!fill) return;
  const pct = complete ? 100 : Math.round(((step - 1) / 3) * 100 + 15);
  fill.style.width = pct + '%';
  // Mark connectors between completed steps
  document.querySelectorAll('.progress-connector').forEach((el, i) => {
    el.classList.toggle('done', step > i + 1);
  });
}

function getCurrentStep() { return _currentStep || 1; }

/* ── Live stream output ─────────────────────────────────────────────────── */
function appendStream(token) {
  const pre = document.getElementById('stream-output');
  pre.textContent += token;
  // auto-scroll to bottom
  pre.scrollTop = pre.scrollHeight;
}

/* ── Profiles bar ───────────────────────────────────────────────────────── */
function showProfilesBar(origin, dest) {
  document.getElementById('profiles-bar').innerHTML = `
    <div class="profile-chip">Sender: <strong>${esc(origin.name)}</strong></div>
    <span class="sep">→</span>
    <div class="profile-chip">Recipient: <strong>${esc(dest.name)}</strong></div>
  `;
}

/* ── Parse + render results ─────────────────────────────────────────────── */
function finaliseResults(text) {
  const sections = parseSections(text);

  fillCard('tone-body',        'card-tone',        sections['TONE ANALYSIS']);
  fillCard('insights-body',    'card-insights',     sections['DESTINATION INSIGHTS']);
  fillCard('overlap-body',     'card-overlap',      sections['OVERLAP']);
  fillCard('connections-body', 'card-connections',  sections['CONNECTION POINTS']);
  fillCard('strategy-body',    'card-strategy',     sections['OUTREACH STRATEGY']);

  // Drafts
  const liRaw    = sections['LINKEDIN DRAFTS'] || '';
  const emRaw    = sections['EMAIL DRAFTS']    || '';
  renderDrafts('linkedin-drafts', parseLinkedInDrafts(liRaw), false);
  renderDrafts('email-drafts',    parseEmailDrafts(emRaw),    true);

  hide('stream-section');
  show('results-section');
}


function fillCard(bodyId, cardId, text) {
  if (!text) return;
  document.getElementById(bodyId).innerHTML = renderWithLinks(text.trim());
  document.getElementById(cardId).classList.add('visible');
}

/**
 * Convert plain text to HTML, turning LinkedIn/HTTP URLs into clickable links.
 * URLs wrapped in parentheses — e.g. "(https://...)" — become "[↗]" links so
 * the surrounding prose stays readable.
 */
function renderWithLinks(text) {
  // Escape HTML entities first
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Match URLs — optionally wrapped in parentheses
  // Group 1: leading paren (optional), Group 2: URL, Group 3: trailing paren
  return escaped.replace(
    /(\(?)((https?:\/\/[^\s\)&"<>]+))(\)?)/g,
    (_match, open, url, _url2, close) => {
      const safeUrl = url; // already HTML-escaped above
      if (open === '(' && close === ')') {
        // Parenthesised URL → inline icon link
        return `(<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="post-link" title="${safeUrl}">↗ view post</a>)`;
      }
      // Bare URL → show as short link
      return `${open}<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="post-link">${safeUrl}</a>${close}`;
    }
  );
}

/* ── Section parser ─────────────────────────────────────────────────────── */
function parseSections(text) {
  const out  = {};
  const lines = text.split('\n');
  let key = null, buf = [];

  for (const line of lines) {
    const m = line.match(/^##\s+(.+)$/);
    if (m) {
      if (key) out[key] = buf.join('\n');
      key = m[1].trim().toUpperCase();
      buf = [];
    } else if (key) {
      buf.push(line);
    }
  }
  if (key) out[key] = buf.join('\n');
  return out;
}

/* ── Draft parsers ──────────────────────────────────────────────────────── */
function _normalise(text) {
  // Unify line endings, collapse 3+ blank lines to 2
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(/\n{3,}/g, '\n\n');
}

function parseLinkedInDrafts(text) {
  if (!text || !text.trim()) return [];
  const t = _normalise(text);

  // 1. Explicit ### / ## Draft N headers
  const byHeader = t.split(/#{2,4}\s*Draft\s*\d+/i);
  if (byHeader.length > 1) return byHeader.slice(1).map(s => s.trim()).filter(Boolean);

  // 2. Double-newline paragraph separation
  let chunks = t.split(/\n\n+/).map(s => s.trim()).filter(s => s.length > 15);
  if (chunks.length >= 2) return chunks;

  // 3. Single-newline separation (one draft per line)
  chunks = t.split(/\n/).map(s => s.trim()).filter(s => s.length > 30);
  if (chunks.length >= 2) return chunks;

  // 4. Whole block as one card
  return t.trim() ? [t.trim()] : [];
}

function parseEmailDrafts(text) {
  if (!text || !text.trim()) return [];
  const t = _normalise(text);

  // 1. Explicit ### / ## Draft N headers
  const byHeader = t.split(/#{2,4}\s*Draft\s*\d+/i);
  if (byHeader.length > 1) return byHeader.slice(1).map(s => s.trim()).filter(Boolean);

  // 2. Split on Subject: at start of line (each email begins with one)
  const bySubject = t.split(/(?=^\s*Subject:)/im).map(s => s.trim()).filter(s => s.length > 15);
  if (bySubject.length >= 2) return bySubject;

  // 3. Whole block as one card
  return t.trim() ? [t.trim()] : [];
}

/* ── Draft renderer ─────────────────────────────────────────────────────── */
function renderDrafts(containerId, drafts, isEmail) {
  const container = document.getElementById(containerId);
  if (!container || !drafts.length) return;
  container.innerHTML = '';

  drafts.forEach((draft, i) => {
    const card = document.createElement('div');
    card.className = 'draft-card';

    // Number
    const num = document.createElement('div');
    num.className = 'draft-num';
    num.textContent = i + 1;

    // Body
    const body = document.createElement('div');
    body.className = 'draft-body';

    if (isEmail) {
      const lines       = draft.split('\n');
      const subjectLine = lines.find(l => /^subject:/i.test(l.trim()));
      const bodyLines   = lines.filter(l => !/^subject:/i.test(l.trim())).join('\n').trim();

      if (subjectLine) {
        const subj = document.createElement('span');
        subj.className = 'email-subject';
        subj.textContent = subjectLine.replace(/^subject:\s*/i, '').trim();
        body.appendChild(subj);
      }
      const bodyText = document.createElement('span');
      bodyText.textContent = bodyLines;
      body.appendChild(bodyText);
    } else {
      // LinkedIn note — strip any Subject: line the model may have accidentally added
      const cleaned = draft.split('\n')
        .filter(l => !/^subject:/i.test(l.trim()))
        .join('\n').trim();
      body.textContent = cleaned;
    }

    // Copy button — copy the clean text
    const copyContent = isEmail ? draft : draft.split('\n').filter(l => !/^subject:/i.test(l.trim())).join('\n').trim();
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => copyText(copyContent, btn));

    card.append(num, body, btn);
    container.appendChild(card);

    // Stagger entrance animation
    requestAnimationFrame(() =>
      setTimeout(() => card.classList.add('visible'), i * 70)
    );
  });
}

/* ── Copy helper ────────────────────────────────────────────────────────── */
async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.classList.remove('copied');
    }, 2000);
  } catch {
    btn.textContent = 'Error';
  }
}

/* ── UI helpers ─────────────────────────────────────────────────────────── */
function resetUI() {
  // Reset progress bar
  _currentStep = 0;
  [1, 2, 3].forEach(n => {
    setStep(n, 'waiting');
    const d = document.getElementById(`step-${n}-detail`);
    if (d) d.textContent = '';
  });
  const fill = document.getElementById('progress-bar-fill');
  if (fill) fill.style.width = '0%';
  document.querySelectorAll('.progress-connector').forEach(el => el.classList.remove('done'));

  // Clear stream
  document.getElementById('stream-output').textContent = '';

  // Clear results
  ['tone-body', 'insights-body', 'overlap-body', 'connections-body', 'strategy-body'].forEach(id => {
    document.getElementById(id).textContent = '';
  });
  ['card-tone', 'card-insights', 'card-overlap', 'card-connections', 'card-strategy'].forEach(id => {
    document.getElementById(id).classList.remove('visible');
  });
  document.getElementById('linkedin-drafts').innerHTML = '';
  document.getElementById('email-drafts').innerHTML   = '';
  document.getElementById('profiles-bar').innerHTML   = '';

  hide('stream-section');
  hide('results-section');
  hide('progress-section');
}

function setBtn(loading) {
  const btn = document.getElementById('analyze-btn');
  btn.disabled = loading;
  btn.innerHTML = loading
    ? 'Analyzing… <span class="arrow">⋯</span>'
    : 'Analyze <span class="arrow">→</span>';
}

function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
