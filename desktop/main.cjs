"use strict";

const { app, BrowserWindow, ipcMain, shell, dialog } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { setTimeout: delay } = require("node:timers/promises");
const { APP_URL, isAppUrl: trustedAppUrl, isExternalUrl, isBriefingPdfUrl } = require("./window-policy.cjs");
const { openReportPdf } = require("./report-window.cjs");
const { startBackend } = require("./portable-backend.cjs");
const root = path.resolve(__dirname, "..");
const smokeTest = process.argv.includes("--smoke-test");
app.setName("STock");
const profile = app.isPackaged
  ? (smokeTest && process.env.STOCK_QA_PROFILE ? path.resolve(process.env.STOCK_QA_PROFILE) : path.join(app.getPath("appData"), "STock"))
  : path.join(root, smokeTest ? ".qa-runtime/desktop-native" : "data/desktop-profile");
fs.mkdirSync(profile, { recursive: true });
app.setPath("userData", profile);
app.setAppUserModelId("STock.Desktop");
const launchLog = path.join(profile, "desktop-launch.log");
let window;
let appUrl = APP_URL, backend, stopping = false, loaded = false;
const isAppUrl = value => trustedAppUrl(value, appUrl);

function windowState() {
  return { maximized: window.isMaximized(), focused: window.isFocused() };
}

function logLaunchFailure(stage, error) {
  const message = `${new Date().toISOString()} ${stage} ${appUrl} ${error?.code || ""} ${error?.message || error}`;
  console.error(message);
  try { fs.appendFileSync(launchLog, `${message}\n`, "utf8"); }
  catch (logError) { console.error(logError); }
}

async function loadLocalPage() {
  let timeout;
  try {
    await Promise.race([
      window.loadURL(appUrl),
      new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error("本地页面加载超时")), 20000);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

async function createWindow() {
  window = new BrowserWindow({
    title: "STock", width: 1440, height: 940, minWidth: 800, minHeight: 560,
    frame: false, show: false, backgroundColor: "#f6f7f9",
    icon: app.isPackaged ? path.join(__dirname, "logo.ico") : path.join(root, "src/quant_news_monitor/ui/logo.ico"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"), contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true,
    },
  });
  window.setMenu(null);
  const contents = window.webContents;
  // This window and its PDF viewer only use the bundled loopback service.
  if (app.isPackaged) await contents.session.setProxy({ mode: "direct" });
  const publishState = () => {
    if (!contents.isDestroyed()) contents.send("stock:window-state", windowState());
  };
  for (const event of ["maximize", "unmaximize", "focus", "blur"]) window.on(event, publishState);
  contents.on("did-finish-load", publishState);
  contents.on("will-attach-webview", event => event.preventDefault());
  contents.on("will-navigate", (event, url) => {
    if (isAppUrl(url)) return;
    event.preventDefault();
    if (isExternalUrl(url)) shell.openExternal(url).catch(console.error);
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (isAppUrl(contents.getURL()) && isBriefingPdfUrl(url, appUrl)) {
      openReportPdf({BrowserWindow, appUrl, url, parent: window, session: contents.session, hidden: smokeTest,
        openExternal: target => shell.openExternal(target).catch(console.error)}).catch(console.error);
      return {action: "deny"};
    }
    if (isAppUrl(contents.getURL()) && isExternalUrl(url)) shell.openExternal(url).catch(console.error);
    return { action: "deny" };
  });
  contents.session.setPermissionRequestHandler((sender, permission, callback, details) => {
    callback(sender === contents && isAppUrl(details.requestingUrl)
      && ["notifications", "clipboard-sanitized-write"].includes(permission));
  });
  contents.session.webRequest.onHeadersReceived({ urls: [`${new URL(appUrl).origin}/*`] }, (details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'",
    ] } });
  });
  try {
    await loadLocalPage();
  } catch (error) {
    if (window.isDestroyed()) return;
    logLaunchFailure("首次页面连接失败", error);
    contents.stop();
    await delay(350);
    if (window.isDestroyed()) return;
    await loadLocalPage();
  }
  loaded = true;
  if (!smokeTest && !window.isDestroyed()) { window.maximize(); window.show(); window.focus(); }
}

if (!smokeTest && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!loaded || !window || window.isDestroyed()) return;
    if (window.isMinimized()) window.restore();
    window.show(); window.focus();
  });
  ipcMain.handle("stock:window-action", (event, action) => {
    if (!window || window.isDestroyed() || event.sender !== window.webContents
      || event.senderFrame !== window.webContents.mainFrame || !isAppUrl(event.senderFrame.url)) {
      throw new Error("Untrusted window command");
    }
    if (action === "minimize") window.minimize();
    else if (action === "toggle-maximize") window.isMaximized() ? window.unmaximize() : window.maximize();
    else if (action === "close") setImmediate(() => window.close());
    else if (action !== "state") throw new Error("Unknown window command");
    return windowState();
  });
  app.whenReady().then(async () => {
    if (app.isPackaged) {
      backend = await startBackend(__dirname, profile);
      appUrl = backend.url;
    }
    await createWindow();
  }).catch(error => {
    logLaunchFailure(backend ? "本地页面启动失败" : "本地服务启动失败", error);
    if (!smokeTest) dialog.showErrorBox("STock", `无法打开 STock 本地工作台。\n${error.code || error.message}\n\n启动日志：${launchLog}\n服务日志：${path.join(profile, "desktop-service.log")}`);
    app.quit();
  });
  app.on("before-quit", event => {
    if (!backend || stopping) return;
    event.preventDefault();
    stopping = true;
    backend.stop().finally(() => app.quit());
  });
  app.on("window-all-closed", () => app.quit());
}
