#!/usr/bin/env python3
"""Flask web interface for RosdepAuditor package distribution detection."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rosdep_auditor.config import load_config  # noqa: E402
from rosdep_auditor.distribution_table import DistributionTable  # noqa: E402
from rosdep_auditor.package_distribution_detector import PackageDistributionDetector  # noqa: E402


CONFIG_PATH = PROJECT_ROOT / "rosdep_auditor" / "configs" / "config_mini.yaml"

app = Flask(__name__)
detector: Optional[PackageDistributionDetector] = None
detector_error: Optional[str] = None


def initialize_detector(force: bool = False) -> Optional[PackageDistributionDetector]:
    """Initialize the detector lazily so imports and health checks stay cheap."""
    global detector, detector_error

    if detector is not None and not force:
        return detector

    try:
        config = load_config(str(CONFIG_PATH))
        detector = PackageDistributionDetector(config)
        detector_error = None
        return detector
    except Exception as exc:  # pragma: no cover - surfaced through /health
        detector = None
        detector_error = str(exc)
        app.logger.exception("Failed to initialize PackageDistributionDetector")
        return None


def _current_detector() -> PackageDistributionDetector:
    active_detector = initialize_detector()
    if active_detector is None:
        raise RuntimeError(detector_error or "Detector failed to initialize")
    return active_detector


@app.route("/")
def index():
    """Render the main distribution detector page."""
    return render_template("index.html")


@app.route("/api/repositories", methods=["GET"])
def get_supported_repositories():
    """Return configured repositories and versions for the input form."""
    try:
        active_detector = _current_detector()
        supported_versions = active_detector.config.get("supported_versions", {})
        repositories = [
            {"name": repo_name, "versions": versions or [""]}
            for repo_name, versions in sorted(supported_versions.items())
        ]
        return jsonify({"success": True, "repositories": repositories})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/detect", methods=["POST"])
def detect_distribution():
    """Run distribution detection for a baseline package."""
    try:
        active_detector = _current_detector()
        payload = request.get_json(silent=True) or {}

        package_name = str(payload.get("package_name", "")).strip()
        repo_name = str(payload.get("repo_name", "")).strip()
        repo_version = str(payload.get("repo_version", "")).strip()
        if repo_version == "__default__":
            repo_version = ""
        method = str(payload.get("method", "standard")).strip() or "standard"
        options = _normalize_options(payload.get("options") or {})

        supported_versions = active_detector.config.get("supported_versions", {})
        repo_versions = supported_versions.get(repo_name, [])
        missing_fields = [
            field
            for field, value in (("package_name", package_name), ("repo_name", repo_name))
            if not value
        ]
        if not repo_version and "" not in repo_versions:
            missing_fields.append("repo_version")
        if missing_fields:
            return jsonify({
                "success": False,
                "error": f"Missing required field(s): {', '.join(missing_fields)}",
            }), 400

        if method not in {"standard", "llm", "multi_mode"}:
            return jsonify({"success": False, "error": f"Unsupported method: {method}"}), 400

        specific_package = (package_name, repo_name, repo_version)
        baseline_package = {
            "name": package_name,
            "repo": repo_name,
            "version": repo_version,
        }

        if method == "llm":
            table = active_detector.detect_distribution_with_llm(
                specific_package=specific_package,
                preset=options["llm_preset"],
            )
            return jsonify({
                "success": True,
                "method": method,
                "baseline_package": baseline_package,
                "distribution_table": _format_distribution_table(table),
            })

        if method == "multi_mode":
            multi_result = active_detector.detect_distribution(
                keyword=package_name,
                specific_package=specific_package,
                top_k=options["top_k"],
                show_scores=options["show_scores"],
                score_threshold=options["score_threshold"],
                use_name_matches=options["use_name_matches"],
                use_source_related=options["use_source_related"],
                return_all_candidates=True,
                use_cache=options["use_cache"],
                w=options["weight"],
                scorer=options["scorer"],
            )
            if isinstance(multi_result, tuple):
                standard_table, all_candidates_table = multi_result
            else:
                standard_table = multi_result
                all_candidates_table = DistributionTable([])

            llm_error = None
            try:
                llm_table = active_detector.detect_distribution_with_llm(
                    specific_package=specific_package,
                    preset=options["llm_preset"],
                )
            except Exception as exc:  # Keep standard results usable if LLM is unavailable.
                llm_error = str(exc)
                llm_table = DistributionTable([])

            response: dict[str, Any] = {
                "success": True,
                "method": method,
                "baseline_package": baseline_package,
                "standard_distribution": _format_distribution_table(standard_table),
                "llm_distribution": _format_distribution_table(llm_table),
                "all_candidates": _format_distribution_table(all_candidates_table),
            }
            if llm_error:
                response["errors"] = {"llm": llm_error}
            return jsonify(response)

        table = active_detector.detect_distribution(
            keyword=package_name,
            specific_package=specific_package,
            top_k=options["top_k"],
            show_scores=options["show_scores"],
            score_threshold=options["score_threshold"],
            use_name_matches=options["use_name_matches"],
            use_source_related=options["use_source_related"],
            use_cache=options["use_cache"],
            w=options["weight"],
            scorer=options["scorer"],
        )
        return jsonify({
            "success": True,
            "method": method,
            "baseline_package": baseline_package,
            "distribution_table": _format_distribution_table(table),
        })

    except Exception as exc:
        app.logger.exception("Distribution detection failed")
        return jsonify({"success": False, "error": f"Detection failed: {exc}"}), 500


@app.route("/health")
def health_check():
    """Report application and detector initialization status."""
    active_detector = initialize_detector()
    return jsonify({
        "status": "healthy" if active_detector is not None else "unhealthy",
        "detector_initialized": active_detector is not None,
        "config_path": str(CONFIG_PATH),
        "error": detector_error,
    })


def _normalize_options(raw_options: dict[str, Any]) -> dict[str, Any]:
    return {
        "top_k": _clamp_int(raw_options.get("top_k"), default=5, minimum=1, maximum=50),
        "score_threshold": _clamp_float(
            raw_options.get("score_threshold"), default=0.0, minimum=0.0, maximum=1.0
        ),
        "show_scores": bool(raw_options.get("show_scores", True)),
        "use_name_matches": bool(raw_options.get("use_name_matches", True)),
        "use_source_related": bool(raw_options.get("use_source_related", True)),
        "use_cache": bool(raw_options.get("use_cache", True)),
        "weight": _clamp_float(raw_options.get("weight"), default=0.7, minimum=0.0, maximum=1.0),
        "scorer": str(raw_options.get("scorer") or "default"),
        "llm_preset": str(raw_options.get("llm_preset") or "default"),
    }


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _format_distribution_table(distribution_table: Any) -> dict[str, Any]:
    """Convert DistributionTable-like objects into JSON-safe response data."""
    if not distribution_table:
        return {"repositories": [], "total_packages": 0}

    repositories = []
    for repo_key, package_set in sorted(distribution_table.items(), key=lambda item: item[0]):
        packages = [_format_package(package) for package in package_set]
        packages.sort(key=lambda pkg: pkg["name"])
        repositories.append({
            "repo_key": repo_key,
            "packages": packages,
            "package_count": len(packages),
        })

    try:
        total_packages = distribution_table.get_total_package_count()
    except AttributeError:
        total_packages = sum(repo["package_count"] for repo in repositories)

    return {"repositories": repositories, "total_packages": total_packages}


def _format_package(package: Any) -> dict[str, Any]:
    filelist = getattr(package, "filelist", None) or []
    final_score = getattr(package, "final_score", 0.0) or 0.0

    return {
        "name": getattr(package, "bin_name", None) or getattr(package, "name", ""),
        "repo_name": getattr(package, "repo_name", ""),
        "repo_version": getattr(package, "repo_version", ""),
        "src_name": getattr(package, "src_name", None),
        "version": getattr(package, "version", None),
        "arch": getattr(package, "arch", None),
        "description": getattr(package, "description", None) or "No description available",
        "final_score": float(final_score),
        "metadata": _json_safe(getattr(package, "metadata", None) or {}),
        "filelist": list(filelist)[:20],
        "filelist_count": len(filelist),
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        return str(value)


if __name__ == "__main__":
    initialize_detector()
    app.run(debug=True, host="0.0.0.0", port=5000)
