"use strict";

const {isBriefingPdfUrl, isExternalUrl} = require('./window-policy.cjs');

// Opened only by the user's report link. A separate sandbox keeps the dashboard intact.
async function openReportPdf({BrowserWindow, appUrl, url, parent, session, hidden = false, openExternal = () => {}}) {
  if (!isBriefingPdfUrl(url, appUrl)) throw new Error('Untrusted report PDF URL');
  const report = new BrowserWindow({
    title: 'STock · 报告 PDF', width: 1000, height: 800,
    show: !hidden, focusable: !hidden, skipTaskbar: hidden, parent,
    autoHideMenuBar: true, backgroundColor: '#f6f7f9',
    webPreferences: {session, plugins: true, contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true},
  });
  report.setMenu(null);
  report.webContents.on('will-attach-webview', event => event.preventDefault());
  report.webContents.on('will-navigate', (event, target) => {
    if (isBriefingPdfUrl(target, appUrl)) return;
    event.preventDefault();
    if (isExternalUrl(target)) openExternal(target);
  });
  report.webContents.setWindowOpenHandler(({url: target}) => {
    if (isExternalUrl(target)) openExternal(target);
    return {action: 'deny'};
  });
  try {
    await report.loadURL(url);
    return report;
  } catch (error) {
    if (!report.isDestroyed()) report.destroy();
    throw error;
  }
}

module.exports = {openReportPdf};
