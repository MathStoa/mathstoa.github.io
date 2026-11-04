export const REPOSITORY = 'https://github.com/MathStoa/mathstoa.github.io';
export function element(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'className') node.className = value;
    else node.setAttribute(key, value);
  }
  if (tag === 'a' && attrs.href && /^https?:$/.test(node.protocol) && node.origin !== location.origin) {
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
export function safeURL(value) {
  try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : '#'; }
  catch { return '#'; }
}
export function suppliedOrcidLink(credit) {
  return element('a', {className:'orcid-link', href:safeURL(credit.orcid),
    'aria-label':'ORCID record of ' + credit.name, title:'Open ORCID record in a new tab'},
    element('img', {src:'assets/orcid.svg', alt:'', width:'16', height:'16'}));
}
export async function loadArchive() {
  const response = await fetch('data/projects.json');
  if (!response.ok) throw new Error('Archive unavailable');
  const data = await response.json();
  if (data.version !== 1 || !Array.isArray(data.projects)) throw new Error('Invalid archive');
  return data.projects;
}
export function tagList(categories) {
  return element('div', {className:'tags'}, categories.map(c => element('span', {className:'tag'}, c.label)));
}
export function projectLink(project) { return 'project.html?id=' + encodeURIComponent(project.id); }
export function dateLabel(iso) { return new Intl.DateTimeFormat('en-GB',{year:'numeric',month:'long',day:'numeric',timeZone:'UTC'}).format(new Date(iso + 'T00:00:00Z')); }
