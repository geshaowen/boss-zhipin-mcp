import ast
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent

EXPECTED_TOOLS = {
    "boss_login": "",
    "boss_search_candidates": "keyword: str, city: str='', experience: str='', salary: str='', count: int=30",
    "boss_multi_search": "keywords: list[str]=None, city: str='', experience: str='', count_per_keyword: int=50, auto_view: bool=True",
    "boss_clear_dedup": "expect_ids: list[str]=None, status: str='', before_date: str=''",
    "boss_view_candidate": "profile_url: str",
    "boss_view_by_index": "index: int",
    "boss_view_by_expect_id": "expect_id: str",
    "boss_greet_by_index": "index: int, message: str=''",
    "boss_evaluate_candidate": "resume: dict, job_requirements: str=''",
    "boss_send_greeting": "profile_url: str, message: str=''",
    "boss_query_db": "status: str='', has_share_url: bool | None=None, keyword: str='', date_from: str='', limit: int=50",
    "boss_update_candidate": "expect_id: str, status: str='', score: int | None=None, notes: str='', share_url: str=''",
    "boss_pipeline_status": "",
    "boss_filter_and_score": "top_n: int=20",
    "boss_export_report": "top_n: int=10, include_detail: bool=True",
    "boss_debug_page": "",
    "boss_reload": "",
}

BASELINE_HASHES = {
    "scraper.py": "53359f73affa3c369ad49368ec8f053880c02a89b01a6440709aca894d89f9dd",
    "candidate_db.py": "c1b2eedb69a8c33039af680af7d49a7b8e4841ae2b12bc132e0e5b93ef789cfb",
    "evaluator.py": "f2e91fdb90d4e29e7e3e99b87f872dd6636505d9015fe55ece2842d6c81167d6",
    "ocr.py": "07111d1cda0939ca5a4c68158f3e00d09530e929766076e6309258ac3d2ff9f7",
}


def tool_contract():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("boss_"):
            continue
        if not any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            for decorator in node.decorator_list
        ):
            continue
        result[node.name] = ast.unparse(node.args)
    return result


class ContractTests(unittest.TestCase):
    def test_exactly_17_tools_are_registered(self):
        self.assertEqual(len(tool_contract()), 17)

    def test_tool_names_and_parameters_match_06a1a7d(self):
        self.assertEqual(tool_contract(), EXPECTED_TOOLS)

    def test_business_module_content_matches_06a1a7d(self):
        actual = {
            # Git may check out CRLF on Windows. Normalize only line endings so
            # the test still locks the source content from 06a1a7d.
            name: hashlib.sha256(
                (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for name in BASELINE_HASHES
        }
        self.assertEqual(actual, BASELINE_HASHES)


if __name__ == "__main__":
    unittest.main()
