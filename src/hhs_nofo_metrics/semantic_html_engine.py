"""Metric engine for ordered semantic source blocks."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Final, Literal
from uuid import uuid4

from hhs_nofo_metrics.adapters import (
    AdapterDescriptor,
    AdapterResult,
    SupportAssessment,
)
from hhs_nofo_metrics.errors import ProfileError
from hhs_nofo_metrics.estimate_reliability import build_metric_reliability
from hhs_nofo_metrics.methods import (
    HHS_READABILITY_ARITHMETIC_DRAFT_REFERENCE,
    HHS_READABILITY_TOKEN_DRAFT_REFERENCE,
    PASSIVE_SENTENCE_METHOD_REFERENCE,
    SEMANTIC_BLOCK_SENTENCE_METHOD_REFERENCE,
    split_semantic_block_sentences,
    tokenize_hhs_readability_draft,
)
from hhs_nofo_metrics.methods.token_candidates import (
    READABILITY_CHARACTER_CANDIDATE_REFERENCE,
    SYLLABLE_CANDIDATE_METHOD_REFERENCE,
)
from hhs_nofo_metrics.metric_scopes import resolve_profile_metric_scopes
from hhs_nofo_metrics.models import (
    METRIC_IDS,
    AdapterIdentity,
    AnalysisResult,
    CoverageSummary,
    EngineIdentity,
    MethodIdentity,
    MetricProfile,
    MetricResult,
    NormalizedDocument,
    ProfileIdentity,
    Segment,
    SourceArtifactIdentity,
    SourceIdentity,
    WarningRecord,
)
from hhs_nofo_metrics.pdf_reflow import METHOD_REFERENCE as PDF_REFLOW_REFERENCE
from hhs_nofo_metrics.pdf_reflow import ReflowDiagnostics, reflow_flat_pdf_lines
from hhs_nofo_metrics.resolved_scope import (
    ResolvedMeasurementScope,
    calculate_resolved_measurement_scope,
)
from hhs_nofo_metrics.selection import select_metric_text
from hhs_nofo_metrics.sources import MaterializedSourceBundle
from hhs_nofo_metrics.version import PACKAGE_VERSION

RESULT_SCHEMA_VERSION: Final = "1.1.0"

_UNITS = {
    "word_count": "words",
    "words_per_sentence": "words_per_sentence",
    "characters_per_word": "characters_per_word",
    "flesch_reading_ease": "score",
    "flesch_kincaid_grade_level": "grade_level",
    "passive_sentence_percentage": "percent",
}
_METHOD_SLOTS = {
    "word_count": "tokenizer",
    "words_per_sentence": "sentence_segmenter",
    "characters_per_word": "character_counter",
    "flesch_reading_ease": "readability_formula",
    "flesch_kincaid_grade_level": "readability_formula",
    "passive_sentence_percentage": "passive_classifier",
}
_VALUE_KEYS = {
    "words_per_sentence": "words_per_sentence",
    "characters_per_word": "characters_per_word",
    "flesch_reading_ease": "flesch_reading_ease",
    "flesch_kincaid_grade_level": "flesch_kincaid_grade_level",
    "passive_sentence_percentage": "passive_sentence_percentage",
}
_EXPECTED_METHODS = {
    "tokenizer": HHS_READABILITY_TOKEN_DRAFT_REFERENCE,
    "sentence_segmenter": SEMANTIC_BLOCK_SENTENCE_METHOD_REFERENCE,
    "character_counter": READABILITY_CHARACTER_CANDIDATE_REFERENCE,
    "syllable_counter": SYLLABLE_CANDIDATE_METHOD_REFERENCE,
    "readability_formula": HHS_READABILITY_ARITHMETIC_DRAFT_REFERENCE,
}


def _method_identity(profile: MetricProfile, slot: str) -> MethodIdentity:
    configuration = profile.methods[slot]
    return MethodIdentity(
        status=configuration.status,
        id=configuration.id,
        version=configuration.version,
        reason=configuration.reason,
    )


def _validate_profile(profile: MetricProfile) -> None:
    for slot, expected in _EXPECTED_METHODS.items():
        method = profile.methods[slot]
        observed = (
            f"{method.id}@{method.version}"
            if method.status == "configured"
            else method.status
        )
        if observed != expected:
            raise ProfileError(
                f"Semantic block pipeline requires {slot} method {expected!r}; "
                f"profile declares {observed!r}"
            )
    passive_method = profile.methods["passive_classifier"]
    passive_reference = (
        f"{passive_method.id}@{passive_method.version}"
        if passive_method.status == "configured"
        else passive_method.status
    )
    if passive_reference != PASSIVE_SENTENCE_METHOD_REFERENCE:
        raise ProfileError(
            "Semantic block pipeline requires passive_classifier method "
            f"{PASSIVE_SENTENCE_METHOD_REFERENCE!r}; profile "
            f"declares {passive_reference!r}"
        )
    resolve_profile_metric_scopes(profile)


def _readability_sentences(
    segments: tuple[Segment, ...],
) -> tuple[tuple[str, ...], int, int]:
    sentences: list[str] = []
    paragraph_count = 0
    excluded_fragment_count = 0
    for segment in segments:
        split = split_semantic_block_sentences(segment.text)
        if split.sentences:
            paragraph_count += 1
        sentences.extend(split.sentences)
        excluded_fragment_count += len(split.excluded_fragments)
    return tuple(sentences), paragraph_count, excluded_fragment_count


def analyze_semantic_document(
    document: NormalizedDocument,
    *,
    source_bundle: MaterializedSourceBundle,
    adapter_descriptor: AdapterDescriptor,
    adapter_result: AdapterResult,
    adapter_configuration_sha256: str,
    support_assessment: SupportAssessment,
    profile: MetricProfile,
    profile_sha256: str,
    production_path: str,
    document_id: str | None,
    revision: str | None,
    estimate_kind: Literal["none", "tagged_pdf", "flat_pdf"] = "none",
) -> AnalysisResult:
    _validate_profile(profile)
    is_estimate = estimate_kind != "none"
    reflow_diagnostics: ReflowDiagnostics | None = None
    if estimate_kind == "flat_pdf":
        document, reflow_diagnostics = reflow_flat_pdf_lines(document)
    role_counts = Counter(segment.role for segment in document.segments)
    segment_count = len(document.segments)
    unknown_count = role_counts.get("unknown", 0)
    page_count = int(document.page_count or 0)
    extraction_error_pages = tuple(
        int(value) for value in document.metadata.get("extraction_error_pages", ())
    )
    pages_with_text = {
        segment.location.page
        for segment in document.segments
        if segment.location.page is not None and segment.text.strip()
    }
    coverage = CoverageSummary(
        page_count=page_count,
        pages_extracted=max(0, page_count - len(extraction_error_pages)),
        pages_with_text=len(pages_with_text),
        extraction_error_pages=extraction_error_pages,
        segments_total=segment_count,
        role_counts=dict(role_counts),
        unknown_role_count=unknown_count,
        classification_coverage=(
            (segment_count - unknown_count) / segment_count if segment_count else 0.0
        ),
    )
    methods = {slot: _method_identity(profile, slot) for slot in profile.methods}
    selected_by_metric = {
        metric_id: select_metric_text(document.segments, profile.metrics[metric_id])
        for metric_id in METRIC_IDS
    }
    selections = {
        metric_id: selected.summary
        for metric_id, selected in selected_by_metric.items()
    }
    word_selected = selected_by_metric["word_count"]
    readability_selected = selected_by_metric["words_per_sentence"]
    word_segments = word_selected.included_segments
    readability_segments = readability_selected.included_segments
    if is_estimate:
        readability_segments = tuple(
            segment
            for segment in readability_segments
            if segment.role != "unknown" or tokenize_hhs_readability_draft(segment.text)
        )
    readability_sentences, readability_paragraph_count, excluded_fragments = (
        _readability_sentences(readability_segments)
    )
    calculation = calculate_resolved_measurement_scope(
        ResolvedMeasurementScope(
            document_status="unable" if word_selected.unable_reason else "eligible",
            readability_status=(
                "unable" if readability_selected.unable_reason else "eligible"
            ),
            document_content_units=(
                tuple(segment.text for segment in word_segments)
                if not word_selected.unable_reason
                else ()
            ),
            readability_sentences=(
                readability_sentences if not readability_selected.unable_reason else ()
            ),
        ),
        classify_passive_voice=True,
    )
    unknown_segments = tuple(
        segment for segment in document.segments if segment.role == "unknown"
    )
    unknown_word_count = len(
        tokenize_hhs_readability_draft(
            "\n".join(segment.text for segment in unknown_segments)
        )
    )
    total_observed_word_count = len(
        tokenize_hhs_readability_draft(
            "\n".join(segment.text for segment in document.segments)
        )
    )
    excluded_unknown_calculation = None
    if is_estimate:
        excluded_word_segments = tuple(
            segment for segment in word_segments if segment.role != "unknown"
        )
        excluded_readability_segments = tuple(
            segment for segment in readability_segments if segment.role != "unknown"
        )
        excluded_readability_sentences, _, _ = _readability_sentences(
            excluded_readability_segments
        )
        excluded_unknown_calculation = calculate_resolved_measurement_scope(
            ResolvedMeasurementScope(
                document_status="eligible",
                readability_status="eligible",
                document_content_units=tuple(
                    segment.text for segment in excluded_word_segments
                ),
                readability_sentences=excluded_readability_sentences,
            ),
            classify_passive_voice=True,
        )
    metrics = {}
    for metric_id in METRIC_IDS:
        selected = selected_by_metric[metric_id]
        method = methods[_METHOD_SLOTS[metric_id]]
        if selected.unable_reason:
            metrics[metric_id] = MetricResult(
                status="unable_to_calculate",
                unit=_UNITS[metric_id],
                method=method,
                reason=selected.unable_reason,
            )
            continue
        if metric_id == "word_count":
            value = calculation.document_word_count
            assert value is not None
            reliability = (
                build_metric_reliability(
                    metric_id=metric_id,
                    included_unknown_value=value,
                    excluded_unknown_value=(
                        excluded_unknown_calculation.document_word_count
                        if excluded_unknown_calculation is not None
                        else None
                    ),
                    classification_coverage=coverage.classification_coverage,
                    unknown_segment_count=unknown_count,
                    unknown_word_count=unknown_word_count,
                    total_observed_word_count=total_observed_word_count,
                    failed_page_count=len(extraction_error_pages),
                    maximum_level="low" if estimate_kind == "flat_pdf" else None,
                    additional_reason_codes=(
                        ("flat_pdf_semantic_structure_unavailable",)
                        if estimate_kind == "flat_pdf"
                        else ()
                    ),
                )
                if is_estimate
                else None
            )
            metrics[metric_id] = MetricResult(
                status="estimated" if is_estimate else "calculated",
                unit=_UNITS[metric_id],
                method=method,
                value=value,
                components={"word_count": value},
                reliability=reliability,
            )
            continue
        counts = calculation.readability_counts
        assert counts is not None
        values = calculation.readability_metrics
        value = values[_VALUE_KEYS[metric_id]]
        components = {
            "word_count": counts.readability_word_count,
            "sentence_count": counts.sentence_count,
            "paragraph_count": readability_paragraph_count,
            "character_count": counts.character_count,
            "syllable_count": counts.syllable_count,
        }
        if readability_paragraph_count:
            components["sentences_per_paragraph"] = (
                counts.sentence_count / readability_paragraph_count
            )
        if metric_id == "passive_sentence_percentage":
            assert counts.passive_sentence_count is not None
            components["passive_sentence_count"] = counts.passive_sentence_count
        if value is None:
            metrics[metric_id] = MetricResult(
                status="unable_to_calculate",
                unit=_UNITS[metric_id],
                method=method,
                reason="The semantic sentence scope has no usable denominator.",
                components=components,
            )
        else:
            rounded_value = round(float(value), 2)
            excluded_value = (
                excluded_unknown_calculation.readability_metrics[_VALUE_KEYS[metric_id]]
                if excluded_unknown_calculation is not None
                and excluded_unknown_calculation.readability_metrics is not None
                else None
            )
            reliability = (
                build_metric_reliability(
                    metric_id=metric_id,
                    included_unknown_value=rounded_value,
                    excluded_unknown_value=(
                        round(float(excluded_value), 2)
                        if excluded_value is not None
                        else None
                    ),
                    classification_coverage=coverage.classification_coverage,
                    unknown_segment_count=unknown_count,
                    unknown_word_count=unknown_word_count,
                    total_observed_word_count=total_observed_word_count,
                    failed_page_count=len(extraction_error_pages),
                    maximum_level="low" if estimate_kind == "flat_pdf" else None,
                    additional_reason_codes=(
                        ("flat_pdf_semantic_structure_unavailable",)
                        if estimate_kind == "flat_pdf"
                        else ()
                    ),
                )
                if is_estimate
                else None
            )
            metrics[metric_id] = MetricResult(
                status="estimated" if is_estimate else "calculated",
                unit=_UNITS[metric_id],
                method=method,
                value=rounded_value,
                components={**components, "unrounded_value": float(value)},
                reliability=reliability,
            )

    warnings = [
        WarningRecord(
            code="profile_provisional",
            severity="warning",
            message=f"Profile {profile.reference} is provisional.",
        )
    ]
    if excluded_fragments:
        warnings.append(
            WarningRecord(
                code="unterminated_semantic_fragments_excluded",
                severity="info",
                message=(
                    f"{excluded_fragments} semantic block fragment(s) without "
                    "terminal sentence punctuation were excluded from readability "
                    "sentence metrics."
                ),
            )
        )
    if estimate_kind == "tagged_pdf":
        warnings.append(
            WarningRecord(
                code="pdf_estimate_reliability_attached",
                severity="info",
                message=(
                    "PDF metrics are estimates with versioned coverage and "
                    "unknown-role sensitivity evidence."
                ),
            )
        )
    elif estimate_kind == "flat_pdf":
        assert reflow_diagnostics is not None
        warnings.append(
            WarningRecord(
                code="flat_pdf_estimate_reliability_attached",
                severity="warning",
                message=(
                    "Flat PDF metrics are low-reliability estimates because "
                    "paragraphs and semantic roles were reconstructed from visual "
                    "text lines rather than supplied by authoritative source "
                    f"structure. {PDF_REFLOW_REFERENCE} reconstructed "
                    f"{reflow_diagnostics.paragraph_count} block(s) from "
                    f"{reflow_diagnostics.source_line_count} line(s), with "
                    f"{reflow_diagnostics.geometry_coverage:.1%} line-geometry "
                    "coverage."
                ),
            )
        )
    warnings.extend(
        WarningRecord(code="adapter_warning", severity="warning", message=value)
        for value in (*document.warnings, *adapter_result.warnings)
    )
    return AnalysisResult(
        schema_version="1.2.0" if is_estimate else RESULT_SCHEMA_VERSION,
        analysis_id=str(uuid4()),
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        engine=EngineIdentity(name="hhs-nofo-metrics", version=PACKAGE_VERSION),
        source=SourceIdentity(
            kind=document.source_kind,
            sha256=document.source_sha256,
            byte_length=source_bundle.primary.byte_length,
            production_path=production_path,
            document_id=document_id,
            revision=revision,
            observed_pdf_metadata=(
                document.metadata.get("observed_pdf_metadata", {})
                if document.source_kind == "pdf"
                else {}
            ),
            artifacts=tuple(
                SourceArtifactIdentity(
                    name=artifact.name,
                    kind=artifact.kind,
                    sha256=artifact.sha256,
                    byte_length=artifact.byte_length,
                    media_type=artifact.media_type,
                )
                for artifact in source_bundle.artifacts
            ),
        ),
        adapter=AdapterIdentity(
            id=document.adapter_id,
            version=document.adapter_version,
            dependencies=adapter_result.dependencies,
            contract_version=adapter_descriptor.contract_version,
            distribution=adapter_descriptor.distribution,
            distribution_version=adapter_descriptor.distribution_version,
            configuration_sha256=adapter_configuration_sha256,
            declared_capabilities=tuple(sorted(adapter_descriptor.capabilities)),
            capabilities_used=tuple(sorted(adapter_result.capabilities_used)),
            extraction_coverage=adapter_result.coverage.to_dict(),
            support_evidence=tuple(
                item.to_dict() for item in support_assessment.evidence
            ),
            extraction_evidence=tuple(
                item.to_dict() for item in adapter_result.evidence
            ),
        ),
        profile=ProfileIdentity(
            id=profile.profile_id,
            version=profile.profile_version,
            status=profile.status,
            sha256=profile_sha256,
        ),
        result_basis="structured_estimate",
        methods=methods,
        coverage=coverage,
        selection=selections,
        metrics=metrics,
        warnings=tuple(warnings),
    )


__all__ = ["analyze_semantic_document"]
