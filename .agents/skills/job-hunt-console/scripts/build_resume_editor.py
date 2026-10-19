"""Build the offline, editable resume sheet. No network or profile writes."""
import argparse
import hashlib
import html
import json
from pathlib import Path

from common import load_config, out_dir


def cell(text="", align="left", placeholder="文字", bold=False):
    value = html.escape(text).replace("\n", "<br>")
    return {"html": f"<b>{value}</b>" if value and bold else value,
            "align": align, "hidden": False, "placeholder": placeholder}


def initial_document(cfg, candidate=None, result=None, review=None):
    candidate, result = candidate or {}, result or {}
    options = cfg.get("resume_editor", {})
    scope = result.get("task_hash") or hashlib.sha256(
        str(cfg.get("_base_dir", "")).encode()).hexdigest()[:16]
    sections = []
    if result:
        for i, section in enumerate(result["sections"]):
            rows = [{"id": f"s{i}-r{j}", "kind": "text", "hidden": False,
                     "cells": [cell(item["text"], placeholder="正文")],
                     "fact_ids": item["fact_ids"]}
                    for j, item in enumerate(section["items"])]
            sections.append({"id": f"s{i}", "title": cell(section["title"]),
                             "hidden": False, "pageBreak": False, "rows": rows})
    else:
        for i, title in enumerate(options.get("section_titles", [])):
            rows = [
                {"id": f"s{i}-r0", "kind": "columns", "hidden": False,
                 "cells": [cell(placeholder="日期"), cell(align="center", placeholder="学校／公司"),
                           cell(align="right", placeholder="专业／岗位")]},
                {"id": f"s{i}-r1", "kind": "text", "hidden": False,
                 "cells": [cell(placeholder="在这里输入正文，可换行、加粗或继续添加文字行")]}]
            if title in options.get("text_only_section_titles", []):
                rows = rows[1:]
            sections.append({"id": f"s{i}", "title": cell(title), "hidden": False,
                             "pageBreak": False, "rows": rows})
    return {"schema_version": 1, "document_id": scope,
            "name": cell(candidate.get("name", ""), "center", "姓名"),
            "contact": cell(" ｜ ".join(candidate[k] for k in ("phone", "email", "location")
                                       if candidate.get(k)), "center", "电话 ｜ 邮箱"),
            "intent": cell(result.get("headline", ""), "center", "求职意向"),
            "showIntent": options.get("show_intent", False),
            "settings": {"fontSize": 10.5, "lineHeight": 1.55, "margin": 12,
                         "sectionGap": 12, "rowGap": 4},
            "sections": sections,
            "origin": {"status": "manual_draft_requires_content_review",
                       "source_review": review or {}, "generated_result": result}}


def render_editor(document):
    template = Path(__file__).resolve().parent.parent / "templates" / "resume-editor.html"
    data = json.dumps(document, ensure_ascii=False).replace("<", "\\u003c")
    return template.read_text(encoding="utf-8").replace("__INITIAL_DOCUMENT__", data)


def write_editor(cfg, output=None, document=None):
    path = Path(output or out_dir(cfg, "简历编辑器.html"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_editor(document or initial_document(cfg)), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--track")
    parser.add_argument("--output")
    args = parser.parse_args()
    print(write_editor(load_config(args.config, args.track), args.output))


if __name__ == "__main__":
    main()
