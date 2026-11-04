import katex from './vendor/katex-0.19.1/katex.mjs';

// Reject the generalized-fraction primitive before its handler normalizes away
// delimiter information. Register only this command handler, not a group builder:
// ordinary \frac and \binom share the genfrac AST type and remain supported.
// Macro aliases resolve to this same blocked primitive, including \let aliases.
// This private registration API is tied to the pinned KaTeX release below.
katex.__defineFunction({
  names:['\\genfrac'], numArgs:0, allowedInArgument:true,
  handler:() => { throw new Error('Generalized fractions are unsupported'); },
});

// Read text only. Never interpret submitted HTML, Markdown, or error messages.
const escaped = (text, index) => {
  let slashes = 0;
  while (index > 0 && text[--index] === '\\') slashes++;
  return slashes % 2 === 1;
};
const literal = text => text.replace(/\\+\$/g, value =>
  value.length % 2 === 0 ? value.slice(1) : value);

export function splitAbstract(text) {
  const parts = [];
  let start = 0;
  let i = 0;
  while (i < text.length) {
    if (text[i] !== '$' || escaped(text, i)) { i++; continue; }
    const delimiter = text.startsWith('$$', i) ? '$$' : '$';
    const bodyStart = i + delimiter.length;
    let end = bodyStart;
    for (; end < text.length; end++) {
      if (delimiter === '$' && text[end] === '\n') break;
      if (text[end] !== '$' || escaped(text, end)) continue;
      const closing = text.startsWith('$$', end) ? '$$' : '$';
      if (closing === delimiter) break;
      end++; // Never split a double delimiter into two inline delimiters.
    }
    if (end === text.length || text[end] !== '$' || !text.slice(bodyStart, end).trim()) {
      i = bodyStart;
      continue;
    }
    parts.push({text:literal(text.slice(start, i))});
    parts.push({text:text.slice(bodyStart, end), raw:text.slice(i, end + delimiter.length), display:delimiter === '$$'});
    i = end + delimiter.length;
    start = i;
  }
  parts.push({text:literal(text.slice(start))});
  return parts;
}

// MathML Core is deliberately narrower than KaTeX's MathML output. In
// particular, browsers can silently ignore font variants and menclose. Keep the
// original formula whenever the output would lose meaning or require CSS.
const mathNamespace = 'http://www.w3.org/1998/Math/MathML';
const coreElements = new Set(('math semantics annotation mrow mi mn mo mtext mspace mfrac ' +
  'msqrt mroot mstyle msub msup msubsup munder mover munderover ' +
  'mtable mtr mtd mphantom').split(' '));
const coreAttributes = new Set(('xmlns display encoding mathvariant stretchy fence separator ' +
  'accent accentunder largeop movablelimits form lspace rspace minsize maxsize ' +
  'width height depth linethickness scriptlevel displaystyle columnalign rowalign ' +
  'columnspacing rowspacing columnspan rowspan').split(' '));
const dimensions = new Set(('lspace rspace minsize maxsize width height depth ' +
  'linethickness columnspacing rowspacing').split(' '));

// Output inspection alone is insufficient: some KaTeX builders discard a
// transformation (for example reflectbox) before producing MathML. Check the
// expanded parse tree, so aliases and nested macro expansions get the same
// treatment. This private API is tied to our pinned, integrity-checked version;
// a renderer upgrade must re-review this small supported node set.
const supportedParseNodes = new Set(('mathord textord atom ordgroup supsub ' +
  'genfrac sqrt op text styling spacing kern leftright middle delimsizing array').split(' '));

function validateExpression(source, displayMode) {
  const tree = katex.__parse(source, {
    displayMode, trust:false, strict:'error', maxExpand:200, maxSize:10, macros:{},
  });
  let count = 0;
  const visit = (node, depth) => {
    if (!node || typeof node !== 'object') return;
    if (++count > 1000 || depth > 64) throw new Error('Formula too complex');
    if (node.type && !supportedParseNodes.has(node.type)) throw new Error('Unsupported expression');
    if (node.type === 'array') {
      // cols is layout metadata, not an expression subtree. Validate its exact
      // ordinary-matrix shape before skipping it; cells still recurse normally.
      if (!Array.isArray(node.cols) || node.cols.length > 20 || !node.cols.every(
        column => column.type === 'align' && ['l', 'c', 'r'].includes(column.align) &&
          Object.keys(column).every(key => key === 'type' || key === 'align')
      )) throw new Error('Unsupported table layout');
    }
    for (const [key, value] of Object.entries(node)) {
      if (key !== 'loc' && !(node.type === 'array' && key === 'cols')) visit(value, depth + 1);
    }
  };
  visit(tree, 0);
}

function safeMathML(source, displayMode) {
  // String generation cannot apply inline styles. Never call katex.render: it
  // creates style attributes before a caller could reject the resulting DOM.
  validateExpression(source, displayMode);
  const markup = katex.renderToString(source, {
    output:'mathml', displayMode, throwOnError:true,
    trust:false, strict:'error', maxExpand:200, maxSize:10, macros:{},
  });
  if (/\sstyle\s*=/i.test(markup)) throw new Error('Inline CSS is unsupported');
  // XML parsing is inert: no HTML parsing, resource loads or CSS application.
  const parsed = new DOMParser().parseFromString(markup, 'application/xml');
  const math = parsed.documentElement.firstElementChild;
  if (parsed.querySelector('parsererror') || !math || math.localName !== 'math') {
    throw new Error('Invalid MathML');
  }
  const nodes = [math, ...math.querySelectorAll('*')];
  if (nodes.length > 500) throw new Error('Formula too complex');
  for (const node of nodes) {
    if (node.namespaceURI !== mathNamespace || !coreElements.has(node.localName)) {
      throw new Error('Unsupported MathML element');
    }
    for (const {name, value} of node.attributes) {
      if (!coreAttributes.has(name)) throw new Error('Unsupported MathML attribute');
      if (name === 'mathvariant' && value !== 'normal') throw new Error('Unsupported font variant');
      if (name === 'scriptlevel' && !/^[012]$/.test(value)) throw new Error('Unsupported script size');
      if ((name === 'columnspan' || name === 'rowspan') && !/^(?:[1-9]|1[0-9]|20)$/.test(value)) {
        throw new Error('Table span too large');
      }
      if (dimensions.has(name) && !value.split(/\s+/).every(size => {
        const match = /^(\d+(?:\.\d+)?)(?:em|ex|px|pt)?$/.exec(size);
        return match && Number(match[1]) <= 10;
      })) throw new Error('Unsupported or excessive dimension');
    }
  }
  return document.importNode(math, true);
}

export function renderAbstract(target, text) {
  const fragment = document.createDocumentFragment();
  for (const part of splitAbstract(text)) {
    if (part.raw === undefined) { fragment.append(document.createTextNode(part.text)); continue; }
    const math = document.createElement('span');
    math.className = part.display ? 'abstract-equation abstract-equation-display' : 'abstract-equation';
    try {
      math.append(safeMathML(part.text, part.display));
      fragment.append(math);
    } catch {
      // Preserve unsupported or malformed input as literal, selectable text.
      fragment.append(document.createTextNode(part.raw));
    }
  }
  target.replaceChildren(fragment);
  target.classList.add('abstract-text');
  return target;
}
