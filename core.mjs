export const STANCES = new Set(['watch', 'bullish', 'neutral', 'bearish']);

export function selectCompanies(data, filters, notes = {}) {
  const query = String(filters.query || '').trim().toLocaleLowerCase();
  const rows = data.companies.filter(c =>
    (!filters.entity || c.entity === filters.entity) &&
    (!filters.layer || c.layers.includes(filters.layer)) &&
    (!filters.notesOnly || Boolean(notes[c.id])) &&
    (!query || [c.name, c.alias, c.ticker, c.business].join(' ').toLocaleLowerCase().includes(query)));
  return rows.sort((a, b) => filters.sort === 'name-asc' ? a.name.localeCompare(b.name, 'en') :
    filters.sort === 'name-desc' ? b.name.localeCompare(a.name, 'en') : a.order - b.order);
}

export function validateNotes(value, knownIds) {
  if (!value || value.schema_version !== 1 || !value.notes || Array.isArray(value.notes) || typeof value.notes !== 'object') throw new Error('笔记文件格式不正确');
  const notes = Object.create(null);
  for (const [id, item] of Object.entries(value.notes)) {
    if (!knownIds.has(id) || !item || !STANCES.has(item.stance) || typeof item.text !== 'string' || item.text.length > 4000 || typeof item.updated_at !== 'string' || !Number.isFinite(Date.parse(item.updated_at))) throw new Error('笔记包含未知公司或无效内容，未导入任何记录');
    notes[id] = {stance:item.stance, text:item.text, updated_at:item.updated_at};
  }
  return notes;
}

export function sourceUrl(value) {
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
