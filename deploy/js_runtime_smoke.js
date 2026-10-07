const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = path.resolve(__dirname, '..');
const element = () => ({
  hidden: false, value: '', checked: false, textContent: '', innerHTML: '', dataset: {},
  className: '', disabled: false, style: {}, selectedOptions: [], options: [],
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  appendChild() {}, prepend() {}, insertBefore() {}, setAttribute() {}, removeAttribute() {},
  showModal() {}, close() {}, reset() {}, focus() {}, closest() { return null; },
  parentElement: { insertBefore() {} },
});
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
  querySelectorAll() { return []; },
  querySelector() { return null; },
  createElement() { return element(); },
  addEventListener() {},
};
const storage = new Map();
const context = {
  console, document, window: {}, location: { href: 'http://localhost/', protocol: 'http:', host: 'localhost', pathname: '/', search: '', hash: '' },
  history: { replaceState() {} }, navigator: {}, URL, URLSearchParams, Headers, FormData: class {}, Blob,
  fetch: async () => ({ status: 200, ok: true, headers: { get: () => 'application/json' }, json: async () => ({}), text: async () => '' }),
  localStorage: { getItem: k => storage.get(k) || null, setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) },
  setTimeout, clearTimeout, setInterval, clearInterval,
  confirm: () => false, prompt: () => null, alert() {},
  Intl, Date, Math, JSON, Object, Array, String, Number, Boolean, Promise,
  CSS: { escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, '_') },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, 'frontend/app.js'), 'utf8'), context, { filename: 'app.js' });
vm.runInContext(fs.readFileSync(path.join(root, 'frontend/rc.js'), 'utf8'), context, { filename: 'rc.js' });
vm.runInContext(fs.readFileSync(path.join(root, 'frontend/ui2.js'), 'utf8'), context, { filename: 'ui2.js' });
if (typeof context.rcRenderTenantTab !== 'function' || typeof context.rcEnhanceLaunchWorkspace !== 'function' || typeof context.ui2PageOpened !== 'function') {
  throw new Error('RC hooks missing');
}
if (typeof context.buildManualProxyUrl !== 'function') throw new Error('manual proxy builder missing');
document.getElementById('proxy-scheme').value = 'socks5';
document.getElementById('proxy-host').value = '2001:db8::10';
document.getElementById('proxy-port').value = '1080';
document.getElementById('proxy-username').value = 'user name';
document.getElementById('proxy-password').value = 'p@ss';
const manualProxy = context.buildManualProxyUrl('proxy');
if (manualProxy !== 'socks5://user%20name:p%40ss@[2001:db8::10]:1080') {
  throw new Error(`manual proxy builder result invalid: ${manualProxy}`);
}
document.getElementById('proxy-username').value = '';
document.getElementById('proxy-password').value = 'secret';
let passwordWithoutUserRejected = false;
try { context.buildManualProxyUrl('proxy'); } catch { passwordWithoutUserRejected = true; }
if (!passwordWithoutUserRejected) throw new Error('manual proxy password without username was accepted');
document.getElementById('api-config').value = '[DEFAULT]\nregion=us-phoenix-1';
context.updateImportRegionDetection();
if (!document.getElementById('import-region-detected').textContent.includes('美国凤凰城')) {
  throw new Error('OCI Config region detection failed');
}
if (context.extractOciConfigRegion('region = uk-london-1') !== 'uk-london-1') {
  throw new Error('OCI Config region parser failed');
}
console.log('JS_RUNTIME_LOAD_OK');
