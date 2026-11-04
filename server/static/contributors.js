// Adds and removes contributor rows on the submission form. Without JavaScript, the rows shown are still saved.
const rows = document.querySelector('[data-contributor-rows]');
const template = document.querySelector('[data-contributor-template]');
const add = document.querySelector('[data-add-contributor]');

function renumber() {
  rows.querySelectorAll('.contributor-row').forEach((row, index) => {
    const number = index + 1;
    row.querySelector('legend').textContent = `Contributor ${number}`;
    row.querySelector('[data-remove-contributor]').setAttribute('aria-label', `Remove contributor ${number}`);
    const own = row.querySelector('[data-own-orcid]');
    if (own) own.value = String(number);
    for (const field of row.querySelectorAll('.field')) {
      const input = field.querySelector('input');
      input.id = field.querySelector('label').htmlFor = `contributor-${number}-${input.dataset.part}`;
    }
  });
}

function showRemove(row) {
  row.querySelector('[data-remove-contributor]').hidden = false;
  syncOwnOrcid(row);
}

function syncOwnOrcid(row) {
  const own = row.querySelector('[data-own-orcid]');
  if (!own) return;
  const input = row.querySelector('[data-part="orcid"]');
  if (own.checked) input.value = own.dataset.ownOrcid.replace('https://orcid.org/', '');
  input.readOnly = own.checked;
}

if (rows && template && add) {
  rows.querySelectorAll('.contributor-row').forEach(showRemove);
  add.hidden = false;
  rows.addEventListener('change', event => {
    const own = event.target.closest('[data-own-orcid]');
    if (!own) return;
    syncOwnOrcid(own.closest('.contributor-row'));
  });
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
