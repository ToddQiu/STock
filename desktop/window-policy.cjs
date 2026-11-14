"use strict";

const APP_URL = "http://127.0.0.1:8765/";

function isAppUrl(value, appUrl = APP_URL) {
  try {
    const url = new URL(value);
    return url.origin === new URL(appUrl).origin && url.pathname === "/" && !url.username && !url.password;
  } catch { return false; }
}

function isExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  } catch { return false; }
}

function isBriefingPdfUrl(value, appUrl = APP_URL) {
  try {
    const url = new URL(value);
    return url.origin === new URL(appUrl).origin && !url.username && !url.password
      && url.pathname === '/api/briefings/pdf' && url.searchParams.getAll('id').length === 1
      && Boolean(url.searchParams.get('id'))
      && [...url.searchParams.keys()].every(key => key === 'id' || key === 'download');
  } catch { return false; }
}

module.exports = { APP_URL, isAppUrl, isExternalUrl, isBriefingPdfUrl };
