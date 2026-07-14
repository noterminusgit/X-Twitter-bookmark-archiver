// === X/Twitter Bookmark Archiver — Content Script ===
// Injected on x.com/i/bookmarks and twitter.com/i/bookmarks

let isRunning = false;
let shouldStop = false;
let collected = [];
let seenIds = new Set();
let scrollCount = 0;

// ── Tweet scraping ──────────────────────────────────────

function extractTweets() {
  // X.com renders each tweet as an <article> with role="article"
  const articles = document.querySelectorAll('article[role="article"]');
  const tweets = [];

  for (const article of articles) {
    try {
      const tweet = parseArticle(article);
      if (tweet && tweet.id && !seenIds.has(tweet.id)) {
        seenIds.add(tweet.id);
        tweets.push(tweet);
      }
    } catch (e) {
      // skip malformed articles silently
    }
  }
  return tweets;
}

function parseArticle(article) {
  // ── Tweet ID ──
  // The article contains <a> with href like /user/status/123456789
  const links = article.querySelectorAll('a[href*="/status/"]');
  let tweetId = null;
  for (const link of links) {
    const m = link.getAttribute('href')?.match(/\/status\/(\d+)/);
    if (m) {
      tweetId = m[1];
      break;
    }
  }
  if (!tweetId) return null;

  // ── Author info ──
  // Author info is in the tweet header area
  const authorLink = article.querySelector('a[role="link"][href*="/"]:not([href*="/status/"])');
  let authorHandle = '';
  let authorName = '';
  if (authorLink) {
    authorHandle = authorLink.getAttribute('href')?.replace(/^\//, '') || '';
    // The display name is typically in a span within the author area
    const nameSpan = article.querySelector('[data-testid="User-Name"]');
    authorName = nameSpan ? nameSpan.textContent?.trim() || '' : '';
  }

  // ── Text content ──
  const textEl = article.querySelector('[data-testid="tweetText"]');
  const text = textEl ? textEl.textContent?.trim() || '' : '';

  // ── Timestamp ──
  const timeEl = article.querySelector('time');
  const timestamp = timeEl ? timeEl.getAttribute('datetime') || '' : '';

  // ── Media URLs ──
  const media = [];
  // Images
  const imgs = article.querySelectorAll('img[src*="media"]');
  imgs.forEach((img) => {
    const src = img.getAttribute('src');
    if (src && !src.includes('profile_images')) {
      media.push({ type: 'photo', url: src });
    }
  });
  // Videos
  const videos = article.querySelectorAll('video source');
  videos.forEach((v) => {
    const src = v.getAttribute('src');
    if (src) media.push({ type: 'video', url: src });
  });

  return {
    id: tweetId,
    author: {
      name: authorName || authorHandle,
      handle: authorHandle,
    },
    text,
    timestamp,
    media,
    scrapedAt: new Date().toISOString(),
    url: `https://x.com/${authorHandle || 'i'}/status/${tweetId}`,
  };
}

// ── Scrolling ───────────────────────────────────────────

async function autoScroll() {
  return new Promise((resolve) => {
    let lastHeight = document.documentElement.scrollHeight;
    let noChangeCount = 0;
    const maxNoChange = 5;     // consecutive unchanged heights = stop
    const maxScrolls = 500;     // safety limit

    function doScroll() {
      if (shouldStop || scrollCount >= maxScrolls) {
        resolve({ stopped: shouldStop, scrollCount });
        return;
      }

      window.scrollBy(0, 4000);
      scrollCount++;

      // Wait for new content to load
      setTimeout(() => {
        const newHeight = document.documentElement.scrollHeight;
        if (newHeight === lastHeight) {
          noChangeCount++;
          if (noChangeCount >= maxNoChange) {
            resolve({ stopped: false, scrollCount });
            return;
          }
        } else {
          noChangeCount = 0;
          lastHeight = newHeight;
        }

        // Collect tweets after scroll
        const fresh = extractTweets();
        collected.push(...fresh);

        // Send progress update to popup
        chrome.runtime.sendMessage({
          action: 'progress',
          stats: {
            total: collected.length,
            newTweets: fresh.length,
            scrolls: scrollCount,
          },
        });

        // Next scroll
        setTimeout(doScroll, 800);
      }, 1500);
    }

    doScroll();
  });
}

// ── Full scrape ─────────────────────────────────────────

async function scrapeAll() {
  isRunning = true;
  shouldStop = false;
  collected = [];
  seenIds = new Set();
  scrollCount = 0;

  // Initial collection
  const initial = extractTweets();
  collected.push(...initial);

  chrome.runtime.sendMessage({
    action: 'progress',
    stats: { total: collected.length, newTweets: initial.length, scrolls: 0 },
  });

  // Auto-scroll and collect
  const result = await autoScroll();

  return {
    tweets: collected,
    stats: {
      total: collected.length,
      scrolls: scrollCount,
      newTweets: collected.length,
    },
    stopped: result.stopped,
  };
}

// ── Message handler ─────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.action) {
    case 'ping':
      sendResponse({ pong: true });
      break;

    case 'scrape':
      if (isRunning) {
        sendResponse({ error: 'Scrape already in progress' });
        return;
      }
      scrapeAll()
        .then((result) => {
          isRunning = false;
          sendResponse(result);
        })
        .catch((err) => {
          isRunning = false;
          sendResponse({ error: err.message });
        });
      return true; // keep channel open for async response

    case 'stop':
      shouldStop = true;
      isRunning = false;
      sendResponse({ stopped: true });
      break;

    default:
      sendResponse({ error: `Unknown action: ${msg.action}` });
  }
});

// Signal ready
console.log('[X Bookmark Archiver] Content script loaded');