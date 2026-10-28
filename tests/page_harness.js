// Runs the page's own script against a tiny fake page, so the tests can check what it renders without a browser.
// usage: node page_harness.js page.html   -> prints JSON {moments, people, tabs, sub, personOptions}
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const data = /<script id="data" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1].replace(/<\\\//g, '</');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];

const els = {};
const make = id => els[id] = els[id] || {
  id, innerHTML: '', textContent: id === 'data' ? data : '', value: '', dataset: {}, onclick: null, oninput: null, onchange: null,
  classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
  querySelectorAll() { return []; },
};
const tabs = [...html.matchAll(/data-tab="([a-z]+)"/g)].map(m => ({ dataset: { tab: m[1] }, onclick: null, classList: { toggle() {} } }));
const doc = {
  getElementById: make,
  querySelectorAll: sel => (sel === '.tab' ? tabs : []),
  querySelector: () => null,
  createElement: () => ({ click() {} }),
};
const sandbox = { document: doc, localStorage: { getItem: () => null, setItem() {} }, navigator: {}, alert() {},
  URL: { createObjectURL: () => '' }, Blob: function () {}, console, setTimeout };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const out = { tabs: tabs.map(t => t.dataset.tab), sub: els.sub.textContent };
out.moments = els.app.innerHTML;                       // the page opens on the Moments tab
out.momentsBar = els.bar.innerHTML;
if (tabs.some(t => t.dataset.tab === 'people')) {
  vm.runInContext("show('people')", sandbox);
  out.people = els.app.innerHTML;
  out.peopleBar = els.bar.innerHTML;
  vm.runInContext("show('topics')", sandbox);
  out.topics = els.app.innerHTML;
}
if (process.argv[3]) out.eval = vm.runInContext(process.argv[3], sandbox);   // optional: evaluate an expression in the page
console.log(JSON.stringify(out));
