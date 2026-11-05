// "Cite this project": a plain-text citation and a BibTeX entry built from the
// listing alone, so citing a project needs no network request. The archive name
// is spelt mathStoa here because both strings are pasted into bibliographies,
// where the Greek letters of the wordmark do not survive classic BibTeX.
import {element as el, dateLabel} from './common.js?v=3e4b890dc1e8';

const ARCHIVE = 'mathStoa';
const DESCRIPTION = 'Open mathematical research project';
const STOP_WORDS = new Set(['a', 'an', 'the', 'on', 'of', 'in', 'and', 'for', 'to', 'is', 'are']);

function names(project) {
  return project.credits.map(credit => credit.name.trim().replace(/\s+/g, ' ')).filter(Boolean);
}
function listNames(list) {
  return list.length < 2 ? list.join('') : list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
}
function sentence(text) { return /[.?!]$/.test(text) ? text : text + '.'; }
function year(project) { return project.added.slice(0, 4); }

export function citationText(project) {
  const listing = project.doi
    ? `${DESCRIPTION} listed on ${ARCHIVE}.`
    : `${DESCRIPTION} listed on ${ARCHIVE}, ${dateLabel(project.added)}.`;
  return `${listNames(names(project))} (${year(project)}). ${sentence(project.title.trim())} ${listing} ${project.doi || project.repositoryUrl}`;
}

// Field text that always compiles: braces become parentheses so the entry stays
// balanced, and the characters LaTeX treats specially are escaped.
function field(text) {
  return text
    .replace(/[{}]/g, brace => brace === '{' ? '(' : ')')
    .replace(/\\/g, '\\textbackslash{}')
    .replace(/\^/g, '\\textasciicircum{}')
    .replace(/~/g, '\\textasciitilde{}')
    .replace(/[&%$#_]/g, '\\$&');
}
// A name containing a comma or the word "and" would be split by BibTeX unless braced.
function author(name) {
  return /,|\band\b/i.test(name) ? `{${field(name)}}` : field(name);
}
function keyPart(text) {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}
export function bibtexKey(project) {
  const surname = keyPart((names(project)[0] || '').split(' ').pop());
  const words = project.title.trim().split(/\s+/).map(keyPart).filter(Boolean);
  const word = words.find(w => !STOP_WORDS.has(w)) || words[0] || '';
  return (surname || 'mathstoa') + year(project) + word;
}
export function bibtex(project) {
  const fields = [
    ['author', names(project).map(author).join(' and ')],
    ['title', `{${field(project.title.trim())}}`],
    ['year', year(project)],
    ['howpublished', `${DESCRIPTION} listed on ${ARCHIVE}`],
  ];
  if (project.doi) fields.push(['doi', project.doi.replace('https://doi.org/', '')]);
  fields.push(['url', project.repositoryUrl]);
  if (!project.doi) fields.push(['note', `Listed ${dateLabel(project.added)}`]);
  const body = fields.map(([name, value]) => `  ${name.padEnd(12)} = {${value}}`).join(',\n');
  return `@misc{${bibtexKey(project)},\n${body}\n}`;
}

async function copy(value) {
  try { await navigator.clipboard.writeText(value); return true; } catch {}
  try {
    const area = document.body.appendChild(el('textarea', {'aria-hidden':'true'}, value));
    area.style.position = 'fixed';
    area.style.opacity = '0';
    area.select();
    const done = document.execCommand('copy');
    area.remove();
    return done;
  } catch { return false; }
}

export function citeSection(project) {
  const text = citationText(project);
  const entry = bibtex(project);
  const status = el('span', {className:'cite-status', role:'status'});
  let timer;
  const copyButton = (label, noun, value) => {
    const button = el('button', {type:'button', className:'button button-small'}, label);
    button.addEventListener('click', async () => {
      status.textContent = await copy(value) ? noun + ' copied.' : 'Copying failed. Select the text and copy it yourself.';
      clearTimeout(timer);
      timer = setTimeout(() => { status.textContent = ''; }, 4000);
    });
    return button;
  };
  return el('section', {className:'project-cite', 'aria-labelledby':'cite-heading'},
    el('h2', {id:'cite-heading'}, 'Cite this project'),
    el('p', {className:'cite-text'}, text),
    el('div', {className:'cite-actions'},
      copyButton('Copy citation', 'Citation', text),
      copyButton('Copy BibTeX', 'BibTeX', entry),
      status),
    el('details', {className:'cite-bibtex'},
      el('summary', {}, 'Show BibTeX'),
      el('pre', {}, el('code', {}, entry))));
}
