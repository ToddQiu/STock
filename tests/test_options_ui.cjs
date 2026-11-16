const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const file = 'src/quant_news_monitor/ui/options.js';

// Test the module's event handlers and rendered balances without starting a browser.
function setup(storage = new Map()) {
  assert.ok(fs.existsSync(file), 'options workspace must be implemented');
  const nodes = new Map();
  class Element {
    constructor(id = '', attrs = '') {
      this.id = id; this.dataset = {}; this.listeners = {}; this.hidden = false;
      this.disabled = false; this.value = ''; this.textContent = ''; this.attrs = {};
      for (const m of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) {
        this.attrs[m[1]] = m[2];
        if (m[1] === 'value') this.value = m[2];
        if (m[1].startsWith('data-')) this.dataset[m[1].slice(5).replace(/-([a-z])/g, (_,c) => c.toUpperCase())] = m[2];
      }
      this.classList = {toggle(){}};
    }
    set innerHTML(html) {
      this.html = html;
      for (const m of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
        const n = new Element(m[3],m[2]);
        if (m[1] === 'select') {
          const body = html.slice(m.index + m[0].length).split('</select>')[0];
          n.value = (body.match(/<option[^>]*value="([^"]*)"[^>]*selected/) || body.match(/<option[^>]*value="([^"]*)"/) || [,''])[1];
        }
        nodes.set(n.id,n);
      }
    }
    get innerHTML(){return this.html || '';}
    addEventListener(type,fn){this.listeners[type] = fn;}
    setAttribute(k,v){this.attrs[k] = String(v);}
    removeAttribute(k){delete this.attrs[k];}
    scrollIntoView(){}
    focus(){}
  }
  const host = new Element('options-workspace'); nodes.set(host.id,host);
  const C = require('../src/quant_news_monitor/ui/options_core.js');
  const L = require('../src/quant_news_monitor/ui/options_learning.js');
  const context = vm.createContext({console,window:{STockOptions:C,STockOptionsLearning:L},
    document:{getElementById:id=>nodes.get(id)},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
    Date,JSON,Number,Math,Intl,Set,Array,Object,String});
  vm.runInContext(fs.readFileSync(file,'utf8'),context);
  const click = (action,extra={}) => host.listeners.click({target:{closest:()=>({dataset:{optAction:action,...extra}})},preventDefault(){}});
  const submit = id => host.listeners.submit({target:{id},preventDefault(){}});
  return {nodes,storage,click,submit,host,context};
}

test('learning entry explains an answer and opens the associated strategy', () => {
  const e = setup();
  assert.equal(e.nodes.get('opt-learn').hidden,false);
  e.click('quiz',{lesson:'0',choice:'0'});
  assert.ok(e.nodes.get('opt-quiz-0').textContent.length > 20);
  e.click('practice',{strategy:'cash_put'});
  assert.equal(e.nodes.get('opt-simulate').hidden,false);
  assert.equal(e.nodes.get('opt-strategy').value,'cash_put');
});

test('cash put simulation shows a liability and expiry can be undone and restored', () => {
  const e = setup();
  e.click('practice',{strategy:'cash_put'});
  e.nodes.get('opt-fee').value = '0';
  e.submit('opt-open-form');
  assert.equal(e.nodes.get('opt-error').textContent,'');
  assert.match(e.nodes.get('opt-equity').textContent,/10,000/);
  assert.match(e.nodes.get('opt-option-value').textContent,/-/);
  e.nodes.get('opt-next-spot').value = '30';
  e.nodes.get('opt-next-days').value = '0';
  e.submit('opt-market-form');
  e.click('settle');
  assert.match(e.nodes.get('opt-shares').textContent,/100/);
  e.click('undo');
  assert.match(e.nodes.get('opt-shares').textContent,/^0/);
  e.click('save');
  const restored = setup(e.storage); restored.click('restore');
  assert.equal(restored.nodes.get('opt-equity').textContent,e.nodes.get('opt-equity').textContent);
  assert.equal(restored.nodes.get('opt-next-days').value,'0');
});

test('invalid orders and corrupt saved exercise preserve the current account', () => {
  const e = setup(); e.click('practice',{strategy:'cash_put'});
  e.nodes.get('opt-contracts').value = '100';
  e.submit('opt-open-form');
  assert.ok(e.nodes.get('opt-error').textContent.length > 0);
  assert.match(e.nodes.get('opt-equity').textContent,/10,000/);
  e.storage.set('stock.options.exercise.v1','{"version":1,"capital":10000,"actions":[{"type":"magic"}]}');
  e.click('restore');
  assert.ok(e.nodes.get('opt-error').textContent.length > 0);
  assert.match(e.nodes.get('opt-equity').textContent,/10,000/);
});

test('closing an option keeps the next stock-backed order at the current price', () => {
  const e = setup();e.click('practice',{strategy:'covered_call'});
  e.submit('opt-open-form');e.click('scenario',{scenario:'down'});e.click('close');
  assert.equal(e.nodes.get('opt-spot').value,'48');
  e.submit('opt-open-form');
  assert.equal(e.nodes.get('opt-error').textContent,'');
});

test('editing initial capital updates equity and the saved unused account', () => {
  const e = setup();e.nodes.get('opt-capital').value='20000';
  e.host.listeners.change({target:e.nodes.get('opt-capital')});
  assert.match(e.nodes.get('opt-equity').textContent,/20,000/);
  e.click('save');const restored=setup(e.storage);restored.click('restore');
  assert.match(restored.nodes.get('opt-equity').textContent,/20,000/);
});
