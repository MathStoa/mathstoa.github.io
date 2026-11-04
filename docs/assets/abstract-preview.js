import {renderAbstract} from './abstract-math.js';

for (const abstract of document.querySelectorAll('[data-abstract]')) {
  renderAbstract(abstract, abstract.textContent);
}
for (const input of document.querySelectorAll('textarea[name="abstract"]')) {
  const preview = document.createElement('section');
  preview.className = 'abstract-preview';
  preview.setAttribute('aria-label', 'Abstract preview');
  const heading = document.createElement('h3');
  heading.textContent = 'Abstract preview';
  const content = document.createElement('div');
  preview.append(heading, content);
  input.closest('.field, .form-row')?.append(preview);
  // No live region: announcing a whole equation on every keystroke is disruptive.
  const update = () => {
    preview.hidden = !input.value.trim();
    renderAbstract(content, input.value);
  };
  input.addEventListener('input', update);
  update();
}
