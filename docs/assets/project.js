import {element as el, loadArchive, tagList, safeURL, dateLabel} from './common.js';
const container = document.querySelector('#project-content');
function section(title,...content) {return el('section',{},el('h2',{},title),...content);}
function creditLine(credit) {
  const line = el('p', {}, el('strong', {}, credit.name));
  if (credit.orcid && /^https:\/\/orcid\.org\/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(credit.orcid)) {
    line.append(el('br'), el('a', {href: credit.orcid}, 'ORCID: ' + credit.orcid.split('/').pop()));
  }
  return line;
}
try {
  const id = new URLSearchParams(location.search).get('id');
  const project = (await loadArchive()).find(p=>p.id===id);
  if (!project) {
    document.title='Project not found — mathΣτοά';
    container.replaceChildren(el('h1',{},'Project not found'),el('p',{className:'lede'},'This project is not in the archive. Check the link or browse the current entries.'),el('a',{href:'index.html#archive',className:'button'},'Explore the archive'));
  } else {
    document.title=project.title+' — mathΣτοά';
    const intro=el('header',{className:'project-intro'},el('h1',{},project.title),el('p',{className:'lede'},project.summary),tagList(project.categories));
    if(project.example) intro.append(el('p',{className:'notice project-example-note'},'This entry illustrates the archive and was not submitted by the project’s maintainers. Consult the original project for current status and participation.'));
    const article=el('article',{className:'prose'},section('About the project',el('p',{},project.description)),section('Contents',el('ul',{},project.highlights.map(t=>el('li',{},t)))),section('Attribution',...project.credits.map(creditLine)));
    const sidebar=el('aside',{className:'project-sidebar'},el('p',{className:'eyebrow'},''),el('h2',{},'Project links'),project.readingUrl?el('a',{className:'button',href:safeURL(project.readingUrl)},'Project website'):null,el('a',{className:'button button-light',href:safeURL(project.repositoryUrl)},'Repository'),el('dl',{},project.doi?[el('dt',{},'DOI'),el('dd',{},el('a',{href:safeURL(project.doi)},project.doi.replace('https://doi.org/','')))]:null,el('dt',{},'Subjects'),el('dd',{},project.categories.map(c=>c.label).join(', ')),el('dt',{},'Added to this archive'),el('dd',{},dateLabel(project.added))),el('p',{className:'sidebar-footnote'},'Archive descriptions are introductions, not certification of mathematical correctness. Follow the source for verification details and exact versions.'));
    container.replaceChildren(intro,el('div',{className:'project-layout'},article,sidebar));
  }
} catch {
  container.replaceChildren(el('h1',{},'The project could not be loaded.'),el('p',{className:'lede'},'Please try again, or browse the public archive data.'),el('a',{href:'data/projects.json'},'Open archive data'));
}
