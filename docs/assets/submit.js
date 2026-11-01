import {element as el, REPOSITORY, safeURL} from './common.js';
const form=document.querySelector('#submission-form');
const result=document.querySelector('#submission-result');
form.addEventListener('input',()=>{result.hidden=true;});
form.addEventListener('submit',event=>{
  event.preventDefault();
  if(!form.reportValidity())return;
  const fields=Object.fromEntries(new FormData(form));
  for(const name of ['repository','reading','policy','snapshot']) {
    const field=form.elements.namedItem(name);
    field.setCustomValidity('');
    if(fields[name] && safeURL(fields[name])==='#') {field.setCustomValidity('Please enter a public HTTPS link without a username or password.');field.reportValidity();return;}
  }
  const lines=[['Project title',fields.title],['Public repository',fields.repository],['Project website',fields.reading],['Abstract',fields.abstract],['Main subject',fields.category],['Project type',fields.kind],['Research stage',fields.stage],['Collaboration',fields.collaboration],['Where someone could help',fields.help],['Project steward and attribution',fields.steward],['Contribution policy',fields.policy],['Reuse terms',fields.license],['Version or archived snapshot',fields.snapshot||'Not provided']];
  const body=lines.map(([label,value])=>'### '+label+'\n\n'+String(value).trim()).join('\n\n')+'\n\n### Submission confirmation\n\n- [x] I maintain this project or have permission to submit it.\n- [x] I have read the submission guidelines and understand that listing is not peer review.\n- [x] I have permission to share this original abstract under CC BY 4.0, with the supplied attribution. The linked project keeps its own licence.\n';
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
for (const name of ['repository','reading','policy','snapshot']) form.elements.namedItem(name).addEventListener('input',event=>event.target.setCustomValidity(''));
