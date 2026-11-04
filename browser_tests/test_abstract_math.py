"""Real browser checks: pip install -r browser_tests/requirements.txt, then
playwright install chromium; python -m unittest discover -s browser_tests -v.
Set MATHSTOA_BROWSER_EXECUTABLE to use an existing Chrome installation.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'self'")
        super().end_headers()

    def log_message(self, *args):
        pass


class AbstractMathBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT / 'docs')))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'
        cls.playwright = sync_playwright().start()
        options = {'headless': True}
        if os.environ.get('MATHSTOA_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['MATHSTOA_BROWSER_EXECUTABLE']
        cls.browser = cls.playwright.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.add_init_script("window.cspViolations = []; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.violatedDirective));")
        self.page.goto(self.origin + '/submit.html')
        self.page.wait_for_function("document.querySelector('.abstract-preview') !== null")

    def preview(self, text):
        self.page.locator('#abstract').fill(text)
        return self.page.locator('.abstract-preview')

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(self.page.evaluate('window.cspViolations'), [])

    def test_inline_display_multiline_and_native_semantics(self):
        source = 'We study $x^2$ and $$\\sum_{n=1}^{\\infty}\nn^{-2}=\\pi^2/6$$.'
        preview = self.preview(source)
        self.assertEqual(preview.locator('math').count(), 2)
        self.assertEqual(preview.locator('math[display="block"]').count(), 1)
        self.assertEqual(preview.locator('annotation[encoding="application/x-tex"]').count(), 2)
        self.assertEqual(self.page.locator('#abstract').input_value(), source)
        self.assertGreater(preview.locator('math').first.bounding_box()['height'], 10)
        self.assertEqual(preview.locator('[style]').count(), 0)

    def test_plaintext_escaped_dollars_and_malformed_math(self):
        for source in ['Ordinary text\nwith a second line.', 'Unmatched $x', '$$x$', '$\\frac{$', '$\\unknown{x}$', '$x\ny$', '$$  $$']:
            with self.subTest(source=source):
                preview = self.preview(source)
                self.assertEqual(preview.locator('math').count(), 0)
                self.assertIn(source, preview.text_content())
        self.assertIn('Costs $5.', self.preview(r'Costs \$5.').text_content())
        self.assertEqual(self.preview('$x$ and $y$').locator('math').count(), 2)

    def test_untrusted_html_urls_attributes_and_macro_isolation(self):
        hostile = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>'
        preview = self.preview(hostile)
        self.assertIn(hostile, preview.text_content())
        self.assertEqual(preview.locator('img,script').count(), 0)
        for formula in [r'\href{javascript:alert(1)}{click}', r'\includegraphics{https://evil.invalid/x}', r'\htmlStyle{color:red}{x}', r'\htmlClass{injected}{x}', r'\def\a{\a}\a']:
            preview = self.preview('$' + formula + '$')
            self.assertEqual(preview.locator('a,img,script,[style],.injected').count(), 0)
        self.assertIsNone(self.page.evaluate('window.pwned'))
        preview = self.preview(r'$\gdef\secret{42}x$ and $\secret$')
        self.assertIn(r'$\secret$', preview.text_content())
        self.assertEqual(self.preview('$x^2$').locator('math').count(), 1)

    def test_unsupported_native_mathml_preserves_distinctions_as_source(self):
        # Chrome ignores these non-Core font/enclosure constructs. Never silently
        # turn distinct notation into the same plain letter or uncancelled term.
        for formula in [r'\mathbf{x}', r'\mathcal{C}', r'\mathbb{R}', r'\cancel{x}', r'\boxed{x}']:
            for delimiter in ['$', '$$']:
                source = delimiter + formula + delimiter
                with self.subTest(source=source):
                    preview = self.preview(source)
                    self.assertEqual(preview.locator('math').count(), 0)
                    self.assertEqual(preview.locator('.abstract-text').text_content(), source)
                    self.assertEqual(self.page.locator('#abstract').input_value(), source)
        self.assertEqual(self.preview(r'$\frac{1}{2}+\sqrt{x}$').locator('math').count(), 1)

    def test_transformations_rejected_before_mathml_can_drop_semantics(self):
        for formula in [r'\reflectbox{x}', r'\mathreflectbox{x}',
                        r'\def\flip{\reflectbox{x}}\flip',
                        r'\def\flip#1{\mathreflectbox{#1}}\flip{x}',
                        r'\let\flip=\mathreflectbox\frac{1}{\flip{x}}',
                        r'\def\a#1{\mathreflectbox{#1}}\def\b{\a{x}}\b']:
            for delimiter in ['$', '$$']:
                source = delimiter + formula + delimiter
                with self.subTest(source=source):
                    preview = self.preview(source)
                    self.assertEqual(preview.locator('math').count(), 0)
                    self.assertEqual(preview.locator('.abstract-text').text_content(), source)
                    self.assertEqual(self.page.locator('#abstract').input_value(), source)
        # Harmless macro aliases continue to render ordinary mathematics.
        self.assertEqual(self.preview(r'$\def\square#1{#1^2}\square{x}$').locator('math').count(), 1)

    def test_all_genfrac_commands_preserve_source_before_delimiter_loss(self):
        for left, right in [('|', '|'), (r'\vert', r'\vert'), (r'\Vert', r'\Vert'), ('(', ')'), ('', ''),
                            (r'\langle', r'\rangle'), (r'\lbrace', r'\rbrace'), (r'\lceil', r'\rceil')]:
            fraction = r'\genfrac{' + left + '}{' + right + r'}{0pt}{}{a}{b}'
            for formula in [fraction, r'\def\fraction{' + fraction + r'}\fraction',
                            r'\frac{1}{' + fraction + '}',
                            r'\let\g=\genfrac\g{' + left + '}{' + right + r'}{0pt}{}{a}{b}',
                            r'\def\g{\genfrac}\begin{pmatrix}1&\g{' + left + '}{' + right + r'}{0pt}{}{a}{b}\\c&d\end{pmatrix}',
                            r'\def\open{' + left + r'}\genfrac{\open}{' + right + r'}{0pt}{}{a}{b}']:
                for delimiter in ['$', '$$']:
                    source = delimiter + formula + delimiter
                    with self.subTest(source=source):
                        preview = self.preview(source)
                        self.assertEqual(preview.locator('math').count(), 0)
                        self.assertEqual(preview.locator('.abstract-text').text_content(), source)
        preview = self.preview(r'$\binom{a}{b}+\frac{1}{2}$')
        self.assertEqual(preview.locator('math').count(), 1)
        self.assertEqual(preview.locator('mo[fence=true]').all_text_contents(), ['(', ')'])

    def test_ordinary_matrices_render_and_cells_keep_expression_checks(self):
        for environment in ['matrix', 'pmatrix', 'bmatrix']:
            source = r'$$\begin{' + environment + r'}a&b\\c&d\end{' + environment + '}$$'
            preview = self.preview(source)
            self.assertEqual(preview.locator('math').count(), 1)
            self.assertEqual(preview.locator('mtr').count(), 2)
            self.assertEqual(preview.locator('mtd').all_text_contents(), ['a', 'b', 'c', 'd'])
        for cell in [r'\reflectbox{x}', r'\def\flip{\mathreflectbox{x}}\flip',
                     r'\genfrac{\langle}{\rangle}{0pt}{}{a}{b}']:
            source = r'$$\begin{pmatrix}1&' + cell + r'\\c&d\end{pmatrix}$$'
            preview = self.preview(source)
            self.assertEqual(preview.locator('math').count(), 0)
            self.assertEqual(preview.locator('.abstract-text').text_content(), source)

    def test_excessive_offsets_and_sizes_cannot_expand_the_page(self):
        self.page.set_viewport_size({'width': 375, 'height': 800})
        for formula in [r'\raisebox{-999999em}{x}', r'\raisebox{999999em}{x}',
                        r'\def\offset{\raisebox{-999999em}{x}}\offset', r'\kern-999999em x']:
            with self.subTest(formula=formula):
                source = '$$' + formula + '$$'
                preview = self.preview(source)
                self.assertEqual(preview.locator('math').count(), 0)
                self.assertEqual(preview.locator('.abstract-text').text_content(), source)
                self.assertLess(preview.bounding_box()['height'], 500)
                self.assertLess(self.page.evaluate('document.documentElement.scrollHeight'), 10000)
        preview = self.preview(r'$$\rule{999999em}{999999em}$$')
        self.assertLess(preview.bounding_box()['height'], 500)

    def test_inline_and_display_long_formulas_scroll_within_mobile_width(self):
        self.page.set_viewport_size({'width': 375, 'height': 800})
        for delimiter in ['$', '$$']:
            preview = self.preview('Before ' + delimiter + 'x+' * 180 + 'x' + delimiter + ' after.')
            self.assertEqual(preview.locator('math').count(), 1)
            equation = preview.locator('.abstract-equation')
            self.assertLessEqual(equation.bounding_box()['width'], 375)
            self.assertTrue(equation.evaluate('(node) => node.scrollWidth > node.clientWidth'))
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 375)
            self.assertIn('auto', equation.evaluate('(node) => getComputedStyle(node).overflowX'))

    def test_inline_styles_are_rejected_before_any_dom_style_write(self):
        self.page.evaluate("""() => {
            window.styleWrites = [];
            const original = Element.prototype.setAttribute;
            Element.prototype.setAttribute = function(name, value) {
                if (name.toLowerCase() === 'style') window.styleWrites.push(value);
                return original.call(this, name, value);
            };
        }""")
        for formula in [r'\fcolorbox{red}{blue}{x}', r'\colorbox{red}{x}',
                        r'\def\boxit{\fcolorbox{red}{blue}{x}}\boxit']:
            source = '$' + formula + '$'
            preview = self.preview(source)
            self.assertEqual(preview.locator('math').count(), 0)
            self.assertEqual(preview.locator('.abstract-text').text_content(), source)
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        self.assertEqual(self.page.evaluate('window.styleWrites'), [])
        self.assertEqual(self.page.evaluate('window.cspViolations'), [])

    def test_archive_cards_omit_abstracts_and_project_page_renders_math(self):
        data = {'version': 1, 'projects': [{
            'id': 'math-test', 'title': 'Math test project',
            'categories': [{'code': 'math.NT', 'label': 'Number theory'}],
            'credits': [{'name': 'Test Author'}], 'added': '2026-11-01',
            'repositoryUrl': 'https://github.com/example/math', 'example': False,
        }]}
        project = data['projects'][0]
        project['summary'] = 'Before $x^2$ then $$\\frac{1}{2}$$ after <img src=x onerror=alert(1)>.'
        self.page.route('**/data/projects.json', lambda route: route.fulfill(json=data))
        self.page.goto(self.origin + '/index.html')
        self.page.wait_for_selector('.project-card')
        self.assertEqual(self.page.locator('.project-card h3').text_content(), project['title'])
        self.assertEqual(self.page.locator('.project-card .card-summary, .project-card math, .project-card img').count(), 0)
        self.assertNotIn('Before', self.page.locator('.project-card').text_content())
        self.page.locator('#sort').select_option('newest')
        self.assertEqual(self.page.locator('.project-card').count(), 1)
        self.assertEqual(self.page.locator('.card-summary').count(), 0)
        # The abstract stays searchable even though the card no longer shows it.
        self.page.locator('#search').fill('Before')
        self.page.locator('#filters button[type=submit]').click()
        self.assertEqual(self.page.locator('.project-card').count(), 1)
        self.page.locator('#search').fill('absent')
        self.page.locator('#filters button[type=submit]').click()
        self.assertEqual(self.page.locator('.project-card').count(), 0)
        self.page.goto(self.origin + '/project.html?id=' + project['id'])
        self.page.wait_for_selector('.project-intro math')
        self.assertEqual(self.page.locator('.project-intro math').count(), 2)
        self.page.set_viewport_size({'width': 375, 'height': 800})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 375)
        if os.environ.get('MATHSTOA_MATH_SCREENSHOT'):
            self.page.screenshot(path=os.environ['MATHSTOA_MATH_SCREENSHOT'], full_page=True)

    def test_project_page_long_inline_and_display_math_do_not_widen_grid(self):
        self.page.set_viewport_size({'width': 375, 'height': 800})
        for delimiter in ['$', '$$']:
            data = {'version': 1, 'projects': [{
                'id': 'long-math', 'title': 'Long equation project',
                'summary': 'Before ' + delimiter + 'x+' * 180 + 'x' + delimiter + ' after.',
                'categories': [{'code': 'math.NT', 'label': 'Number theory'}],
                'credits': [{'name': 'Test Author'}], 'added': '2026-11-01',
                'repositoryUrl': 'https://github.com/example/math', 'example': False,
            }]}
            self.page.route('**/data/projects.json', lambda route: route.fulfill(json=data))
            self.page.goto(self.origin + '/project.html?id=long-math')
            self.page.wait_for_selector('.project-body math')
            self.assertEqual(self.page.locator('.project-body math').count(), 1)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 375)
            equation = self.page.locator('.project-body .abstract-equation')
            self.assertTrue(equation.evaluate('(node) => node.scrollWidth > node.clientWidth'))
            self.assertLessEqual(self.page.locator('.project-body').bounding_box()['width'], 375)
            # Also check the two-column grid, where intrinsic minimum sizing is
            # easy to miss when only testing a standalone preview container.
            self.page.set_viewport_size({'width': 800, 'height': 900})
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 800)
            self.page.set_viewport_size({'width': 375, 'height': 800})
            self.page.unroute('**/data/projects.json')

    def test_saved_abstract_markers_and_no_javascript_fallback(self):
        from html import escape
        source = 'Saved $x^2$ and $$x=1$$ <script>alert(1)</script>'
        html = ('<!doctype html><html><head><link rel="stylesheet" href="/assets/abstract-math.css"></head>'
                '<body><div data-abstract>' + escape(source) + '</div>'
                '<script type="module" src="/assets/abstract-preview.js"></script></body></html>')
        self.page.route('**/saved-draft', lambda route: route.fulfill(body=html, content_type='text/html', headers={
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'"}))
        self.page.goto(self.origin + '/saved-draft')
        self.page.wait_for_selector('[data-abstract] math')
        self.assertEqual(self.page.locator('[data-abstract] math').count(), 2)
        self.assertEqual(self.page.locator('[data-abstract] script').count(), 0)
        self.assertIn('<script>alert(1)</script>', self.page.locator('[data-abstract]').text_content())
        context = self.browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.route('**/saved-draft', lambda route: route.fulfill(body=html, content_type='text/html'))
            page.goto(self.origin + '/saved-draft')
            self.assertEqual(page.locator('[data-abstract]').text_content(), source)
        finally:
            context.close()

    def test_legacy_submission_keeps_source(self):
        source = 'Study $x^2$ with $$x=1$$. ' + 'Mathematical research. ' * 4
        self.preview(source)
        for selector, value in [('#title', 'Math project'), ('#repository', 'https://github.com/example/math'), ('#collaborators', 'Example, 0000-0002-1825-0097')]:
            self.page.locator(selector).fill(value)
        self.page.locator('#category').select_option(index=1)
        self.page.locator('#permission').check()
        self.page.locator('button[type=submit]').click()
        from urllib.parse import urlsplit, parse_qs
        href = self.page.locator('#submission-result a').get_attribute('href')
        self.assertIn(source.strip(), parse_qs(urlsplit(href).query)['body'][0])
