import {element as el, loadArchive, tagList, projectLink} from './common.js';
const search = document.querySelector('#search');
const subject = document.querySelector('#subject');
const sort = document.querySelector('#sort');
const projects = document.querySelector('#projects');
const count = document.querySelector('#result-count');
const reset = document.querySelector('#reset');
const heading = document.querySelector('#archive-heading');
const searchLabel = document.querySelector('label[for="search"]');
const browseProjects = document.querySelector('#browse-projects');
const browseAuthors = document.querySelector('#browse-authors');
let archive = [];
let view = 'projects';
let author = '';
let authorNames = new Map();
function cleanName(name) { return name.trim().replace(/\s+/g, ' '); }
function nameKey(name) { return cleanName(name).toLowerCase(); }
function readState() {
  const params = new URLSearchParams(location.search);
  view = params.get('view') === 'authors' ? 'authors' : 'projects';
  author = view === 'projects' ? cleanName(params.get('author') || '') : '';
  search.value = params.get('q') || '';
  subject.value = params.get('subject') || '';
  sort.value = params.get('sort') === 'newest' ? 'newest' : 'title';
}
function stateURL(nextView = view, nextAuthor = author, hash = location.hash) {
  const params = new URLSearchParams();
  if (nextView === 'authors') params.set('view', 'authors');
  else if (nextAuthor) params.set('author', nextAuthor);
  if (search.value.trim()) params.set('q', search.value.trim());
  if (subject.value) params.set('subject', subject.value);
  if (sort.value !== 'title') params.set('sort', sort.value);
  const suffix = params.toString();
  return location.pathname + (suffix ? '?' + suffix : '') + hash;
}
function updateLabels() {
  const authors = view === 'authors';
  heading.textContent = authors ? 'Browse contributors' : 'Browse projects';
  searchLabel.textContent = authors ? 'Search contributors' : 'Search projects';
  search.placeholder = authors ? 'Search contributor names' : 'Search projects, topics, or subjects';
  sort.options[0].textContent = authors ? 'Name A–Z' : 'Title A–Z';
  for (const [link, target] of [[browseProjects, 'projects'], [browseAuthors, 'authors']]) {
    if (!link) continue;
    link.href = stateURL(target, '', '#archive');
    if (view === target) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}
function card(project) {
  return el('article', {className:'project-card'},
    el('h3', {}, el('a', {href:projectLink(project)}, project.title)),
    el('p', {className:'card-summary'}, project.summary),
    tagList(project.categories),
    el('div', {className:'card-bottom'},
      el('a', {href:projectLink(project),'aria-label':'View ' + project.title}, 'View project')));
}
function isPlainClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}
function navigate(nextView, nextAuthor = '') {
  view = nextView;
  author = nextAuthor;
  const url = stateURL(view, author, '#archive');
  if (url !== location.pathname + location.search + location.hash) history.pushState(null, '', url);
  render(false);
}
function authorCard(entry) {
  const link = el('a', {href:stateURL('projects', entry.name, '#archive')}, entry.name);
  link.addEventListener('click', event => {
    if (!isPlainClick(event)) return;
    event.preventDefault();
    navigate('projects', entry.name);
  });
  return el('article', {className:'project-card'},
    el('h3', {}, link),
    el('p', {className:'card-summary'}, `${entry.projects.size} ${entry.projects.size === 1 ? 'project' : 'projects'}`));
}
function render(updateURL = true) {
  const query = search.value.trim().toLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  const inSubject = archive.filter(project => !subject.value || project.categories.some(category => category.code === subject.value));
  let filtered;
  if (view === 'authors') {
    const authors = new Map();
    for (const project of inSubject) {
      for (const credit of project.credits) {
        const key = nameKey(credit.name);
        if (!key) continue;
        if (!authors.has(key)) authors.set(key, {name:authorNames.get(key), projects:new Set(), added:project.added});
        const entry = authors.get(key);
        entry.projects.add(project.id);
        if (project.added > entry.added) entry.added = project.added;
      }
    }
    filtered = [...authors.values()].filter(entry => words.every(word => nameKey(entry.name).includes(word)))
      .sort((a, b) => sort.value === 'newest' ? b.added.localeCompare(a.added) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
    projects.replaceChildren(...filtered.map(authorCard));
  } else {
    const selectedAuthor = nameKey(author);
    filtered = inSubject.filter(project => {
      const text = [project.title,project.summary,project.description,project.kind,...project.categories.flatMap(category => [category.code,category.label]),...project.credits.map(credit => credit.name)].join(' ').toLowerCase();
      return words.every(word => text.includes(word)) && (!selectedAuthor || project.credits.some(credit => nameKey(credit.name) === selectedAuthor));
    }).sort((a, b) => sort.value === 'newest' ? b.added.localeCompare(a.added) || a.title.localeCompare(b.title) : a.title.localeCompare(b.title));
    projects.replaceChildren(...filtered.map(card));
  }
  const noun = view === 'authors' ? 'contributor' : 'project';
  if (!filtered.length) projects.append(el('div', {className:'empty-state'},
    el('h3', {}, archive.length ? `No matching ${noun}s` : `No ${noun}s listed`),
    el('p', {}, archive.length ? `No ${noun}s match those filters. Try another subject or clear your search.` : view === 'authors' ? 'Contributors will appear here when projects are listed.' : 'Projects will appear here after review.')));
  count.textContent = `${filtered.length} ${filtered.length === 1 ? noun : noun + 's'}`;
  if (view === 'projects' && author) count.textContent += ' with contributions from ' + (authorNames.get(nameKey(author)) || author);
  reset.hidden = !query && !subject.value && sort.value === 'title' && !author;
  updateLabels();
  if (updateURL) history.replaceState(null, '', stateURL());
}
document.querySelector('#filters').addEventListener('submit', event => { event.preventDefault(); render(); });
search.addEventListener('input', () => render());
[subject, sort].forEach(field => field.addEventListener('change', () => render()));
reset.addEventListener('click', () => { search.value=''; subject.value=''; sort.value='title'; author=''; render(); search.focus(); });
for (const [link, target] of [[browseProjects, 'projects'], [browseAuthors, 'authors']]) {
  link?.addEventListener('click', event => {
    if (!isPlainClick(event)) return;
    event.preventDefault();
    navigate(target);
  });
}
window.addEventListener('popstate', () => { readState(); render(false); });
try {
  archive = await loadArchive();
  authorNames = new Map();
  for (const project of archive) {
    for (const credit of project.credits) {
      const key = nameKey(credit.name);
      if (key && !authorNames.has(key)) authorNames.set(key, cleanName(credit.name));
    }
  }
  const subjects = new Map(archive.flatMap(project => project.categories.map(category => [category.code, category.label])));
  for (const [code, label] of [...subjects].sort((a, b) => a[1].localeCompare(b[1]))) subject.append(el('option', {value:code}, label));
  readState();
  render(false);
} catch {
  count.textContent='The archive could not be loaded.';
  projects.replaceChildren(el('div',{className:'empty-state'},el('h3',{},'Please try again shortly.'),el('p',{},'You can also browse the archive in our public repository.'),el('a',{href:'https://github.com/MathStoa/mathstoa.github.io/tree/main/docs/data'},'Open archive data')));
}
