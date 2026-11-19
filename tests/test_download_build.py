"""A first release must have a usable download/guide page, including without JS."""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.ids = []
        self.labels = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        self.labels.extend(attrs.get("aria-labelledby", "").split())
        if "href" in attrs:
            self.hrefs.append(attrs["href"])
        if tag == "script":
            self.hrefs.append(attrs["src"])


class TestPublicDownload(unittest.TestCase):
    def test_built_page_references_own_release_guide_and_relative_assets(self):
        source = Path(__file__).resolve().parents[1] / "site" / "build.py"
        spec = importlib.util.spec_from_file_location("site_builder", source)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            for repo in ("user/test-repo", "example/example.github.io"):
                builder.build(repo, output)
                html = (output / "index.html").read_text(encoding="utf-8")
                self.assertNotIn("{{", html)
                links = Links()
                links.feed(html)
                self.assertEqual(len(links.ids), len(set(links.ids)))
                self.assertTrue(set(links.labels) <= set(links.ids))
                for href in links.hrefs:
                    if href.startswith("#"):
                        self.assertIn(href[1:], links.ids)
                    elif href.startswith("./"):
                        self.assertTrue((output / href).exists(), href)
                    elif href.startswith("https://github.com/"):
                        self.assertTrue(href.startswith(f"https://github.com/{repo}"), href)
                self.assertIn(f"https://github.com/{repo}/releases", links.hrefs)
                self.assertIn(f"https://github.com/{repo}/blob/HEAD/docs/GUIDE.md", links.hrefs)
                self.assertTrue((output / ".nojekyll").is_file())
            with self.assertRaises(ValueError):
                builder.build("example/..", output)
