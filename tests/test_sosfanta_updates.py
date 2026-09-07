import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from advisor.sosfanta_updates import (
    SosFantaError,
    accept_latest,
    build_bundle,
    check_updates,
    extract_page,
    guide_url,
    semantic_diff,
    stored_status,
)


def page(role, tier="TOP", players="Alpha, Beta", prose="Alpha is the starter."):
    return f"""<html><body><article><h2 class="article-page-subtitle">{role}</h2>
    <p><a>promo</a></p><p><strong>{tier}</strong> - {players}</p><p>{prose}</p>
    </article><aside><p>unrelated news</p></aside></body></html>"""


class SosFantaUpdatesTests(unittest.TestCase):
    def test_builds_season_urls(self):
        self.assertTrue(guide_url("2026/27").endswith("2026-2027-tutti-consigli-fasce-chi-prendere/"))
        self.assertTrue(guide_url("2026/2027", 3).endswith("chi-prendere/3/"))
        with self.assertRaises(SosFantaError):
            guide_url("26/27")

    def test_extracts_only_tier_blocks_from_article(self):
        blocks = extract_page(page("PORTIERI"), "P")
        self.assertEqual(blocks, [{"tier": "TOP", "players": ["Alpha", "Beta"], "paragraphs": ["Alpha is the starter."]}])
        with self.assertRaises(SosFantaError):
            extract_page(page("DIFENSORI"), "P")

    def test_semantic_diff_limits_modified_text(self):
        old = {"roles": {"P": [{"tier": "TOP", "players": ["Alpha"], "paragraphs": ["Stable.", "Old."]}]}}
        new = {"roles": {"P": [{"tier": "TOP", "players": ["Alpha"], "paragraphs": ["Stable.", "New."]}], "D": [], "C": [], "A": []}}
        changes = semantic_diff(old, new)
        self.assertEqual(changes[0]["old_text"], ["Old."])
        self.assertEqual(changes[0]["new_text"], ["New."])

    def test_check_accept_and_bundle_flow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            versions = {"PORTIERI": "Alpha is the starter."}

            def fetcher(url):
                index = 1 if url.endswith("chi-prendere/") else int(url.rstrip("/").rsplit("/", 1)[1])
                role = {1: "PORTIERI", 2: "DIFENSORI", 3: "CENTROCAMPISTI", 4: "ATTACCANTI"}[index]
                return page(role, prose=versions.get(role, "Stable."))

            first = check_updates(root, "profile", "2026/27", fetcher)
            self.assertEqual(first["state"], "baseline_missing")
            first_hash = first["content_hash"]
            accept_latest(root, "profile", "2026/27", first_hash)
            self.assertEqual(check_updates(root, "profile", "2026/27", fetcher)["state"], "unchanged")
            self.assertEqual(stored_status(root, "profile", "2026/27")["state"], "unchanged")

            versions["PORTIERI"] = "Beta now challenges Alpha for the place."
            changed = check_updates(root, "profile", "2026/27", fetcher)
            self.assertEqual(changed["state"], "changed")
            self.assertEqual(changed["change_count"], 1)

            starters = root / "titolari.csv"
            starters.write_text("squadra,nome,id_fantacalcio,status,note\nClub,Alpha,1,TITOLARE,Old note\n", encoding="utf-8")
            with self.assertRaises(SosFantaError):
                accept_latest(root, "profile", "2026/27", first_hash)
            bundle = build_bundle(root, "profile", "2026/27", starters, changed["content_hash"])
            self.assertIn("Return JSON only", bundle)
            self.assertIn("Beta now challenges Alpha", bundle)
            self.assertIn("current_titolari_csv", bundle)
            self.assertIn("untrusted data", bundle)

    def test_rejects_corrupt_stored_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "profile" / "2026-27" / "sosfanta" / "latest.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text('{"roles": []}', encoding="utf-8")
            with self.assertRaises(SosFantaError):
                stored_status(root, "profile", "2026/27")

    def test_rejects_an_invalid_requested_season(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SosFantaError):
                stored_status(Path(temporary), "profile", "2026-27")

    def test_check_and_accept_share_a_snapshot_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roles = {1: "PORTIERI", 2: "DIFENSORI", 3: "CENTROCAMPISTI", 4: "ATTACCANTI"}

            def role_for(url):
                page_number = 1 if url.endswith("chi-prendere/") else int(url.rstrip("/").rsplit("/", 1)[1])
                return roles[page_number]

            baseline = check_updates(root, "profile", "2026/27", lambda url: page(role_for(url)))
            accept_latest(root, "profile", "2026/27", baseline["content_hash"])
            fetching = threading.Event()
            release = threading.Event()

            def changed_fetcher(url):
                fetching.set()
                release.wait(timeout=2)
                return page(role_for(url), prose="Changed.")

            with ThreadPoolExecutor(max_workers=2) as pool:
                checking = pool.submit(check_updates, root, "profile", "2026/27", changed_fetcher)
                self.assertTrue(fetching.wait(timeout=2))
                accepting = pool.submit(accept_latest, root, "profile", "2026/27", baseline["content_hash"])
                self.assertFalse(accepting.done())
                release.set()
                checking.result(timeout=2)
                with self.assertRaises(SosFantaError):
                    accepting.result(timeout=2)


if __name__ == "__main__":
    unittest.main()
