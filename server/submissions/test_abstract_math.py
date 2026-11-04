"""Abstract source survives the existing moderation/publication boundary."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone

from .admin import SubmissionAdmin, SubmissionRevisionAdmin
from .forms import SubmissionForm
from .publication import accepted_declaration
from .models import Submission, SubmissionRevision


class AbstractMathTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='math-author')
        self.client.force_login(self.user)
        self.source = 'Study $x^2$ and $$\\frac{1}{2}$$. <img src=x onerror=alert(1)> ' + ('Research notes. ' * 3).strip()
        self.submission = Submission.objects.create(owner=self.user, title='Math project', abstract=self.source, collaborators=[{'name': 'Test, Author', 'orcid': 'https://orcid.org/0000-0002-1825-0097'}], category='math.NT', repository_url='https://github.com/example/math')

    def test_draft_and_form_escape_html_and_load_shared_renderer(self):
        for url in [reverse('submissions:detail', args=[self.submission.pk]), reverse('submissions:edit', args=[self.submission.pk]), reverse('submissions:create')]:
            response = self.client.get(url)
            self.assertContains(response, '/assets/abstract-preview.js')
            self.assertContains(response, '/assets/abstract-math.css')
            self.assertNotContains(response, '<img src=x')
            self.assertIn("script-src 'self'; style-src 'self'", response['Content-Security-Policy'])
            self.assertNotIn('unsafe-inline', response['Content-Security-Policy'])
        self.assertContains(self.client.get(reverse('submissions:detail', args=[self.submission.pk])), escape(self.source))
        form = SubmissionForm({'abstract': self.source})
        form.is_valid()
        self.assertEqual(form.cleaned_data['abstract'], self.source)

    def test_snapshot_public_json_and_feed_retain_plaintext_source(self):
        revision = SubmissionRevision.objects.create(submission=self.submission, number=1, snapshot={**self.submission.snapshot(), "publication_declaration": accepted_declaration()}, submitted_by=self.user, status='approved', reviewed_at=timezone.now())
        self.submission.published_revision = revision
        self.submission.save()
        self.assertEqual(revision.snapshot['abstract'], self.source)
        self.assertEqual(revision.public_project()['summary'], self.source)
        projects = self.client.get(reverse('archive_json')).json()['projects']
        self.assertEqual(next(p for p in projects if p['id'] == self.submission.public_id)['summary'], self.source)
        response = self.client.get(reverse('archive_feed'))
        self.assertContains(response, '$x^2$')
        self.assertNotContains(response, '<img src=x')
        self.assertNotContains(response, '<math')

    def test_moderators_preview_exact_revision_and_escape_html(self):
        revision = SubmissionRevision(submission=self.submission, snapshot={'abstract': self.source})
        preview = SubmissionRevisionAdmin(SubmissionRevision, admin.site).abstract_preview(revision)
        self.assertIn(str(escape(self.source)), preview)
        self.assertNotIn('<img src=x', preview)
        self.submission.abstract = 'Changed draft $y$'
        self.assertNotIn('Changed draft', preview)
        self.assertIn('$y$', SubmissionAdmin(Submission, admin.site).abstract_preview(self.submission))

    def test_local_renderer_module_is_served_with_javascript_mime_type(self):
        response = self.client.get('/assets/vendor/katex-0.19.1/katex.mjs')
        self.assertEqual(response.status_code, 200)
        self.assertIn(response['Content-Type'].split(';')[0], ['text/javascript', 'application/javascript'])
        # Consume the test-client wrapper so it closes the file without firing
        # connection cleanup inside this TestCase's PostgreSQL transaction.
        self.assertIn(b"KaTeX", b"".join(response.streaming_content))
