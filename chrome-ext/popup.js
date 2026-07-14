// === X/Twitter Bookmark Archiver — Popup Script ===

const $ = (id) => document.getElementById(id);

const statusDot   = $('statusDot');
const statusText  = $('statusText');
const scrapedEl   = $('scrapedCount');
const newEl       = $('newCount');
const scrollEl    = $('scrollCount');
const logEl       = $('log');
const startBtn    = $('startBtn');
const stopBtn     = $('stopBtn');
const exportBtn   = $('exportBtn');
const sendBtn     = $('sendBtn');
const serverUrl   = $('serverUrl');

let port = null;
let collectedTweets = [];

// ── Logging ──────────────────────────────────────────────

function log(msg, type = 'info') {
  const line = document.createElement('div');
  line.className = type;
  const ts = new Date().toLocaleTimeString();
  line.textContent = `[${ts}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(text, state = 'idle') {
  statusText.textContent = text;
  statusDot.className = `dot ${state}`;
}

// ── Stats ────────────────────────────────────────────────

function updateStats() {
  scrapedEl.textContent = collectedTweets.length;
}

// ── Message routing ─────────────────────────────────────

function sendToTab(msg) {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs || !tabs[0]) {
        resolve({ error: 'No active tab' });
        return;
      }
      chrome.tabs.sendMessage(tabs[0].id, msg, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ error: chrome.runtime.lastError.message });
        } else {
          resolve(response || {});
        }
      });
    });
  });
}

// ── Scraping Control ────────────────────────────────────

let isScraping = false;

startBtn.addEventListener('click', async () => {
  const resp = await sendToTab({ action: 'ping' });
  if (resp.error) {
    log('Content script not found. Are you on x.com/i/bookmarks?', 'err');
    setStatus('Error: not on bookmarks page', 'error');
    return;
  }
  if (resp.pong !== true) {
    log('Content script not responding properly', 'err');
    setStatus('Error: content script issue', 'error');
    return;
  }

  isScraping = true;
  collectedTweets = [];
  updateStats();
  setStatus('Scraping bookmarks...', 'active');
  startBtn.disabled = true;
  stopBtn.disabled = false;
  exportBtn.disabled = true;
  sendBtn.disabled = true;
  log('Scraping started — scrolling and collecting...', 'info');

  try {
    const result = await sendToTab({ action: 'scrape' });
    if (result.error) {
      log(`Scrape error: ${result.error}`, 'err');
      setStatus('Error during scrape', 'error');
      return;
    }
    collectedTweets = result.tweets || [];
    const stats = result.stats || {};
    newEl.textContent = stats.newTweets || 0;
    scrollEl.textContent = stats.scrolls || 0;
    updateStats();
    log(`Scrape complete — collected ${collectedTweets.length} tweets across ${stats.scrolls || 0} scrolls`, 'ok');
    setStatus(`Done — ${collectedTweets.length} bookmarks scraped`, 'done');
    exportBtn.disabled = false;
    sendBtn.disabled = false;
  } catch (err) {
    log(`Scrape failed: ${err.message}`, 'err');
    setStatus('Scrape failed', 'error');
  } finally {
    isScraping = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
});

stopBtn.addEventListener('click', async () => {
  log('Stopping scrape...', 'warn');
  setStatus('Stopping...', 'idle');
  await sendToTab({ action: 'stop' });
  isScraping = false;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  if (collectedTweets.length > 0) {
    exportBtn.disabled = false;
    sendBtn.disabled = false;
  }
});

// ── Export: JSON Download ────────────────────────────────

exportBtn.addEventListener('click', () => {
  if (collectedTweets.length === 0) {
    log('Nothing to export', 'warn');
    return;
  }
  const blob = new Blob([JSON.stringify(collectedTweets, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `x-bookmarks-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  log(`Downloaded ${collectedTweets.length} bookmarks as JSON`, 'ok');
});

// ── Send to Local Server ────────────────────────────────

sendBtn.addEventListener('click', async () => {
  if (collectedTweets.length === 0) {
    log('Nothing to send', 'warn');
    return;
  }
  const url = serverUrl.value.trim() || 'http://localhost:6007/api/bookmarks';
  sendBtn.disabled = true;
  sendBtn.textContent = '⏳ Sending...';
  setStatus(`Sending ${collectedTweets.length} bookmarks...`, 'active');

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'chrome-ext',
        scrapedAt: new Date().toISOString(),
        count: collectedTweets.length,
        bookmarks: collectedTweets,
      }),
    });
    if (!resp.ok) {
      throw new Error(`Server responded ${resp.status}: ${await resp.text()}`);
    }
    const body = await resp.json();
    log(`Sent ${collectedTweets.length} bookmarks → ${url} (${resp.status})`, 'ok');
    setStatus('Sent to server', 'done');
  } catch (err) {
    log(`Send failed: ${err.message}`, 'err');
    setStatus('Send failed', 'error');
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = '📤 Send to Server';
  }
});

// ── Check tab on open ───────────────────────────────────

(async function init() {
  const resp = await sendToTab({ action: 'ping' });
  if (resp.pong === true) {
    setStatus('Ready — bookmarks page detected', 'idle');
    log('Content script loaded, ready to scrape', 'ok');
  } else {
    setStatus('Navigate to x.com/i/bookmarks to begin', 'idle');
    log('Waiting for bookmarks page...', 'info');
  }
})();