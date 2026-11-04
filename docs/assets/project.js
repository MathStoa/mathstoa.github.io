import {renderAbstract} from './abstract-math.js';
import {element as el, loadArchive, tagList, safeURL, dateLabel, suppliedOrcidLink, abstractLicense} from './common.js?v=30b3f113a369';
const container = document.querySelector('#project-content');
function section(title,...content) {return el('section',{},el('h2',{},title),...content);}
function creditLine(credit) {
  const name = credit.name.trim().replace(/\s+/g, ' ');
  const authorQuery = new URLSearchParams({author:name});
  const line = el('p', {className:'project-credit'}, el('a', {
    className:'contributor-link', href:'index.html?' + authorQuery + '#archive',
    title:'Projects with contributions from ' + name,
  }, name));
  if (credit.orcid && /^https:\/\/orcid\.org\/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(credit.orcid)) {
    line.append(el('br'), suppliedOrcidLink(credit));
  }
  return line;
}
try {
  const id = new URLSearchParams(location.search).get('id');
  const project = (await loadArchive()).find(p=>p.id===id);
  if (!project) {
    document.title='Project not found — math-stoa';
    container.replaceChildren(el('h1',{},'Project not found'),el('p',{className:'lede'},'This project is not in the archive. Check the link or browse the current entries.'),el('a',{href:'index.html#archive',className:'button'},'Explore the archive'));
  } else {
    document.title=project.title+' — math-stoa';
    const intro=el('header',{className:'project-intro'},el('h1',{},project.title),renderAbstract(el('div',{className:'lede'}),project.summary),abstractLicense(project),tagList(project.categories));
    if(project.example) intro.append(el('p',{className:'notice project-example-note'},'This entry illustrates the archive and was not submitted by the project’s maintainers. Consult the original project for current status and participation.'));
    const article=el('article',{className:'prose'},section('Contributors',...project.credits.map(creditLine)));
    const sidebar=el('aside',{className:'project-sidebar'},el('h2',{},'Project links'),project.readingUrl?el('a',{className:'button',href:safeURL(project.readingUrl),target:'_blank',rel:'noopener noreferrer',title:'Open project website in a new tab'},'Project website'):null,el('a',{className:'button button-light',href:safeURL(project.repositoryUrl),target:'_blank',rel:'noopener noreferrer',title:'Open repository in a new tab'},'Repository'),el('dl',{},project.doi?[el('dt',{},'DOI'),el('dd',{},el('a',{href:safeURL(project.doi)},project.doi.replace('https://doi.org/','')))]:null,el('dt',{},'Subjects'),el('dd',{},project.categories.map(c=>c.label).join(', ')),el('dt',{},'Added to this archive'),el('dd',{},dateLabel(project.added))));
    container.replaceChildren(el('div',{className:'project-layout'},el('div',{className:'project-body'},intro,article),sidebar));
  }
} catch {
  container.replaceChildren(el('h1',{},'The project could not be loaded.'),el('p',{className:'lede'},'Please try again, or browse the public archive data.'),el('a',{href:'data/projects.json'},'Open archive data'));
}
