/**
 * X Bookmark Archiver — Background Service Worker
 *
 * Handles:
 *   1. Receiving scraped bookmark data from the content script
 *   2. JSON download mode (blob → download)
 *   3. Local server mode (POST to companion server)
 *   4. Extension state management via chrome.storage.local
 *   5. Messaging bridge between popup UI and content script
 */

/* ========== Constants ========== */

const STORAGE_KEYS = {
  SCAN_STATE: 'scanState',         // { status, progress, total, current }
  BOOKMARKS: 'cachedBookmarks',     // Array of bookmark objects (last scrape)
  SETTINGS: 'settings',            // { exportMode: 'json' | 'server', serverUrl: string }
  LAST_SCAN: 'lastScanTimestamp',   // ISO timestamp of last completed scan
};

const DEFAULT_SETTINGS = {
  exportMode: 'json',
  serverUrl: 'http://localhost:6007/api/bookmarks',
};

/* ========== State ========== */

let scanState = {
  status: 'idle',       // 'idle' | 'scanning' | 'completed' | 'error'
  progress: 0,          // 0–100
  total: 0,
  current: 0,
  errorMessage: null,
};

let cachedBookmarks = [];
let extensionSettings = { ...DEFAULT_SETTINGS };

/* ========== Initialisation ========== */

/**
 * Load persisted state from chrome.storage.local on worker startup.
 */
async function initState() {
  try {
    const stored = await chrome.storage.local.get([
      STORAGE_KEYS.SCAN_STATE,
      STORAGE_KEYS.BOOKMARKS,
      STORAGE_KEYS.SETTINGS,
    ]);

    if (stored[STORAGE_KEYS.SCAN_STATE]) {
      scanState = { ...scanState, ...stored[STORAGE_KEYS.SCAN_STATE] };
    }
    if (stored[STORAGE_KEYS.BOOKMARKS]) {
      cachedBookmarks = stored[STORAGE_KEYS.BOOKMARKS];
    }
    if (stored[STORAGE_KEYS.SETTINGS]) {
      extensionSettings = { ...DEFAULT_SETTINGS, ...stored[STORAGE_KEYS.SETTINGS] };
    }
  } catch (err) {
    console.error('[BGA] Failed to load extension state:', err);
  }
}

/* ========== Storage Helpers ========== */

function persistScanState() {
  return chrome.storage.local.set({ [STORAGE_KEYS.SCAN_STATE]: scanState });
}

function persistBookmarks() {
  return chrome.storage.local.set({ [STORAGE_KEYS.BOOKMARKS]: cachedBookmarks });
}

function persistSettings() {
  return chrome.storage.local.set({ [STORAGE_KEYS.SETTINGS]: extensionSettings });
}

async function persistLastScan() {
  const timestamp = new Date().toISOString();
  await chrome.storage.local.set({ [STORAGE_KEYS.LAST_SCAN]: timestamp });
  return timestamp;
}

/* ========== Export Handlers ========== */

/**
 * Create a downloadable JSON blob containing all bookmarks.
 * Injects a download via the extension's downloads API so it
 * works from the service worker context.
 */
async function exportAsJsonDownload(bookmarks) {
  const data = {
    exportedAt: new Date().toISOString(),
    totalBookmarks: bookmarks.length,
    bookmarks,
  };

  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  const filename = `x-bookmarks-${Date.now()}.json`;

  try {
    const downloadId = await chrome.downloads.download({
      url,
      filename,
      saveAs: true,
    });
    console.log(`[BGA] JSON download started (id=${downloadId}): ${filename}`);
    return { success: true, downloadId, filename };
  } catch (err) {
    console.error('[BGA] JSON download failed:', err);
    return { success: false, error: err.message };
  } finally {
    // Revoke blob URL after a short delay so the download can start
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

/**
 * POST bookmark data to the local companion server.
 */
async function exportToServer(bookmarks) {
  const payload = {
    exportedAt: new Date().toISOString(),
    totalBookmarks: bookmarks.length,
    bookmarks,
  };

  const url = extensionSettings.serverUrl;

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => '');
      throw new Error(`Server responded ${response.status}: ${body}`);
    }

    const result = await response.json().catch(() => ({}));
    console.log('[BGA] Server export succeeded:', result);
    return { success: true, serverResponse: result };
  } catch (err) {
    console.error('[BGA] Server export failed:', err);
    return { success: false, error: err.message };
  }
}

/**
 * Export bookmarks using the currently configured mode.
 */
async function exportBookmarks(bookmarks, modeOverride = null) {
  const mode = modeOverride || extensionSettings.exportMode;

  if (!bookmarks || bookmarks.length === 0) {
    return { success: false, error: 'No bookmarks to export.' };
  }

  switch (mode) {
    case 'json':
      return exportAsJsonDownload(bookmarks);
    case 'server':
      return exportToServer(bookmarks);
    default:
      return { success: false, error: `Unknown export mode: ${mode}` };
  }
}

/* ========== Bookmark Processing ========== */

/**
 * Accept the raw array of bookmark objects scraped from the content script,
 * normalise, cache, persist, and trigger the configured export.
 */
async function processBookmarks(rawBookmarks) {
  if (!Array.isArray(rawBookmarks) || rawBookmarks.length === 0) {
    scanState = {
      status: 'error',
      progress: 0,
      total: 0,
      current: 0,
      errorMessage: 'No bookmark data received.',
    };
    await persistScanState();
    return { success: false, error: 'No bookmark data received.' };
  }

  // Normalise each bookmark — ensure required fields exist
  const bookmarks = rawBookmarks.map((b, i) => ({
    id: b.id || `bookmark-${Date.now()}-${i}`,
    tweetId: b.tweetId || b.id_str || null,
    url: b.url || null,
    author: b.author || b.authorName || b.screenName || null,
    authorHandle: b.authorHandle || b.handle || null,
    authorAvatar: b.authorAvatar || b.avatarUrl || null,
    text: b.text || b.fullText || b.content || '',
    createdAt: b.createdAt || b.tweetDate || null,
    media: b.media || [],
    links: b.links || [],
    isReply: Boolean(b.isReply),
    isRetweet: Boolean(b.isRetweet),
    isQuote: Boolean(b.isQuote),
    likeCount: b.likeCount || b.favoriteCount || 0,
    retweetCount: b.retweetCount || 0,
    replyCount: b.replyCount || 0,
    bookmarkCount: b.bookmarkCount || 0,
    // Preserve any extra fields the content script sends
    ...Object.fromEntries(
      Object.entries(b).filter(
        ([k]) => !['id','tweetId','url','author','authorHandle','authorAvatar',
                    'text','createdAt','media','links','isReply','isRetweet',
                    'isQuote','likeCount','retweetCount','replyCount','bookmarkCount'].includes(k)
      )
    ),
  }));

  // Cache and persist
  cachedBookmarks = bookmarks;
  await persistBookmarks();

  // Update scan state
  scanState = {
    status: 'completed',
    progress: 100,
    total: bookmarks.length,
    current: bookmarks.length,
    errorMessage: null,
  };
  await persistScanState();
  await persistLastScan();

  // Fire export
  const exportResult = await exportBookmarks(bookmarks);

  return {
    success: true,
    total: bookmarks.length,
    exportResult,
  };
}

/* ========== Messaging — Inbound from Content Script / Popup ========== */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Use keepalive pattern — return true for async sendResponse
  (async () => {
    try {
      switch (message.type) {
        /* ------- Content Script: scraped bookmark data ------- */
        case 'BOOKMARKS_SCRAPED': {
          // Forward progress updates to the popup if it's open
          if (message.progress !== undefined) {
            scanState = {
              ...scanState,
              status: 'scanning',
              progress: message.progress,
              total: message.total || 0,
              current: message.current || 0,
            };
            await persistScanState();
            broadcastState();
            sendResponse({ success: true });
            return;
          }

          // Final payload with all bookmarks
          scanState = {
            ...scanState,
            status: 'scanning',
            progress: 99,
          };
          await persistScanState();

          const result = await processBookmarks(message.bookmarks);
          scanState = {
            ...scanState,
            status: result.success ? 'completed' : 'error',
            errorMessage: result.success ? null : (result.error || 'Unknown error'),
          };
          await persistScanState();
          broadcastState();
          sendResponse(result);
          break;
        }

        /* ------- Popup: start scan ------- */
        case 'START_SCAN': {
          scanState = {
            status: 'scanning',
            progress: 0,
            total: 0,
            current: 0,
            errorMessage: null,
          };
          await persistScanState();
          sendResponse({ success: true });

          // Find the X/Twitter tab and inject the content script
          injectContentScript();
          break;
        }

        /* ------- Popup: get current state ------- */
        case 'GET_STATE': {
          sendResponse({
            scanState,
            bookmarks: cachedBookmarks,
            settings: extensionSettings,
            lastScan: (await chrome.storage.local.get(STORAGE_KEYS.LAST_SCAN))[STORAGE_KEYS.LAST_SCAN] || null,
          });
          break;
        }

        /* ------- Popup: update settings ------- */
        case 'UPDATE_SETTINGS': {
          extensionSettings = { ...extensionSettings, ...message.settings };
          await persistSettings();
          sendResponse({ success: true, settings: extensionSettings });
          break;
        }

        /* ------- Popup: re-export last scrape ------- */
        case 'REEXPORT': {
          const mode = message.mode || null;
          const result = await exportBookmarks(cachedBookmarks, mode);
          sendResponse(result);
          break;
        }

        /* ------- Popup: reset state ------- */
        case 'RESET_STATE': {
          scanState = {
            status: 'idle',
            progress: 0,
            total: 0,
            current: 0,
            errorMessage: null,
          };
          cachedBookmarks = [];
          await persistScanState();
          await persistBookmarks();
          await chrome.storage.local.remove(STORAGE_KEYS.LAST_SCAN);
          sendResponse({ success: true });
          break;
        }

        /* ------- Content Script: ping (alive check) ------- */
        case 'PING': {
          sendResponse({ pong: true });
          break;
        }

        default:
          console.warn('[BGA] Unknown message type:', message.type);
          sendResponse({ success: false, error: `Unknown type: ${message.type}` });
      }
    } catch (err) {
      console.error('[BGA] Message handler error:', err);
      sendResponse({ success: false, error: err.message });
    }
  })();

  return true; // Keep channel open for async sendResponse
});

/* ========== Content Script Injection ========== */

/**
 * Find an open X/Twitter tab and inject the content script.
 */
async function injectContentScript() {
  try {
    const tabs = await chrome.tabs.query({ url: [
      'https://x.com/*',
      'https://twitter.com/*',
      'https://mobile.twitter.com/*',
    ]});

    // Prefer the active tab, else pick the first match
    const targetTab = tabs.find(t => t.active) || tabs[0];

    if (!targetTab) {
      scanState = {
        status: 'error',
        progress: 0,
        total: 0,
        current: 0,
        errorMessage: 'No X/Twitter tab found. Open x.com/bookmarks first.',
      };
      await persistScanState();
      broadcastState();
      return;
    }

    // Execute the content script in the target tab
    await chrome.scripting.executeScript({
      target: { tabId: targetTab.id },
      files: ['content.js'],
    });

    // Tell the content script to begin scraping
    chrome.tabs.sendMessage(targetTab.id, { type: 'BEGIN_SCRAPE' });

    // Focus the tab so the user can see progress
    await chrome.tabs.update(targetTab.id, { active: true });
  } catch (err) {
    console.error('[BGA] Failed to inject content script:', err);
    scanState = {
      status: 'error',
      progress: 0,
      total: 0,
      current: 0,
      errorMessage: `Injection error: ${err.message}`,
    };
    await persistScanState();
    broadcastState();
  }
}

/* ========== Broadcast State ========== */

/**
 * Push current state to any open popup.
 */
function broadcastState() {
  chrome.runtime.sendMessage({
    type: 'STATE_UPDATE',
    scanState,
    bookmarksCount: cachedBookmarks.length,
  }).catch(() => {
    // Popup may not be open — silent
  });
}

/* ========== Extension Lifecycle ========== */

chrome.runtime.onInstalled.addListener(async (details) => {
  console.log(`[BGA] Extension installed/updated (reason=${details.reason})`);

  // Initialise defaults on fresh install
  if (details.reason === 'install') {
    await chrome.storage.local.set({
      [STORAGE_KEYS.SETTINGS]: DEFAULT_SETTINGS,
      [STORAGE_KEYS.SCAN_STATE]: scanState,
      [STORAGE_KEYS.BOOKMARKS]: [],
    });
  }

  await initState();
});

// Initialise on service worker wake (suspend/resume cycle)
initState();

console.log('[BGA] X Bookmark Archiver service worker loaded.');