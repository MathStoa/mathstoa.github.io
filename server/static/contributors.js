// Adds and removes contributor rows on the submission form. Without JavaScript, the rows shown are still saved.
const rows = document.querySelector('[data-contributor-rows]');
const template = document.querySelector('[data-contributor-template]');
const add = document.querySelector('[data-add-contributor]');

function renumber() {
  rows.querySelectorAll('.contributor-row').forEach((row, index) => {
    const number = index + 1;
    row.querySelector('legend').textContent = `Contributor ${number}`;
    row.querySelector('[data-remove-contributor]').setAttribute('aria-label', `Remove contributor ${number}`);
    for (const field of row.querySelectorAll('.field')) {
      const input = field.querySelector('input');
      input.id = field.querySelector('label').htmlFor = `contributor-${number}-${input.dataset.part}`;
    }
  });
}

function showRemove(row) {
  row.querySelector('[data-remove-contributor]').hidden = false;
}

if (rows && template && add) {
  rows.querySelectorAll('.contributor-row').forEach(showRemove);
  add.hidden = false;
  add.addEventListener('click', () => {
    const row = template.content.firstElementChild.cloneNode(true);
    showRemove(row);
    rows.append(row);
    renumber();
    row.querySelector('input').focus();
  });
  rows.addEventListener('click', event => {
    const row = event.target.closest('[data-remove-contributor]')?.closest('.contributor-row');
    if (!row) return;
    const neighbour = row.nextElementSibling ?? row.previousElementSibling;
    row.remove();
    renumber();
    (neighbour?.querySelector('input') ?? add).focus();
  });
}
