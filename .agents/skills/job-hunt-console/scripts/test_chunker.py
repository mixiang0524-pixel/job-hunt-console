"""Synthetic chunker regression tests; no real postings or candidate files."""
import unittest

import chunker


class ChunkerTests(unittest.TestCase):
    def test_compatibility_characters_and_zero_width(self):
        self.assertEqual(chunker.normalize("⼯作\u200b内容\u00a0，要求。"), "工作内容 ，要求。")

    def test_markdown_duties_and_requirements_stay_separate(self):
        text = "## 岗位职责\n1. 整理用户访谈记录并形成问题清单。\n2. 跟进需求评审并记录验收结果。\n## 任职要求\n1. 能够清楚表达分析过程并保留依据。"
        sections = chunker.sections(text)
        self.assertTrue(any("访谈" in block for block in sections["职责"]))
        self.assertTrue(any("表达" in block for block in sections["要求"]))
        self.assertFalse(any("表达" in block for block in sections["职责"]))

    def test_hyphenated_heading_is_not_a_duty(self):
        text = "-岗位职责\n1. 整理访谈记录并标注问题。\n-任职要求\n1. 具备清楚表达分析过程的能力。"
        duties = chunker.duty_blocks(text)
        self.assertTrue(duties)
        self.assertFalse(any(block.strip() == "岗位职责" for block in duties))

    def test_english_heading_tail(self):
        text = "工作职责 RESPONSIBILITIES\n1. 整理访谈记录并形成需求清单。\n任职要求\n1. 能够沟通并记录评审结论。"
        sections = chunker.sections(text)
        self.assertTrue(any("访谈" in block for block in sections["职责"]))
        self.assertFalse(any("沟通" in block for block in sections["职责"]))

    def test_metadata_is_not_work(self):
        text = "岗位基本信息\n岗位名称：样例岗位\n工作地点：样例城市\n岗位职责\n1. 整理访谈记录并形成清单。\n任职要求\n1. 能够清楚表达思路并核对资料。"
        duties = chunker.duty_blocks(text)
        self.assertFalse(any("样例城市" in block or "岗位名称" in block for block in duties))

    def test_unstructured_paragraph_remains_uncertain(self):
        text = "这是没有标题和列表的虚构散文，用于检查分段不确定时会保留提示，不能据此对岗位作出确定的职业判断。"
        self.assertFalse(chunker.reliability(text)["reliable"])


if __name__ == "__main__":
    unittest.main()
