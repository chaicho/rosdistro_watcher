#!/usr/bin/env python3

import argparse
import ast
import os
import sys
from typing import List, Tuple, Dict, Optional
import re
import json

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "rosdep_auditor"

from .package_info import PackageInfo
from .existence_verifer import ExistenceVerifier
from .tools import logger
from .distribution_table import DistributionTable
from .config import load_config
from .tools.llm_caller import LLMCaller, _call_llm, build_package_distribution_prompt
from .cross_repository_name_matcher import CrossRepositoryNameMatcher
from .upstream_correlation_analyzer import SourceCorrelationAnalyzer
from .candidate_package_selector import CandidatePackageSelector
from .tools.cache import save_json_cache, load_json_cache


class PackageDistributionDetector:
    """
    Package distribution detector

    Integrates four core components to provide complete package distribution detection.
    Uses consistent configuration access and verification methods as problem_detector.
    """
    def __init__(self, config):
        self.config = config
        self.existence_verifier = ExistenceVerifier(config)
        self.name_matcher = CrossRepositoryNameMatcher(config)
        self.source_analyzer = SourceCorrelationAnalyzer(config)
        self.candidate_selector = CandidatePackageSelector(config)
            
    def detect_distribution_with_llm(self,
                                     specific_package: Optional[Tuple[str, str, str]] = None,
                                     specific_packages: Optional[List[Tuple[str, str, str]]] = None,
                                     preset: str = "default") -> DistributionTable:
        """Detect package distribution directly using LLM."""
        if specific_package is not None:
            if specific_packages is None:
                specific_packages = [specific_package]

        if specific_packages and len(specific_packages) > 1:
            merged_table = DistributionTable([])
            for pkg_tuple in specific_packages:
                single_result = self.detect_distribution_with_llm(specific_package=pkg_tuple, specific_packages=None, preset=preset)
                merged_table.add_distribution_table(single_result)
            return merged_table

        if not specific_packages or len(specific_packages) == 0:
            raise ValueError("Either specific_package or specific_packages must be provided")

        specific_package = specific_packages[0]
        logger.info(f"detect_distribution_with_llm: {specific_package}")
        pkg_name, repo_name, repo_version = specific_package
        supported_arches = self.config.get('supported_arches', {}).get(repo_name, ['amd64', 'x86_64'])
        baseline_package = self.existence_verifier.verify(pkg_name, repo_name, repo_version, supported_arches[0])

        supported_versions = self.config.get('supported_versions', {})
        all_target_repos = []
        for repo_name, versions in supported_versions.items():
            for repo_version in versions:
                if repo_name == baseline_package.repo_name and repo_version == baseline_package.repo_version:
                    continue
                all_target_repos.append((repo_name, repo_version))

        if not all_target_repos:
            return DistributionTable([])

        target_repos = []
        for repo_name, repo_version in all_target_repos:
            if repo_version == '' or repo_version is None:
                repo_key = repo_name
            else:
                repo_key = f"{repo_name}_{repo_version}"
            target_repos.append(repo_key)
        target_repos = sorted(target_repos)

        prompt = build_package_distribution_prompt(baseline_package, target_repos)

        logger.info(prompt)
        response = _call_llm(prompt, preset=preset)
        logger.info(response)
        try:
            json_match = re.search(r'```json\n({.*?})\n```', response, re.DOTALL)
            json_str = json_match.group(1) if json_match else response[response.find('{'):response.rfind('}')+1]
            llm_choices = json.loads(json_str)

            selected_packages = []

            selected_packages.append(baseline_package)

            for repo_key, package_name in llm_choices.items():
                if package_name is None or package_name == 'null':
                    continue

                if '_' in repo_key:
                    repo_name, repo_version = repo_key.split('_', 1)
                else:
                    repo_name, repo_version = repo_key, ''

                package_info = PackageInfo(
                    name=package_name,
                    bin_name=package_name,
                    repo_name=repo_name,
                    repo_version=repo_version,
                    description=f"LLM selected package for {baseline_package.bin_name}",
                    confidence=1.0
                )
                selected_packages.append(package_info)

            return DistributionTable(selected_packages)

        except (json.JSONDecodeError, IndexError, AttributeError):
            logger.error(f"Warning: LLM returned a non-JSON or malformed response:\n{response}")
            return DistributionTable([])
    
    def detect_distribution(self,
                          keyword: str = "",
                          specific_packages: Optional[List[Tuple[str, str, str]]] = None,
                          specific_package: Optional[Tuple[str, str, str]] = None,
                          top_k: int = 1,
                          show_scores: bool = False,
                          score_threshold: float = 0.00,
                          use_name_matches: bool = True,
                          use_source_related: bool = True,
                          return_all_candidates: bool = False,
                          use_cache: bool = True,
                          w: float = 0.7,
                          scorer: str = "default"
                          ) -> DistributionTable:
        """Detect equivalent packages across configured distributions.

        ``keyword`` is only needed when no concrete package is supplied. When
        ``specific_package`` or ``specific_packages`` is provided, that package
        is used to establish the baseline.
        """
        logger.info(f"detect_distribution: {keyword}, {specific_packages}, {specific_package}, {top_k}, {show_scores}, {score_threshold}, {use_name_matches}, {use_source_related}, {return_all_candidates}, {use_cache}, {w}, {scorer}")
        if specific_package is not None:
            if specific_packages is None:
                specific_packages = [specific_package]

        if specific_packages and len(specific_packages) > 1:
            merged_table = DistributionTable([])
            all_candidates_table = DistributionTable([]) if return_all_candidates else None

            for pkg_tuple in specific_packages:
                if return_all_candidates:
                    single_result, candidates_table = self.detect_distribution(
                        keyword=keyword,
                        specific_packages=[pkg_tuple],
                        top_k=top_k,
                        show_scores=show_scores,
                        score_threshold=score_threshold,
                        use_name_matches=use_name_matches,
                        use_source_related=use_source_related,
                        return_all_candidates=True,
                        use_cache=use_cache,
                        w=w,
                        scorer=scorer
                    )
                    merged_table.add_distribution_table(single_result)
                    all_candidates_table.add_distribution_table(candidates_table)
                else:
                    single_result = self.detect_distribution(
                        keyword=keyword,
                        specific_packages=[pkg_tuple],
                        top_k=top_k,
                        show_scores=show_scores,
                        score_threshold=score_threshold,
                        use_name_matches=use_name_matches,
                        use_source_related=use_source_related,
                        return_all_candidates=False,
                        use_cache=use_cache,
                        w=w,
                        scorer=scorer
                    )
                    merged_table.add_distribution_table(single_result)
            
            if return_all_candidates:
                return merged_table, all_candidates_table
            else:
                return merged_table

        assert specific_packages is None or len(specific_packages) == 1, \
            f"Expected None or single package, got {len(specific_packages) if specific_packages else None} packages"

        baseline_package = self._establish_baseline(keyword, specific_packages)
        logger.debug("Finish baseline package")
        if baseline_package:
            logger.debug(f"Baseline: {baseline_package.bin_name} ({baseline_package.repo_name}) - {baseline_package.description or 'No description'}")
        else:
            logger.debug("No baseline package found")

        if not baseline_package:
            return DistributionTable([])

        candidates_cache_key = None
        candidates = None

        if use_cache:
            candidates_cache_key = self._make_candidates_cache_key(
                keyword=keyword,
                specific_packages=specific_packages,
                use_name_matches=use_name_matches,
                use_source_related=use_source_related
            )
            disk_candidates = load_json_cache(
                cache_name="candidates_cache",
                cache_key=candidates_cache_key,
                subdir="candidates",
                deserialize=lambda data: [PackageInfo.from_dict(d) for d in data]
            )
            if disk_candidates is not None:
                logger.info(f"Loaded {len(disk_candidates)} candidates from disk cache {candidates_cache_key} ")
                candidates = disk_candidates
            else:
                logger.info(f"No candidates found in disk cache for key: {candidates_cache_key}")

        if candidates is None:
            candidates = []
            if use_name_matches:
                name_matches = self.name_matcher.find_matches(baseline_package)
                candidates.extend(name_matches)

            if use_source_related:
                source_related = self.source_analyzer.analyze(
                    baseline_package.bin_name,
                    baseline_package.repo_name,
                    baseline_package.repo_version or ""
                )
                candidates.extend(source_related)

            candidates = PackageInfo.deduplicate(candidates)

            if use_cache and candidates_cache_key is not None:
                logger.info(f"Attempting to save {len(candidates)} candidates to disk cache (key: {candidates_cache_key})")
                try:
                    cache_file = save_json_cache(
                        cache_name="candidates_cache",
                        cache_key=candidates_cache_key,
                        data=candidates,
                        subdir="candidates",
                        serialize=lambda candidates: [pkg.to_dict() for pkg in candidates]
                    )
                    logger.info(f"Successfully saved {len(candidates)} candidates to disk cache: {cache_file}")
                except Exception as e:
                    logger.warning(f"Failed to save candidates to disk cache: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            else:
                if not use_cache:
                    logger.debug("Cache disabled (use_cache=False), skipping disk cache save")
                if candidates_cache_key is None:
                    logger.warning("candidates_cache_key is None, cannot save to disk cache")

        logger.debug("Finish binary name matching")
        logger.debug(f"Found {len(candidates)} candidates")
        for match in candidates:
            logger.debug(f"  - {match.bin_name} ({match.repo_name}) - {match.description[:50] if match.description else 'No description'}...")

        self.candidate_selector.set_scorer(scorer)
        selected_packages = self.candidate_selector.select(
            baseline_package,
            candidates,
            top_k,
            show_scores,
            score_threshold,
            w
        )
        logger.debug("Finish candidate package selection")
        logger.debug(f"Selected {len(selected_packages)} packages")
        for pkg in selected_packages:
            logger.debug(f"  - {pkg.bin_name} ({pkg.repo_name}) confidence={pkg.confidence:.2f} - {pkg.description[:50] if pkg.description else 'No description'}...")

        distribution_table = self._build_distribution_table(selected_packages)

        logger.debug("Finish candidate package selection") 
        for repo_name, package_set in distribution_table.items():
            if package_set:
                package_info = next(iter(package_set))
                desc_preview = package_info.description[:50] if package_info.description else 'No description'
                logger.info(f"{repo_name}: {package_info.bin_name} - {desc_preview}...")
            else:
                logger.info(f"{repo_name}: (empty package set)")

        if return_all_candidates:
            return distribution_table, self._build_distribution_table(candidates)
        else:
            return distribution_table

    def _establish_baseline(self,
                          keyword: str,
                          specific_packages: Optional[List[Tuple[str, str, str]]]) -> Optional[PackageInfo]:
        """Determine baseline package."""
        if specific_packages:
            supported_arches = self.config.get('supported_arches', {})

            for package_name, repo_name, repo_version in specific_packages:
                arch = supported_arches.get(repo_name, ['amd64'])[0]
                package_info = self.existence_verifier.verify(package_name, repo_name, repo_version, arch)
                if package_info:
                    logger.debug(f"Successfully established baseline from specific package: {package_name} ({repo_name})")
                    return package_info
                else:
                    logger.debug(f"Failed to verify specific package: {package_name} ({repo_name})")

            logger.debug("All specific packages failed, trying to infer from keyword")
            return self._infer_baseline_from_keyword(keyword)
        else:
            return self._infer_baseline_from_keyword(keyword)

    def _infer_baseline_from_keyword(self, keyword: str) -> Optional[PackageInfo]:
        """Infer baseline package from keyword."""
        supported_versions = self.config.get('supported_versions', {})
        supported_arches = self.config.get('supported_arches', {})
        
        for repo_name in supported_versions.keys():
            for repo_version in supported_versions.get(repo_name, ['']):
                for arch in supported_arches.get(repo_name, ['amd64']):
                    package_info = self.existence_verifier.verify(keyword, repo_name, repo_version, arch)
                    if package_info:
                        package_info.confidence = 0.7
                        return package_info
        return None

    def _build_distribution_table(self,
                                 packages: List[PackageInfo]) -> DistributionTable:
        """Build distribution table from package list."""
        return DistributionTable(packages)

    def _make_cache_key(self,
                       keyword: str,
                       specific_packages: Optional[List[Tuple[str, str, str]]],
                       top_k: int,
                       score_threshold: float,
                       use_name_matches: bool,
                       use_source_related: bool,
                       w: float,
                       scorer: str) -> int:
        """Generate cache key including all parameters that affect results."""
        if specific_packages:
            sorted_packages = sorted(
                (pkg_name, repo_name, repo_version or "")
                for pkg_name, repo_name, repo_version in specific_packages
            )
            packages_tuple = tuple(sorted_packages)
        else:
            packages_tuple = tuple()
        return hash((
            keyword,
            packages_tuple,
            top_k,
            score_threshold,
            use_name_matches,
            use_source_related,
            w,
            scorer
        ))
    
    def _make_candidates_cache_key(self,
                                   keyword: str,
                                   specific_packages: Optional[List[Tuple[str, str, str]]],
                                   use_name_matches: bool,
                                   use_source_related: bool) -> str:
        """Generate candidates cache key containing only parameters that affect candidate generation."""
        import hashlib
        if specific_packages:
            sorted_packages = sorted(
                (pkg_name, repo_name, repo_version or "")
                for pkg_name, repo_name, repo_version in specific_packages
            )
            packages_tuple = tuple(sorted_packages)
        else:
            packages_tuple = tuple()
        config_hash = self.config.get('_hash', '')
        key_content = str((
            config_hash,
            keyword,
            packages_tuple,
            use_name_matches,
            use_source_related
        ))
        return hashlib.md5(key_content.encode('utf-8')).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect equivalent packages across configured distributions."
    )
    parser.add_argument(
        "specific_package",
        help="Python tuple: (package_name, repository, version)",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "configs",
            "config_usefulness.yaml",
        ),
        help="Configuration YAML (defaults to config_usefulness.yaml)",
    )
    args = parser.parse_args()

    try:
        specific_package = ast.literal_eval(args.specific_package)
    except (SyntaxError, ValueError) as error:
        parser.error(f"invalid specific package tuple: {error}")
    if (
        not isinstance(specific_package, tuple)
        or len(specific_package) != 3
        or not all(isinstance(value, str) for value in specific_package)
    ):
        parser.error("specific_package must be a tuple of three strings")

    detector = PackageDistributionDetector(load_config(args.config))
    distribution = detector.detect_distribution(specific_package=specific_package)
    print(distribution)
    return 0 if len(distribution) else 1


if __name__ == "__main__":
    raise SystemExit(main())
