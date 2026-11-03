import {element as el, REPOSITORY, safeURL} from './common.js';
const form=document.querySelector('#submission-form');
const result=document.querySelector('#submission-result');
const subjectBoxes=[...form.querySelectorAll('input[name="secondary"]')];
form.addEventListener('input',()=>{result.hidden=true;});
form.addEventListener('submit',event=>{
  event.preventDefault();
  for(const box of subjectBoxes)box.setCustomValidity('');
  if(!form.reportValidity())return;
  const data=new FormData(form);
  const fields=Object.fromEntries(data);
  // The main subject is listed once, however it was ticked.
  const secondary=data.getAll('secondary').filter(subject=>subject!==fields.category);
  if(secondary.length>3){const box=subjectBoxes.find(box=>box.checked);box.setCustomValidity('Please tick at most 3 further subjects.');box.reportValidity();return;}
  for(const name of ['repository','reading']) {
    const field=form.elements.namedItem(name);
    field.setCustomValidity('');
    if(fields[name] && safeURL(fields[name])==='#') {field.setCustomValidity('Please enter a public HTTPS link without a username or password.');field.reportValidity();return;}
  }
  const doi=form.elements.namedItem('doi');
  doi.setCustomValidity('');
  if(fields.doi.trim() && !/^(?:(?:https?:\/\/)?(?:dx\.)?doi\.org\/|doi:\s*)?10\.\d{4,9}(?:\.\d+)*\/[-._;()\/:a-z0-9]+$/i.test(fields.doi.trim())) {doi.setCustomValidity('Please enter a DOI such as 10.5281/zenodo.1234567, or its https://doi.org/ link.');doi.reportValidity();return;}
  const lines=[['Project title',fields.title],['Public repository',fields.repository],['DOI',fields.doi.trim()||'Not provided'],['Project website',fields.reading.trim()||'Not provided'],['Short abstract',fields.abstract],['Main subject',fields.category],['Further subjects',secondary.join(', ')||'None'],['Contributors',fields.collaborators]];
  const body=lines.map(([label,value])=>'### '+label+'\n\n'+String(value).trim()).join('\n\n')+'\n\n### Submission confirmation\n\n- [x] I maintain this project or have permission to submit it, have read the submission guidelines, and have the right to publish its abstract under CC BY 4.0.\n- [x] I understand that listing is not peer review.\n';
  const url=new URL(REPOSITORY+'/issues/new');
  url.searchParams.set('title','[Project] '+fields.title.trim());
  url.searchParams.set('body',body);
  const longDraft = url.href.length > 7000;
  if (longDraft) url.searchParams.delete('body');
  result.replaceChildren(el('h3',{},'Submission draft'),el('p',{},'Nothing has been submitted yet. Open the draft on GitHub, review it, and choose “Submit new issue”. A GitHub account is required.'),el('a',{className:'button',href:url.href,target:'_blank',rel:'noopener noreferrer'},'Review draft on GitHub'));
  if (longDraft) {
    const draft = el('textarea',{id:'draft-text',rows:'12',readOnly:'', 'aria-label':'Submission text to copy'},body);
    const note = el('p',{},'This draft is too long to include in a link. Copy the text below and paste it into the issue body on GitHub.');
    const copy = el('button',{type:'button',className:'button button-light'},'Copy draft');
    copy.addEventListener('click',async()=>{
      try { await navigator.clipboard.writeText(body); copy.textContent='Draft copied'; }
      catch { draft.focus(); draft.select(); note.textContent='Select and copy the highlighted draft, then paste it into the issue body on GitHub.'; }
    });
    result.append(note,draft,copy);
  }
  result.hidden=false;
  result.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'nearest'});
});
for (const name of ['repository','doi','reading']) form.elements.namedItem(name).addEventListener('input',event=>event.target.setCustomValidity(''));
for (const box of subjectBoxes) box.addEventListener('input',()=>{for(const other of subjectBoxes)other.setCustomValidity('');});
