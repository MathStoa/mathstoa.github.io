# MathStoa · MathΣτοά

A small, curated archive of living mathematical research: open questions, expository projects, formalizations, and research notes that live in public Git repositories.

[Visit the pilot](https://rosati-tom.github.io/mathstoa/) · [Submit a project](https://github.com/rosati-tom/mathstoa/issues/new?template=submit-project.yml) · [Contribution policy](CONTRIBUTING.md)

MathStoa helps readers understand what a project is doing, where to read it, and how to contribute. The mathematical work stays in its original repository. A finished paper is optional.

## Pilot scope

The initial archive contains **three public examples**: mathlib, the Stacks Project, and the Liquid Tensor Experiment. They illustrate possible listings. They are not registered MathStoa projects, partnerships, or invitations from their maintainers. Their own repositories govern contributions and credit. Examples are excluded from the announcement feed.

New registrations require a project steward's permission and a human review. Research stage, contribution preferences, and correctness are separate questions: a listing or an announcement does not certify a mathematical claim.

The pilot provides search and subject filters, readable project pages, repository links, and an Atom announcement feed. Submission opens a public GitHub issue; a maintainer reviews it and updates the archive. GitHub supplies the submission account and discussion tools.

## Run locally

This is a static site with no Node dependencies. Python 3.10 or later is enough to preview it and run the checks.

The intended local checkout is `/Users/trosati/Git/Organisation and dev/MathStoa`:

```sh
git clone https://github.com/rosati-tom/mathstoa.git "/Users/trosati/Git/Organisation and dev/MathStoa"
cd "/Users/trosati/Git/Organisation and dev/MathStoa"
python3 -m http.server 4173 --directory docs
```

Open <http://localhost:4173>. Use the local server so the browser can load the archive JSON.

Before publishing an archive change:

```sh
python3 scripts/validate.py
python3 scripts/feed.py
python3 -m unittest discover -s tests -v
```

Commit the updated feed with the archive when the generator changes it.

## Content and deployment

- `docs/` is the complete public site.
- `docs/data/projects.json` is the manually reviewed archive and its portable source of truth.
- `scripts/validate.py` checks the archive contract.
- `scripts/feed.py` generates announcements from accepted, non-example entries.
- `tests/` checks the validation and feed behavior.
- `.github/ISSUE_TEMPLATE/submit-project.yml` collects proposed listings.

Publish with GitHub Pages from **branch `main`, folder `/docs`**. The pilot address is <https://rosati-tom.github.io/mathstoa/>. Project repositories and their contents remain external.

To move the site, serve the contents of `docs/` from another static host, update the public site and submission links, update the recovery base URL in `docs/404.html`, change `SITE_URL` in `scripts/feed.py`, and regenerate the feed. The archive is ordinary JSON and can be exported or imported independently of this interface. A later submission service or database can use the same stable project IDs; the current site has no application server or database to migrate. Preserve the submission issues or export their decisions if the GitHub repository also moves.

## Credit and reuse

Project credit belongs to the people and groups responsible for the work. An archive listing does not make MathStoa a co-author. Each project sets its own contribution and authorship policy; see [our contribution policy](CONTRIBUTING.md) for the requirements for listings.

The site code is available under the [MIT License](LICENSE). Original archive summaries contributed to MathStoa are available under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/): credit MathStoa, retain any supplied author attribution, and link to the source record and license. Mention changes when adapting a summary.

External repositories, linked documents, quoted material, and other third-party content retain their own rights and licenses. Listing them does not change those terms. MathStoa is an independent pilot and is not affiliated with arXiv.
