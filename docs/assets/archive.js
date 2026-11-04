import {element as el, loadArchive, tagList, projectLink, suppliedOrcidLink} from './common.js?v=30b3f113a369';
const search = document.querySelector('#search');
const subject = document.querySelector('#subject');
const sort = document.querySelector('#sort');
const projects = document.querySelector('#projects');
const count = document.querySelector('#result-count');
const reset = document.querySelector('#reset');
const defaults = () => ({q:'', author:'', subject:'', sort:'title'});
let state = defaults();
let archive = [];
let status = 'loading';
const contributorNames = new Map();
function cleanName(name) { return name.trim().replace(/\s+/g, ' '); }
function folded(text) { return text.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase().replace(/[,\s]+/g, ' ').trim(); }
function nameKey(name) {
  const parts = cleanName(name).split(',').map(part => part.trim());
  return folded(parts.length > 1 ? [...parts.slice(1), parts[0]].join(' ') : parts[0]);
}
function readState() {
  const params = new URLSearchParams(location.search);
  return {
    q:params.get('q') || '',
    author:cleanName(params.get('author') || ''),
    subject:params.get('subject') || '',
    sort:params.get('sort') === 'newest' ? 'newest' : 'title',
  };
}
function stateURL(next = state, hash = location.hash) {
  const params = new URLSearchParams();
  if (next.q.trim()) params.set('q', next.q.trim());
  if (next.author) params.set('author', next.author);
  if (next.subject) params.set('subject', next.subject);
  if (next.sort !== 'title') params.set('sort', next.sort);
  const suffix = params.toString();
  return location.pathname + (suffix ? '?' + suffix : '') + hash;
}
function updateReset() {
  const applied = state.q.trim() || state.author || state.subject || state.sort !== 'title';
  reset.hidden = !applied && !search.value.trim() && !subject.value && sort.value === 'title';
}
function syncControls() {
  search.value = state.q;
  subject.value = state.subject;
  sort.value = state.sort;
}
function isPlainClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}
function apply(next, {sync = true, hash = location.hash} = {}) {
  state = next;
  const url = stateURL(state, hash);
  if (url !== location.pathname + location.search + location.hash) history.pushState(null, '', url);
  if (sync) syncControls();
  render();
}
function contributorLink(name) {
  const next = {...state, q:'', author:cleanName(name)};
  const link = el('a', {className:'contributor-link', href:stateURL(next, '#archive'),
    title:'Projects with contributions from ' + cleanName(name)}, cleanName(name));
  link.addEventListener('click', event => {
    if (!isPlainClick(event)) return;
    event.preventDefault();
    apply(next, {hash:'#archive'});
  });
  return link;
}
const ORCID_ID = /^https:\/\/orcid\.org\/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/;
function orcidLink(credit) {
  return suppliedOrcidLink(credit);
}
function card(project) {
  const contributors = new Map();
  for (const credit of project.credits) {
    const key = nameKey(credit.name);
    if (!key) continue;
    const orcid = ORCID_ID.test(credit.orcid || '') ? credit.orcid : '';
    const seen = contributors.get(key);
    if (!seen) contributors.set(key, {name:credit.name, orcid, orcidAuthenticated:credit.orcidAuthenticated === true});
    else if (!seen.orcid) {seen.orcid = orcid; seen.orcidAuthenticated = credit.orcidAuthenticated === true;}
  }
  const credits = el('p', {className:'card-credits'});
  [...contributors.values()].forEach((credit, index) => {
    if (index) credits.append('; ');
    credits.append(contributorLink(credit.name));
    if (credit.orcid) credits.append(orcidLink(credit));
  });
  return el('article', {className:'project-card'},
    tagList(project.categories),
    el('div', {className:'card-main'},
      el('h3', {}, el('a', {href:projectLink(project)}, project.title)),
      credits));
}
function matchesQuery(project, words) {
  const text = folded([project.title, project.summary, project.repositoryUrl, project.doi,
    ...project.categories.flatMap(category => [category.code, category.label]), ...project.credits.map(credit => credit.name)].join(' '));
  return words.every(word => text.includes(word));
}
function render() {
  updateReset();
  if (status === 'loading') { count.textContent = 'Loading the archive…'; return; }
  if (status === 'error') {
    count.textContent = 'The archive could not be loaded.';
    projects.replaceChildren(el('div', {className:'empty-state'}, el('h3', {}, 'Please try again shortly.'),
      el('p', {}, 'You can also browse the archive in our public repository.'),
      el('a', {href:'https://github.com/MathStoa/mathstoa.github.io/tree/main/docs/data'}, 'Open archive data')));
    return;
  }
  const query = folded(state.q);
  const words = query ? query.split(' ') : [];
  const selectedName = nameKey(state.author);
  const filtered = archive.filter(project =>
    (!state.subject || project.categories.some(category => category.code === state.subject)) &&
    (!selectedName || project.credits.some(credit => nameKey(credit.name) === selectedName)) && matchesQuery(project, words)
  ).sort((a, b) => state.sort === 'newest' ? b.added.localeCompare(a.added) || a.title.localeCompare(b.title) : a.title.localeCompare(b.title));
  projects.replaceChildren(...filtered.map(card));
  if (!filtered.length) projects.append(el('div', {className:'empty-state'},
    el('h3', {}, archive.length ? 'No matching projects' : 'No projects listed'),
    el('p', {}, archive.length ? 'No projects match this search. Try other terms or clear the filters.' : 'Projects will appear here after moderation.')));
  count.textContent = `${filtered.length} ${filtered.length === 1 ? 'project' : 'projects'}`;
  if (state.author) count.textContent += ' with contributions from ' + (contributorNames.get(selectedName) || state.author);
}
document.querySelector('#filters').addEventListener('submit', event => {
  event.preventDefault();
  apply({q:search.value.trim(), author:'', subject:subject.value, sort:sort.value});
});
search.addEventListener('input', updateReset);
subject.addEventListener('change', () => apply({...state, subject:subject.value}, {sync:false}));
sort.addEventListener('change', () => apply({...state, sort:sort.value}, {sync:false}));
reset.addEventListener('click', () => { apply(defaults()); search.focus(); });
window.addEventListener('popstate', () => { state = readState(); syncControls(); render(); });
try {
  archive = await loadArchive();
  for (const project of archive) {
    for (const credit of project.credits) {
      const key = nameKey(credit.name);
      if (key && !contributorNames.has(key)) contributorNames.set(key, cleanName(credit.name));
    }
  }
  const subjects = new Map(archive.flatMap(project => project.categories.map(category => [category.code, category.label])));
  for (const [code, label] of [...subjects].sort((a, b) => a[1].localeCompare(b[1]))) subject.append(el('option', {value:code}, label));
  status = 'ready';
} catch {
  status = 'error';
}
state = readState();
syncControls();
render();
