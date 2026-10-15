import {selectCompanies, validateNotes, sourceUrl} from './core.mjs';

const $ = id => document.getElementById(id);
const KEY = 'stock.pages.research.v1';
let data, selected, notes = Object.create(null), dirty = false, statusTimer, notesReadable = true;
const filterIds = ['search', 'entity', 'layer', 'sort', 'notes-only'];

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function status(message) {
  clearTimeout(statusTimer);
  $('status').textContent = message;
  $('status').hidden = false;
  statusTimer = setTimeout(() => {$('status').hidden = true;}, 7000);
}
function canLeave() { return !dirty || confirm('当前修改尚未保存，放弃本次修改？'); }
function saveNotes(next) {
  if (!notesReadable) throw new Error('原有笔记无法读取，已阻止覆盖。请先检查或备份浏览器存储中的原记录。');
  try { localStorage.setItem(KEY, JSON.stringify({schema_version:1, notes:next})); }
  catch { throw new Error('浏览器无法保存笔记。请检查存储权限或空间；当前内容仍保留在编辑框中。'); }
  notes = next;
}
function filters() { return {query:$('search').value, entity:$('entity').value, layer:$('layer').value, sort:$('sort').value, notesOnly:$('notes-only').checked}; }
function renderList() {
  if (!data) return;
  const rows = selectCompanies(data, filters(), notes);
  $('count').textContent = `${rows.length} / ${data.companies.length}`;
  $('companies').replaceChildren(...rows.map(c => {
    const button = node('button', undefined, 'company-row');
    button.type = 'button';
    button.dataset.id = c.id;
    button.setAttribute('aria-current', String(c.id === selected));
    button.append(node('strong', c.name), node('small', `${data.entities[c.entity]}${c.ticker ? ' · '+c.ticker : ''}${notes[c.id] ? ' · 有笔记' : ''}`));
    button.addEventListener('click', () => {
      if (!canLeave()) return;
      showCompany(c.id);
      if (matchMedia('(max-width:800px)').matches) $('content').scrollIntoView({block:'start'});
      $('content').focus({preventScroll:true});
    });
    return button;
  }));
  if (!rows.length) $('companies').append(node('p', '没有匹配的公司，请调整搜索或筛选。', 'error'));
}
function showCompany(id) {
  const company = data.companies.find(c => c.id === id);
  if (!company) return;
  selected = company.id;
  history.replaceState(null, '', '#company='+encodeURIComponent(selected));
  $('empty-detail').hidden = true;
  $('company-detail').hidden = false;
  for (const key of ['name','alias','ticker','business','monetization']) $(key).textContent = company[key] || '';
  $('classification').textContent = [data.entities[company.entity], ...company.layers.map(l => data.layers[l])].join(' · ');
  $('as-of').textContent = `资料整理日期：${data.reviewed_at}；非实时更新`;
  const fields = {fundamentals:'基本面驱动',moat:'竞争壁垒',valuation:'估值框架',macro:'宏观传导',strategic_fit:'CVC 视角',metrics:'跟踪指标',risks:'主要风险'};
  $('analysis').replaceChildren(...Object.entries(fields).flatMap(([key,label]) => [node('dt',label),node('dd',company[key])]));
  $('sources').replaceChildren(...company.sources.flatMap(source => {
    const url = sourceUrl(source.url);
    if (!url) return [];
    const link = node('a', source.title + (source.published_at ? ' · '+source.published_at : ' · 原文日期待核验'));
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    return [link];
  }));
  const labels = {bullish:'有条件看多',neutral:'中性观察',bearish:'有条件看空'};
  $('scenarios').replaceChildren(...Object.entries(labels).map(([key,label]) => {
    const item = company.scenarios[key], section = node('div',undefined,'scenario '+key);
    section.append(node('h4',label),node('p',item.thesis),node('p','成立条件：'+item.conditions),node('p','失效条件：'+item.invalidation));
    return section;
  }));
  const note = notes[selected];
  $('stance').value = note?.stance || 'watch';
  $('note').value = note?.text || '';
  $('saved-at').textContent = note ? '保存于 '+new Date(note.updated_at).toLocaleString('zh-CN') : '尚无笔记';
  $('delete-note').disabled = !note;
  dirty = false;
  renderList();
}
function view(name) {
  $('research-view').hidden = name !== 'research';
  $('about-view').hidden = name !== 'about';
  $('research-tab').setAttribute('aria-pressed',String(name === 'research'));
  $('about-tab').setAttribute('aria-pressed',String(name === 'about'));
}
async function load() {
  $('load-error').hidden = true; $('retry').hidden = true;
  try {
    const response = await fetch('./company-data.json');
    if (!response.ok) throw new Error('HTTP '+response.status);
    const value = await response.json();
    if (!Array.isArray(value.companies) || value.companies.length !== 25) throw new Error('目录校验失败');
    data = value;
    const ids = new Set(data.companies.map(c => c.id));
    try { const stored = localStorage.getItem(KEY); notes = stored ? validateNotes(JSON.parse(stored), ids) : Object.create(null); notesReadable = true; }
    catch { notesReadable = false; status('本地笔记无法读取，已停止写入。原存储未修改，请先检查浏览器存储权限或备份。'); }
    for (const [id, entries] of [['entity',data.entities],['layer',data.layers]]) {
      $(id).replaceChildren(new Option(id === 'entity' ? '全部类型' : '全部环节',''), ...Object.entries(entries).map(([key,label])=>new Option(label,key)));
    }
    $('method').textContent = `资料整理日期 ${data.reviewed_at}。${data.method}`;
    const requested = new URLSearchParams(location.hash.slice(1)).get('company');
    showCompany(ids.has(requested) ? requested : data.companies[0].id);
  } catch {
    $('count').textContent = '读取失败';
    $('load-error').textContent = '研究目录加载失败，请检查网络后重试。';
    $('load-error').hidden = false; $('retry').hidden = false;
    $('empty-detail').textContent = '暂无可显示的研究资料。';
  }
}
for (const id of filterIds) $(id).addEventListener(id === 'search' ? 'input' : 'change', renderList);
$('research-tab').addEventListener('click',()=>view('research'));
$('about-tab').addEventListener('click',()=>view('about'));
$('retry').addEventListener('click',load);
for (const id of ['note','stance']) $(id).addEventListener('input',()=>{dirty=true; $('saved-at').textContent='未保存';});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
$('note-form').addEventListener('submit',event=>{
  event.preventDefault();
  if (!selected) return;
  try {
    const next = {...notes,[selected]:{stance:$('stance').value,text:$('note').value,updated_at:new Date().toISOString()}};
    validateNotes({schema_version:1,notes:next},new Set(data.companies.map(c=>c.id)));
    saveNotes(next); showCompany(selected); status('笔记已保存在此浏览器');
  } catch(error) {status(error.message);}
});
$('delete-note').addEventListener('click',()=>{
  if (!selected || !confirm('删除这家公司的本地笔记？此操作不能撤销，请先导出备份。')) return;
  try {const next={...notes};delete next[selected];saveNotes(next);showCompany(selected);status('本条笔记已删除');} catch(error){status(error.message);}
});
$('export-notes').addEventListener('click',()=>{
  if (!data) return status('目录尚未加载，请稍后再试');
  if (!notesReadable) return status('无法读取原有笔记，不能导出空文件代替备份');
  const blob=new Blob([JSON.stringify({schema_version:1,exported_at:new Date().toISOString(),notes},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob), link=node('a');link.href=url;link.download='STock-research-notes.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  status(dirty ? '已导出已保存笔记；当前未保存修改不在文件中' : '已导出笔记备份');
});
$('import-notes').addEventListener('change',async event=>{
  const file=event.target.files[0];event.target.value='';if(!file)return;
  try {
    if(!data)throw new Error('目录尚未加载，请稍后再试');
    if(file.size>1000000)throw new Error('文件超过 1 MB，未导入');
    const imported=validateNotes(JSON.parse(await file.text()),new Set(data.companies.map(c=>c.id)));
    if(!canLeave())return;
    const overlaps=Object.keys(imported).filter(id=>notes[id]);
    if(overlaps.length&&!confirm(`将覆盖 ${overlaps.length} 条同公司的已有笔记，继续？`))return;
    saveNotes({...notes,...imported});showCompany(selected);status(`已导入 ${Object.keys(imported).length} 条笔记`);
  } catch(error){status(error instanceof SyntaxError ? '不是有效的 JSON 笔记文件，未导入' : error.message);}
});
load();
