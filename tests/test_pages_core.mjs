import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {selectCompanies, validateNotes, sourceUrl} from '../pages/core.mjs';
const data=JSON.parse(readFileSync(new URL('../src/quant_news_monitor/company_landscape.json',import.meta.url),'utf8'));

test('all 25 public entities and default order',()=>{
  assert.equal(selectCompanies(data,{}).length,25);
  assert.equal(selectCompanies(data,{})[0].id,'ain');
});
test('search, entity and layer intersect without mutating catalog',()=>{
  const before=JSON.stringify(data);
  assert.equal(selectCompanies(data,{query:'MSFT',entity:'listed'})[0].name,'Microsoft');
  assert.equal(selectCompanies(data,{query:'MSFT',entity:'private'}).length,0);
  assert.ok(selectCompanies(data,{layer:'models'}).every(row=>row.layers.includes('models')));
  selectCompanies(data,{sort:'name-desc'});
  assert.equal(JSON.stringify(data),before);
});
test('ascending and descending sorting are inverse',()=>{
  assert.deepEqual(selectCompanies(data,{sort:'name-asc'}).map(c=>c.id),selectCompanies(data,{sort:'name-desc'}).map(c=>c.id).reverse());
});
test('notes-only filter and strict round trip',()=>{
  const note={stance:'watch',text:'Evidence, not a price forecast',updated_at:'2026-10-15T00:00:00Z'};
  const notes=validateNotes({schema_version:1,notes:{ain:note}},new Set(['ain']));
  assert.equal(notes.ain.text,note.text);
  assert.equal(selectCompanies(data,{notesOnly:true},notes).length,1);
});
test('invalid and unknown records are rejected transactionally',()=>{
  const ids=new Set(['ain']);
  const valid={stance:'watch',text:'x',updated_at:'2026-10-15T00:00:00Z'};
  for(const value of [null,{}, {schema_version:1,notes:[]}, {schema_version:1,notes:{fake:valid}}, {schema_version:1,notes:{ain:{...valid,stance:'guaranteed'}}}, {schema_version:1,notes:{ain:{...valid,text:'x'.repeat(4001)}}}, {schema_version:1,notes:{ain:{...valid,updated_at:'bad'}}}]) assert.throws(()=>validateNotes(value,ids));
  assert.throws(()=>validateNotes(JSON.parse('{"schema_version":1,"notes":{"__proto__":{}}}'),ids));
});
test('links allow only HTTPS without embedded credentials',()=>{
  assert.equal(sourceUrl('javascript:alert(1)'),null);
  assert.equal(sourceUrl('http://localhost/'),null);
  assert.equal(sourceUrl('https://user:password@example.com/'),null);
  assert.equal(sourceUrl('https://example.com/'),'https://example.com/');
  for(const c of data.companies)for(const s of c.sources)assert.ok(sourceUrl(s.url));
});
test('page assets are repository-relative and no desktop APIs are called',()=>{
  const html=readFileSync(new URL('../pages/index.html',import.meta.url),'utf8');
  assert.equal(/(?:src|href)="\/(?!\/)/.test(html),false);
  const js=readFileSync(new URL('../pages/app.js',import.meta.url),'utf8');
  assert.equal(js.includes('/api/'),false);
  assert.ok(html.includes('公开研究版'));
});
