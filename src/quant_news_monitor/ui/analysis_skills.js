window.createAnalysisSkillEditor = function ({endpoint, title, description, sections, afterSave}) {
    const skillDialog = document.createElement('dialog');
    skillDialog.className = 'modal pw-skills-dialog';
    const keys = sections.map(([key]) => key);
    const titleId = `skill-title-${keys[0]}`;
    skillDialog.setAttribute('aria-labelledby', titleId);
    skillDialog.innerHTML = `<form>
      <div class="pw-heading"><h2 id="${titleId}">${title}</h2><button type="button" class="icon-button" data-close aria-label="关闭"><span data-ui-icon="x" aria-hidden="true"></span></button></div>
      <p class="form-note">${description}</p>
      <div class="pw-skills-sections">${sections.map(([key,label,hint]) => `
        <section><div class="pw-heading"><label for="pw-skill-${key}">${label}</label><label class="secondary-button pw-skill-upload">上传 .md<input type="file" data-skill-file="${key}" accept=".md,.markdown,text/markdown,text/plain"></label></div>
        <p class="form-note">${hint}</p><textarea id="pw-skill-${key}" name="${key}" rows="12" maxlength="8000" placeholder="在这里粘贴 Skill Markdown…"></textarea>
        <div class="pw-heading"><small data-count="${key}">0 / 8,000</small><button type="button" class="text-button" data-clear="${key}">清空，恢复默认</button></div></section>`).join('')}</div>
      <p class="form-note">保存后下次生成生效，已有报告不会被改写。生成时会将 Skill 与分析资料发送给所配置的模型；Markdown 中的代码不会执行。</p>
      <p class="negative" role="alert" data-error></p>
      <div class="modal-actions"><button type="button" class="secondary-button" data-close>取消</button><button class="primary-button" type="submit">保存 Skill</button></div>
    </form>`;
    document.body.append(skillDialog);
    skillDialog.querySelector('[data-ui-icon]').replaceWith(uiIcon('x'));
    const skillForm = skillDialog.querySelector('form');
    const skillError = skillDialog.querySelector('[data-error]');
    const skillCounts = () => keys.forEach(key => {
      skillDialog.querySelector(`[data-count="${key}"]`).textContent = `${skillForm.elements[key].value.length.toLocaleString('en-US')} / 8,000`;
    });
    skillDialog.querySelectorAll('[data-close]').forEach(node => node.onclick = () => skillDialog.close());
    skillForm.addEventListener('input', skillCounts);
    skillDialog.querySelectorAll('[data-clear]').forEach(node => node.onclick = () => {
      skillForm.elements[node.dataset.clear].value = ''; skillCounts();
    });
    skillDialog.querySelectorAll('[data-skill-file]').forEach(node => node.onchange = async () => {
      const file = node.files[0]; if (!file) return;
      skillError.textContent = '';
      try {
        if (!/\.(md|markdown)$/i.test(file.name) || file.size > 64000) throw Error('请选择不超过 64 KB 的 .md 或 .markdown 文件');
        const value = (await file.text()).replace(/^\uFEFF/, '');
        if (value.length > 8000 || /[\u0000\uFFFD]/.test(value)) throw Error('请使用 UTF-8 Markdown，最多 8,000 字符');
        skillForm.elements[node.dataset.skillFile].value = value; skillCounts();
      } catch (error) { skillError.textContent = error.message; }
      node.value = '';
    });
    const open = async event => {
      const trigger = event.currentTarget; trigger.disabled = true;
      try {
        const values = await api(endpoint);
        for (const key of keys) skillForm.elements[key].value = values[key] || '';
        skillError.textContent = ''; skillCounts(); skillDialog.showModal();
      } catch (error) { showToast(error.message, 'error'); }
      finally { trigger.disabled = false; }
    };
    skillForm.onsubmit = async event => {
      event.preventDefault(); const save = skillForm.querySelector('[type="submit"]'); save.disabled = true;
      skillError.textContent = '';
      try {
        await api(endpoint, {method:'POST', body:Object.fromEntries(keys.map(key => [key, skillForm.elements[key].value]))});
        skillDialog.close(); showToast('Skill 已保存，下一次生成时生效', 'success');
        await afterSave?.();
      } catch (error) { skillError.textContent = error.message; }
      finally { save.disabled = false; }
    };
    return open;
  };
