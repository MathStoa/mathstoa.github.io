import {element as el, loadArchive, tagList, projectLink} from './common.js';
const search = document.querySelector('#search');
const subject = document.querySelector('#subject');
const sort = document.querySelector('#sort');
const projects = document.querySelector('#projects');
const count = document.querySelector('#result-count');
const reset = document.querySelector('#reset');
let archive = [];
function readState() {
  const params = new URLSearchParams(location.search);
  search.value = params.get('q') || '';
  subject.value = params.get('subject') || '';
  sort.value = params.get('sort') === 'newest' ? 'newest' : 'title';
}
function card(project) {
  return el('article', {className:'project-card'},
    el('h3', {}, el('a', {href:projectLink(project)}, project.title)),
    el('p', {className:'card-summary'}, project.summary),
    tagList(project.categories),
    el('div', {className:'card-bottom'},
      el('a', {href:projectLink(project),'aria-label':'View ' + project.title}, 'View project')));
}
function render(updateURL = true) {
  const query = search.value.trim().toLocaleLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  const filtered = archive.filter(p => {
    const text = [p.title,p.summary,p.description,p.kind,...p.categories.flatMap(c => [c.code,c.label]),...p.credits.map(c => c.name)].join(' ').toLocaleLowerCase();
    return words.every(w => text.includes(w)) && (!subject.value || p.categories.some(c => c.code === subject.value));
  }).sort((a,b) => sort.value === 'newest' ? b.added.localeCompare(a.added) || a.title.localeCompare(b.title) : a.title.localeCompare(b.title));
  projects.replaceChildren(...filtered.map(card));
  if (!filtered.length) projects.append(el('div',{className:'empty-state'},
    el('h3',{},archive.length ? 'No matching projects' : 'No projects listed'),
    el('p',{},archive.length ? 'No projects match those filters. Try another subject or clear your search.' : 'Projects will appear here after review.')));
  count.textContent = `${filtered.length} ${filtered.length === 1 ? 'project' : 'projects'}`;
  reset.hidden = !query && !subject.value && sort.value === 'title';
  if (updateURL) {
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q',search.value.trim());
    if (subject.value) params.set('subject',subject.value);
    if (sort.value !== 'title') params.set('sort',sort.value);
    const suffix = params.toString();
    history.replaceState(null,'',location.pathname+(suffix ? '?'+suffix : '')+location.hash);
  }
}
document.querySelector('#filters').addEventListener('submit', e => { e.preventDefault(); render(); });
search.addEventListener('input',()=>render());
[subject,sort].forEach(field=>field.addEventListener('change',()=>render()));
reset.addEventListener('click',()=>{ search.value='';subject.value='';sort.value='title';render();search.focus(); });
window.addEventListener('popstate',()=>{readState();render(false);});
try {
  archive = await loadArchive();
  const subjects = new Map(archive.flatMap(p=>p.categories.map(c=>[c.code,c.label])));
  for (const [code,label] of [...subjects].sort((a,b)=>a[1].localeCompare(b[1]))) subject.append(el('option',{value:code},label));
  readState();render(false);
} catch {
  count.textContent='The archive could not be loaded.';
  projects.replaceChildren(el('div',{className:'empty-state'},el('h3',{},'Please try again shortly.'),el('p',{},'You can also browse the archive in our public repository.'),el('a',{href:'https://github.com/MathStoa/mathstoa.github.io/tree/main/docs/data'},'Open archive data')));
}
