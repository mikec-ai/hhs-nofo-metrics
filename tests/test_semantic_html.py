from __future__ import annotations

import json

import pytest

from hhs_nofo_metrics import SourceBundle, analyze
from hhs_nofo_metrics.adapters.html import HtmlAdapterPlugin
from hhs_nofo_metrics.errors import AdapterContractError
from hhs_nofo_metrics.methods import split_semantic_block_sentences
from hhs_nofo_metrics.sources import materialize_source_bundle

HTML = b"""<!doctype html>
<html><body>
  <nav><p>Previous section.</p></nav>
  <main id="download_target">
    <h1>Funding opportunity</h1>
    <h7>Deep Builder heading</h7>
    <p>Applicants submit forms. Reviewers score applications.</p>
    <ul>
      <li>Complete the budget.</li>
      <li>Attach a letter</li>
    </ul>
    <table>
      <tr><th>Requirement</th><td>Explain the plan.</td></tr>
    </table>
    <p hidden>This should never appear.</p>
  </main>
</body></html>"""


def test_semantic_sentence_method_respects_block_terminal_punctuation() -> None:
    split = split_semantic_block_sentences(
        "The U.S. Department offers $1.5 million. Apply now. Supporting material"
    )

    assert split.sentences == (
        "The U.S. Department offers $1.5 million.",
        "Apply now.",
    )
    assert split.excluded_fragments == ("Supporting material",)
    assert split_semantic_block_sentences("Forms, letters, etc.").sentences == (
        "Forms, letters, etc.",
    )


def test_html_adapter_emits_ordered_semantic_blocks_from_selected_root() -> None:
    bundle = SourceBundle.from_html(HTML)
    with materialize_source_bundle(bundle) as materialized:
        result = HtmlAdapterPlugin().extract(
            materialized, config={"root_id": "download_target"}
        )

    assert [(item.role, item.text) for item in result.document.segments] == [
        ("heading", "Funding opportunity"),
        ("heading", "Deep Builder heading"),
        ("body", "Applicants submit forms. Reviewers score applications."),
        ("list", "Complete the budget."),
        ("list", "Attach a letter"),
        ("heading", "Requirement"),
        ("table", "Explain the plan."),
    ]
    assert result.coverage.matched_blocks == 7


def test_html_adapter_honors_legal_omitted_end_tags() -> None:
    html = b"""<main id=download_target>
      <p>First paragraph.<p>Second paragraph.
      <ul><li>First item.<li>Second item.</ul>
      <table><tr><th>Label<th>Value<tr><td>One<td>Two</table>
    </main>"""
    bundle = SourceBundle.from_html(html)

    with materialize_source_bundle(bundle) as materialized:
        result = HtmlAdapterPlugin().extract(
            materialized, config={"root_id": "download_target"}
        )

    assert [(item.role, item.text) for item in result.document.segments] == [
        ("body", "First paragraph."),
        ("body", "Second paragraph."),
        ("list", "First item."),
        ("list", "Second item."),
        ("heading", "Label"),
        ("heading", "Value"),
        ("table", "One"),
        ("table", "Two"),
    ]


def test_html_adapter_preserves_nested_lists_with_omitted_list_item_end_tags() -> None:
    html = b"""<main id=download_target><ul>
      <li>Parent item.<ul><li>Nested item.</ul>
      <li>Sibling item.
    </ul></main>"""
    bundle = SourceBundle.from_html(html)

    with materialize_source_bundle(bundle) as materialized:
        result = HtmlAdapterPlugin().extract(
            materialized, config={"root_id": "download_target"}
        )

    assert [(item.role, item.text) for item in result.document.segments] == [
        ("list", "Parent item."),
        ("list", "Nested item."),
        ("list", "Sibling item."),
    ]


def test_html_analysis_uses_source_structure_and_publishes_no_source_text() -> None:
    result = analyze(
        SourceBundle.from_html(HTML),
        profile="hhs-nofo-fy27-html@0.4.0",
        adapter_config={"root_id": "download_target"},
        production_path="builder_export_html",
    )

    assert result.source.kind == "html"
    assert result.result_basis == "structured_estimate"
    assert result.metrics["word_count"].value == 21
    assert result.metrics["words_per_sentence"].value == 3
    assert result.metrics["words_per_sentence"].components["sentence_count"] == 4
    assert result.metrics["words_per_sentence"].components["paragraph_count"] == 3
    assert result.metrics["words_per_sentence"].components[
        "sentences_per_paragraph"
    ] == pytest.approx(4 / 3)
    assert result.metrics["passive_sentence_percentage"].status == "calculated"
    assert result.metrics["passive_sentence_percentage"].value == 0
    assert result.coverage.role_counts == {
        "heading": 3,
        "body": 1,
        "list": 2,
        "table": 1,
    }
    assert "Applicants submit forms" not in json.dumps(result.to_dict())
    assert any(
        warning.code == "unterminated_semantic_fragments_excluded"
        for warning in result.warnings
    )


def test_current_html_profile_calculates_passive_sentence_percentage() -> None:
    html = b"""<!doctype html><html><body><main id="download_target">
      <p>Applications are reviewed by experts. Applicants submit forms.</p>
      <p>The award will be made in September. Awards remain available.</p>
    </main></body></html>"""

    result = analyze(
        SourceBundle.from_html(html),
        profile="hhs-nofo-fy27-html@0.4.0",
        adapter_config={"root_id": "download_target"},
    )

    metric = result.metrics["passive_sentence_percentage"]
    assert metric.status == "calculated"
    assert metric.value == 50
    assert metric.components["passive_sentence_count"] == 2
    assert metric.components["sentence_count"] == 4
    assert metric.method.id == "hhs-nofo-passive-sentence-rule"
    assert metric.method.version == "0.3.0-provisional"


def test_html_pipeline_rejects_wrong_source() -> None:
    with pytest.raises(AdapterContractError, match="accepts one HTML"):
        analyze(b"%PDF-1.7", profile="hhs-nofo-fy27-html@0.4.0")


def test_html_adapter_requires_requested_root() -> None:
    with pytest.raises(AdapterContractError, match="was not found"):
        analyze(
            SourceBundle.from_html(HTML),
            profile="hhs-nofo-fy27-html@0.4.0",
            adapter_config={"root_id": "missing"},
        )


def test_html_pipeline_accepts_exact_builtin_adapter_reference() -> None:
    result = analyze(
        SourceBundle.from_html(HTML),
        profile="hhs-nofo-fy27-html@0.4.0",
        adapter="hhs-semantic-html-adapter@0.1.0",
        adapter_config={"root_id": "download_target"},
    )

    assert result.adapter.id == "hhs-semantic-html-adapter"
