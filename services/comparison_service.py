from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.feature_extractor import (
    DEFAULT_FEATURE_CONFIG,
    FEATURE_VERSION,
    FeatureConfig,
    OrbFeatures,
    extract_features,
    features_from_cache,
    features_to_cache,
    preprocess_for_features,
)
from core.image_alignment import (
    ALIGNMENT_VERSION,
    DEFAULT_ALIGNMENT_CONFIG,
    AlignmentConfig,
    AlignmentResult,
    TransformModel,
    align_features,
    ink_mask,
    make_side_by_side,
    overlay_alignment,
    pairwise_quality,
    warp_to_target,
)
from core.image_normalizer import PREPROCESSING_VERSION
from core.map_page_detector import MapPageDetector
from core.parcel_detector import (
    DEFAULT_PARCEL_CONFIG,
    ParcelConfig,
    ParcelDetection,
    ParcelMask,
    build_parcel_mask,
    detect_main_parcels,
)
from core.pdf_reader import PdfInfo
from services.cache_service import (
    AnalysisCache,
    CachedAnalysis,
    analysis_cache_key,
    file_fingerprint,
)
from services.processing_service import DocumentAnalysis, analyse_document
from utils.image_utils import encode_png, to_grayscale
from utils.logging_utils import get_logger

logger = get_logger("comparison_service")

CACHE_VERSION = f"{FEATURE_VERSION}|{PREPROCESSING_VERSION}|{ALIGNMENT_VERSION}"


@dataclass(frozen=True)
class ComparableMap:
    """A document reduced to the map image and its ORB features."""

    info: PdfInfo
    analysis: DocumentAnalysis
    image: np.ndarray
    features: OrbFeatures
    cache_hit: bool = False
    parcel: ParcelDetection | None = None
    parcel_mask: ParcelMask | None = None
    masked_image: np.ndarray | None = None
    masked_features: OrbFeatures | None = None

    @property
    def filename(self) -> str:
        return self.info.filename

    @property
    def map_page_number(self) -> int | None:
        return self.analysis.map_page_number

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape[:2]

    @property
    def has_parcel(self) -> bool:
        return self.parcel is not None and self.parcel.has_parcel

    @property
    def parcel_label(self) -> str:
        return self.parcel.label if self.parcel is not None else "-"


@dataclass(frozen=True)
class ComparisonResult:
    reference: ComparableMap
    candidate: ComparableMap
    alignment: AlignmentResult
    warped: np.ndarray | None
    overlay_png: bytes = b""
    side_by_side_png: bytes = b""
    ink_coverage: float = 0.0
    error: str = ""
    parcel_masking: bool = False
    ink_coverage_unmasked: float = 0.0
    overlay_unmasked_png: bytes = b""
    parcel_mask_png: bytes = b""
    images_parcel_free: bool = False
    parcel_note: str = ""

    @property
    def is_aligned(self) -> bool:
        return self.alignment.is_aligned

    @property
    def has_error(self) -> bool:
        return bool(self.error)

    @property
    def label(self) -> str:
        return f"{self.reference.filename} ↔ {self.candidate.filename}"

    @property
    def quality_label(self) -> str:
        return self.alignment.confidence_label

    @property
    def category(self) -> str:
        """Display category for the alignment quality (PRD 9 bands, adapted)."""
        return pairwise_quality(self.alignment.quality)

    @property
    def coverage_gain(self) -> float:
        """Difference between masked and unmasked coverage.

        Not an "improvement": the sign depends on the pair. When two parcels sit
        close together the unmasked score is *inflated* by parcel agreement, so
        masking lowers it (SU_057/SU_058: -0.08). When parcels sit far apart the
        unmasked score is *deflated*, so masking raises it (controlled test:
        +0.13). Masking is the more trustworthy direction because it measures
        base-map agreement only.
        """
        if not self.parcel_masking:
            return 0.0
        return round(self.ink_coverage - self.ink_coverage_unmasked, 4)


def _cache_path(image: np.ndarray, cache_dir: Path) -> Path:
    digest = hashlib.sha1(np.ascontiguousarray(image).tobytes()).hexdigest()[:16]
    safe_version = CACHE_VERSION.replace("|", "_")
    return Path(cache_dir) / f"{digest}_{safe_version}.npz"


def load_or_extract(
    image: np.ndarray,
    cache_dir: Path | None = None,
    config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
) -> tuple[OrbFeatures, bool]:
    """Extract ORB features, reusing a disk cache when the image is unchanged."""
    if cache_dir is not None:
        path = _cache_path(image, cache_dir)
        if path.exists():
            try:
                features = features_from_cache(path)
                if features.feature_version == FEATURE_VERSION:
                    return features, True
            except Exception as exc:  # noqa: BLE001 - a bad cache file is not fatal
                logger.warning("Cache fitur rusak (%s), dihitung ulang: %s", path.name, exc)
                path.unlink(missing_ok=True)

    features = extract_features(image, config)
    if cache_dir is not None and not features.is_empty:
        try:
            features_to_cache(features, _cache_path(image, cache_dir))
        except OSError as exc:
            logger.warning("Gagal menulis cache fitur: %s", exc)
    return features, False


def prepare_comparable(
    info: PdfInfo,
    detector: MapPageDetector | None = None,
    cache_dir: Path | None = None,
    feature_config: FeatureConfig = DEFAULT_FEATURE_CONFIG,
    parcel_config: ParcelConfig = DEFAULT_PARCEL_CONFIG,
    analysis_cache: "AnalysisCache | None" = None,
    use_analysis_cache: bool = True,
) -> ComparableMap:
    """Prepare a document for comparison.

    The analysis cache is the difference between ~1.0 s and a few milliseconds on
    a repeat run: rendering, page detection and normalisation dominate the cost,
    while ORB itself needs only ~27 ms.
    """
    cached = None
    if use_analysis_cache and analysis_cache is not None:
        cached = _load_cached(info, analysis_cache)

    if cached is not None:
        return _comparable_from_cache(info, cached, cache_dir, feature_config)

    analysis = analyse_document(info, detector or MapPageDetector())
    if analysis.normalized is None:
        raise ValueError(
            f"{info.filename} tidak memiliki halaman peta yang dapat dibandingkan."
        )

    image = analysis.normalized.image
    features, cache_hit = load_or_extract(image, cache_dir, feature_config)

    parcel = detect_main_parcels(image, parcel_config)
    mask = build_parcel_mask(image, parcel, parcel_config)

    masked_image = None
    masked_features = None
    if mask.is_empty is False:
        masked_image = mask.apply(image)
        masked_features, _ = load_or_extract(masked_image, cache_dir, feature_config)

    if use_analysis_cache and analysis_cache is not None:
        _store_analysis(info, analysis, image, masked_image, parcel, analysis_cache)

    return ComparableMap(
        info=info,
        analysis=analysis,
        image=image,
        features=features,
        cache_hit=cache_hit,
        parcel=parcel,
        parcel_mask=mask,
        masked_image=masked_image,
        masked_features=masked_features,
    )


def _load_cached(info: PdfInfo, cache: AnalysisCache) -> CachedAnalysis | None:
    if not info.is_valid:
        return None
    try:
        fingerprint = file_fingerprint(info.path)
    except OSError as exc:
        logger.warning("Tidak dapat menghitung hash %s: %s", info.filename, exc)
        return None

    from config import MAP_DETECTION_DPI

    key = analysis_cache_key(fingerprint, MAP_DETECTION_DPI)
    return cache.load(key, info)


def _store_analysis(info, analysis, image, masked_image, parcel, cache: AnalysisCache) -> None:
    from config import MAP_DETECTION_DPI

    try:
        fingerprint = file_fingerprint(info.path)
    except OSError as exc:
        logger.warning("Tidak dapat menghitung hash %s: %s", info.filename, exc)
        return

    detection = analysis.detection
    scores = ()
    if detection is not None:
        scores = tuple(
            (page.page_index, page.score, page.is_map) for page in detection.pages
        )

    polygons = ()
    confidences = ()
    if parcel is not None and parcel.has_parcel:
        polygons = tuple(candidate.polygon for candidate in parcel.candidates)
        confidences = tuple(candidate.confidence for candidate in parcel.candidates)

    cache.store(
        CachedAnalysis(
            key=analysis_cache_key(fingerprint, MAP_DETECTION_DPI),
            info=info,
            map_page=analysis.map_page_number - 1 if analysis.map_page_number else None,
            page_count=info.page_count,
            detection_scores=scores,
            normalized=image,
            masked=masked_image,
            parcel_polygons=polygons,
            parcel_confidences=confidences,
        )
    )


def _comparable_from_cache(
    info: PdfInfo,
    cached: CachedAnalysis,
    cache_dir: Path | None,
    feature_config: FeatureConfig,
) -> ComparableMap:
    """Rebuild everything from cached images without re-rendering the PDF."""
    from core.image_normalizer import (
        NormalizedImage,
        OrientationEstimate,
        SkewEstimate,
    )
    from core.map_page_detector import MapDetectionResult, PageAssessment, PageFeatures
    from core.parcel_detector import ParcelCandidate, ParcelDetection
    from services.processing_service import DocumentAnalysis

    if cached.normalized is None:
        raise ValueError(f"{info.filename}: cache tidak memuat peta.")

    image = cached.normalized
    features, cache_hit = load_or_extract(image, cache_dir, feature_config)

    # Reuse the stored polygons instead of running contour detection again; only
    # the mask itself has to be redrawn, which is a cheap polyline call.
    candidates = tuple(
        ParcelCandidate(
            polygon=polygon,
            confidence=confidence,
            dark_fraction=1.0 if confidence >= 0.5 else 0.0,
        )
        for polygon, confidence in zip(cached.parcel_polygons, cached.parcel_confidences)
    )
    parcel = ParcelDetection(candidates=candidates)
    mask = build_parcel_mask(image, parcel)

    masked = cached.masked
    if masked is None and not mask.is_empty:
        masked = mask.apply(image)
    masked_features = None
    if masked is not None:
        masked_features, _ = load_or_extract(masked, cache_dir, feature_config)

    pages = tuple(
        PageAssessment(
            page_index=index,
            is_map=is_map,
            score=score,
            features=PageFeatures(),
        )
        for index, score, is_map in cached.detection_scores
    )
    detection = MapDetectionResult(pages=pages, map_page=cached.map_page)

    # Skew and orientation are already applied to the cached image, so the
    # metadata is reported as neutral rather than pretending a fresh estimate.
    normalized = NormalizedImage(
        image=image,
        original=image,
        scale=1.0,
        skew=SkewEstimate(),
        orientation=OrientationEstimate(reason="dari cache"),
        steps=("cache",),
    )

    analysis = DocumentAnalysis(info=info, detection=detection, normalized=normalized)

    return ComparableMap(
        info=info,
        analysis=analysis,
        image=image,
        features=features,
        cache_hit=cache_hit,
        parcel=parcel,
        parcel_mask=mask,
        masked_image=masked,
        masked_features=masked_features,
    )


def ink_coverage(
    reference: np.ndarray,
    warped: np.ndarray,
    tolerance: float = 6.0,
    threshold: int = 225,
) -> float:
    """Symmetric ink agreement via distance transform.

    Binarised IoU collapses on thin line art: a four degree rotation drops IoU to
    ~0.03 even though the alignment is correct. Measuring how much ink lies within
    a few pixels of the other image's ink is stable instead (genuine pairs scored
    0.55-0.96, mismatched pairs 0.00-0.02).

    ``threshold=225`` keeps the faint base-map lines. With Otsu's ~175 the genuine
    pair only reached 0.559 because the base map was discarded entirely.
    """
    ref = to_grayscale(reference)
    cand = to_grayscale(warped)
    if ref.shape != cand.shape:
        cand = cv2.resize(cand, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)

    ref_ink = ink_mask(ref, threshold) > 0
    cand_ink = ink_mask(cand, threshold) > 0

    if not ref_ink.any() or not cand_ink.any():
        return 0.0

    dist_to_cand = cv2.distanceTransform((~cand_ink).astype(np.uint8), cv2.DIST_L2, 3)
    dist_to_ref = cv2.distanceTransform((~ref_ink).astype(np.uint8), cv2.DIST_L2, 3)

    ref_covered = float((dist_to_cand[ref_ink] <= tolerance).mean())
    cand_covered = float((dist_to_ref[cand_ink] <= tolerance).mean())
    return round(min(ref_covered, cand_covered), 4)


def compare_maps(
    reference: ComparableMap,
    candidate: ComparableMap,
    config: AlignmentConfig = DEFAULT_ALIGNMENT_CONFIG,
    render_images: bool = True,
    ignore_parcel: bool = True,
) -> ComparisonResult:
    """Align ``candidate`` onto ``reference`` and build comparison imagery.

    The transform is fitted candidate -> reference so that the fitted matrix can
    be applied directly to the candidate image. Fitting the opposite direction
    and reusing that matrix collapses ink coverage from ~0.63 to ~0.13 on a
    synthetic rotation, because the matrix would then map in the wrong way.

    When ``ignore_parcel`` is set and both maps have a detected main parcel, the
    parcel outlines are painted out before alignment and scoring. Measured on a
    controlled case (identical base map, parcels 516px apart) this lifted ink
    coverage from 0.865 to 0.998, which is the whole point of the phase.
    """
    if reference.features.is_empty or candidate.features.is_empty:
        return ComparisonResult(
            reference=reference,
            candidate=candidate,
            alignment=AlignmentResult(
                model=TransformModel.NONE,
                matrix=None,
                match_count=0,
                inlier_count=0,
                inlier_ratio=0.0,
                reprojection_error=float("inf"),
                quality=0.0,
                reason="fitur tidak cukup pada salah satu peta",
            ),
            warped=None,
            error="Fitur ORB tidak cukup untuk melakukan alignment.",
        )

    # Always compute the unmasked baseline so the user can see the parcel impact.
    baseline = _compare_once(
        reference, candidate, config, render_images=False, use_masking=False
    )
    if not baseline.is_aligned:
        return ComparisonResult(
            reference=reference,
            candidate=candidate,
            alignment=baseline.alignment,
            warped=None,
            error=f"Alignment gagal: {baseline.alignment.reason or 'tanpa model'}",
        )

    can_mask = (
        reference.masked_features is not None
        and candidate.masked_features is not None
        and not reference.masked_features.is_empty
        and not candidate.masked_features.is_empty
    )
    use_masking = ignore_parcel and can_mask

    result = baseline
    if use_masking:
        # Realign on the parcel-free images; the parcel is what we want ignored.
        masked = _compare_once(
            reference, candidate, config, render_images=False, use_masking=True
        )
        if masked.is_aligned:
            result = masked

    overlay_png = b""
    side_png = b""
    overlay_unmasked_png = b""
    mask_png = b""
    if render_images:
        overlay_png = encode_png(
            overlay_alignment(*_overlay_inputs(result))
        )
        side_png = encode_png(make_side_by_side(reference.image, candidate.image))
        if result is not baseline:
            overlay_unmasked_png = encode_png(
                overlay_alignment(*_overlay_inputs(baseline))
            )
        mask_png = _parcel_overlay_png(reference, result.warped)

    return ComparisonResult(
        reference=reference,
        candidate=candidate,
        alignment=result.alignment,
        warped=result.warped,
        overlay_png=overlay_png,
        side_by_side_png=side_png,
        ink_coverage=result.ink_coverage,
        parcel_masking=result.images_parcel_free,
        ink_coverage_unmasked=baseline.ink_coverage,
        overlay_unmasked_png=overlay_unmasked_png,
        parcel_mask_png=mask_png,
        images_parcel_free=result.images_parcel_free,
        parcel_note=_parcel_note(reference, candidate, ignore_parcel, can_mask),
    )


def _parcel_note(
    reference: ComparableMap,
    candidate: ComparableMap,
    requested: bool,
    can_mask: bool,
) -> str:
    if not requested:
        return "Masking bidang utama dimatikan."
    if not reference.has_parcel and not candidate.has_parcel:
        return "Bidang utama tidak terdeteksi pada kedua peta; tanpa masking."
    if not can_mask:
        return "Masking dilewati: fitur peta tanpa bidang tidak memadai."
    notes = []
    for comparable in (reference, candidate):
        if comparable.parcel is not None and comparable.parcel.needs_review:
            notes.append(f"{comparable.filename}: keyakinan deteksi rendah")
    if notes:
        return "Perlu pemeriksaan manual — " + "; ".join(notes)
    return "Bidang utama diabaikan saat pencocokan."


def _overlay_inputs(result: ComparisonResult) -> tuple[np.ndarray, np.ndarray]:
    """Pick the images that match the alignment actually used."""
    if result.images_parcel_free and result.reference.masked_image is not None:
        reference_image = result.reference.masked_image
    else:
        reference_image = result.reference.image
    warped = result.warped if result.warped is not None else result.candidate.image
    return reference_image, warped


def _parcel_overlay_png(
    reference: ComparableMap, warped: np.ndarray | None
) -> bytes:
    """Show where the detected parcels sit, for manual inspection."""
    if reference.parcel is None or not reference.parcel.has_parcel:
        return b""
    canvas = cv2.cvtColor(reference.image, cv2.COLOR_GRAY2BGR)
    for candidate in reference.parcel.candidates:
        cv2.polylines(canvas, [candidate.polygon], True, (0, 0, 255), 3)
    return encode_png(canvas)


def _compare_once(
    reference: ComparableMap,
    candidate: ComparableMap,
    config: AlignmentConfig,
    render_images: bool,
    use_masking: bool,
) -> ComparisonResult:
    """Single alignment pass, optionally on parcel-free images."""
    if use_masking:
        ref_image = reference.masked_image
        cand_image = candidate.masked_image
        ref_features = reference.masked_features
        cand_features = candidate.masked_features
        if ref_image is None or cand_image is None:
            use_masking = False

    if not use_masking:
        ref_image = reference.image
        cand_image = candidate.image
        ref_features = reference.features
        cand_features = candidate.features

    assert ref_image is not None and cand_image is not None
    assert ref_features is not None and cand_features is not None

    alignment = align_features(cand_features, ref_features, config)
    if not alignment.is_aligned:
        return ComparisonResult(
            reference=reference,
            candidate=candidate,
            alignment=alignment,
            warped=None,
            error=f"Alignment gagal: {alignment.reason or 'tanpa model'}",
        )

    warped = warp_to_target(cand_image, alignment, ref_image.shape)
    coverage = ink_coverage(ref_image, warped)

    return ComparisonResult(
        reference=reference,
        candidate=candidate,
        alignment=alignment,
        warped=warped,
        ink_coverage=coverage,
        images_parcel_free=use_masking,
    )


def comparison_summary(result: ComparisonResult) -> str:
    if result.has_error:
        return f"{result.label}: {result.error}"

    alignment = result.alignment
    lines = [
        f"Dokumen A: {result.reference.filename} "
        f"(peta halaman {result.reference.map_page_number})",
        f"Dokumen B: {result.candidate.filename} "
        f"(peta halaman {result.candidate.map_page_number})",
        f"Bidang utama A: {result.reference.parcel_label}",
        f"Bidang utama B: {result.candidate.parcel_label}",
        f"Model transformasi: {alignment.model.label}",
        f"Kecocokan fitur: {alignment.match_count}",
        f"Inlier: {alignment.inlier_count} ({alignment.inlier_ratio * 100:.1f}%)",
        f"Galat reproyeksi: {alignment.reprojection_error:.2f} px",
        f"Kualitas alignment: {alignment.quality:.3f} ({alignment.confidence_label})",
    ]
    if result.parcel_masking:
        lines.append(
            f"Tumpang tindih tinta (peta dasar): {result.ink_coverage:.3f}"
        )
        lines.append(
            f"Pembanding tanpa masking: {result.ink_coverage_unmasked:.3f} "
            f"(selisih {result.coverage_gain:+.3f}; arah selisih tergantung "
            f"apakah kedua bidang saling bertumpuk)"
        )
    else:
        lines.append(f"Tumpang tindih tinta: {result.ink_coverage:.3f}")
    if result.parcel_note:
        lines.append(f"Catatan bidang: {result.parcel_note}")
    if alignment.homography_rejected:
        lines.append("Catatan: homografi ditolak karena tidak stabil.")
    if alignment.reason:
        lines.append(f"Catatan: {alignment.reason}")
    return "\n".join(lines)


__all__ = [
    "CACHE_VERSION",
    "ComparableMap",
    "ComparisonResult",
    "compare_maps",
    "comparison_summary",
    "ink_coverage",
    "load_or_extract",
    "prepare_comparable",
    "preprocess_for_features",
]
