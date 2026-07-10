#!/usr/bin/env python3

from typing import List, Tuple, Dict, Optional
import os
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from .package_info import PackageInfo
from .tools import logger
from .tools.name_pattern import clean_package_name, roles_related_score  

class CandidatePackageSelector:
    """
    Candidate package selector

    Filters the most suitable candidate packages through semantic analysis.

    Uses class-level singleton pattern to share model instances and avoid duplicate loading.
    """
    _shared_sentence_model = None
    _model_initialized = False
    _sentence_model_name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, config, scorer: str = "default"):
        self.config = config
        if not CandidatePackageSelector._model_initialized:
            try:
                os.environ['TRANSFORMERS_OFFLINE'] = '1'
                os.environ['HF_HUB_OFFLINE'] = '1'

                CandidatePackageSelector._shared_sentence_model = SentenceTransformer(
                    CandidatePackageSelector._sentence_model_name,
                    cache_folder=os.getenv('SENTENCE_TRANSFORMERS_HOME'),
                    local_files_only=True,
                )
                CandidatePackageSelector._model_initialized = True
                logger.info(f"Sentence transformer model loaded successfully (shared): {CandidatePackageSelector._shared_sentence_model}")
            except Exception as e:
                logger.warning(f"Failed to load sentence transformer model: {e}. Falling back to text-based similarity.")
                CandidatePackageSelector._shared_sentence_model = None
                CandidatePackageSelector._model_initialized = True
        else:
            logger.debug("Reusing existing sentence transformer model instance")

        self.sentence_model = CandidatePackageSelector._shared_sentence_model

        self._score_cache = {}
        self._filelist_similarity_cache = {}

        self.scorer = scorer

    def _select_from_packages(self, target_package: PackageInfo, candidates: List[PackageInfo], top_k: int = 1, show_scores: bool = False, score_threshold: float = 0.00, w: float = 0.9) -> tuple[List[PackageInfo], dict]:
        """
        Select the most suitable packages from candidates.

        Returns:
            tuple: (selected_packages, score_map)
        """

        if not candidates:
            return [], {}

        repo_groups = self._group_by_repo(candidates)

        selected_packages = []
        score_map = {}
        
        for repo, repo_candidates in repo_groups.items():
            if len(repo_candidates) == 1:
                score = self._calculate_comprehensive_score(target_package, repo_candidates[0], show_scores, score_threshold, w)
                if score > 0:
                    score_map[repo_candidates[0]] = score
                    selected_packages.append(repo_candidates[0])
            else:
                scored_candidates = []
                if target_package.filelist and len(target_package.filelist) > 0:
                    scored_by_filelist = [(c, self._calculate_filelist_similarity(target_package, c)) for c in repo_candidates]
                    max_filelist_score = max((s for _, s in scored_by_filelist), default=0.0)
                    if max_filelist_score > 0.5:
                        filtered = [c for c, s in scored_by_filelist if s > 0.0]
                        if filtered:
                            repo_candidates = filtered
                for candidate in repo_candidates:
                    score = self._calculate_comprehensive_score(target_package, candidate, show_scores, score_threshold, w)
                    if score > 0:
                        score_map[candidate] = score
                        scored_candidates.append((candidate, score))

                scored_candidates.sort(key=lambda x: x[1], reverse=True)
                result_count = min(top_k, len(scored_candidates))
                selected_packages.extend([candidate for candidate, score in scored_candidates[:result_count] if score > 0])


        return selected_packages, score_map


    def select(self,
              target_package: PackageInfo,
              candidates: List[PackageInfo],
              top_k: int = 1,
              show_scores: bool = False,
              score_threshold: float = 0.00,
              w: float = 0.9) -> List[PackageInfo]:
        """
        Filter candidate packages.

        Args:
            target_package: Target package information
            candidates: List of candidate packages
            top_k: Number of candidate packages to return for each repository, default is 1
            show_scores: Whether to show score details, default is False
            score_threshold: Score threshold, default is 0.01
            w: Weight parameter controlling lcs, source, filelist weights; remaining items use (1-w), default is 0.9

        Returns:
            List[PackageInfo]: List of filtered package information
        """

        selected_packages, first_round_scores = self._select_from_packages(target_package, candidates, top_k, show_scores, score_threshold, w)

        return selected_packages

    def _group_by_repo(self, candidates: List[PackageInfo]) -> Dict[Tuple[str, str], List[PackageInfo]]:
        """Group by repository"""
        groups = {}
        for candidate in candidates:
            if (candidate.repo_name, candidate.repo_version) not in groups:
                groups[(candidate.repo_name, candidate.repo_version)] = []
            groups[(candidate.repo_name, candidate.repo_version)].append(candidate)
        return groups

    def set_scorer(self, scorer: str):
        """
        Set new scorer type

        Args:
            scorer: Scorer type ("default", etc.)
        """
        self.scorer = scorer
        self._score_cache.clear()
        self._filelist_similarity_cache.clear()

    def _calculate_comprehensive_score(self,
                                    target: PackageInfo,
                                    candidate: PackageInfo,
                                    show_scores: bool = False,
                                    score_threshold: float = 0.00,
                                    w: float = 0.9) -> float:
        cache_key = (
            target.bin_name,
            target.repo_name + "_" + target.repo_version,
            candidate.bin_name,
            candidate.repo_name + "_" + candidate.repo_version,
            w,  # Include weight parameter to ensure different weights don't share cache
        )

        if cache_key in self._score_cache:
            return self._score_cache[cache_key]

        total_score = self._multi_dimension_scoring(target, candidate, show_scores, score_threshold, w)

        self._score_cache[cache_key] = total_score

        return total_score

    def _multi_dimension_scoring(self, target: PackageInfo, candidate: PackageInfo, show_scores: bool = False, score_threshold: float = 0.00, w: float = 0.9) -> float:
        ratios = {
            'lcs_similarity': w / 3,
            'source_similarity': w / 3,
            'file_coverage': w / 3,
            'type_similarity': (1 - w) / 3,
            'version_similarity': (1 - w) / 3,
            'description_similarity': (1 - w) / 3,
        }

        target_parsed = self._parse_package_name(target.bin_name)
        candidate_parsed = self._parse_package_name(candidate.bin_name)

        lcs_score = self._calculate_lcs_similarity_optimized(target_parsed['core_name'], candidate_parsed['core_name'])
        lcs_weighted = lcs_score * ratios['lcs_similarity']

        source_score = self._calculate_source_similarity(target, candidate)
        source_weighted = source_score * ratios['source_similarity']

        type_score = self._calculate_type_similarity(target.bin_name, candidate.bin_name)
        type_weighted = type_score * ratios['type_similarity']

        version_score = self._calculate_version_similarity(target_parsed['version'], candidate_parsed['version'])
        version_weighted = version_score * ratios['version_similarity']

        desc_score = self._calculate_semantic_similarity(target.description, candidate.description)
        desc_weighted = desc_score * ratios['description_similarity']

        file_coverage_score = self._calculate_filelist_similarity(target, candidate)
        file_weighted = file_coverage_score * ratios['file_coverage']

        total_score = lcs_weighted + source_weighted + type_weighted + version_weighted + desc_weighted + file_weighted

        if show_scores:
            repo_key = f"{candidate.repo_name}_{candidate.repo_version}" if candidate.repo_version else candidate.repo_name
            scores = [
                f"LCS:{lcs_weighted:.3f}({lcs_score:.3f})",
                f"Src:{source_weighted:.3f}({source_score:.3f})",
                f"Type:{type_weighted:.3f}({type_score:.3f})",
                f"Ver:{version_weighted:.3f}({version_score:.3f})",
                f"Desc:{desc_weighted:.3f}({desc_score:.3f})",
                f"File:{file_weighted:.3f}({file_coverage_score:.3f})"
            ]
            logger.info(f"{candidate.bin_name} ({repo_key}) Total:{total_score:.3f} [{', '.join(scores)}]")

        return total_score


    def _parse_package_name(self, package_name: str) -> dict:
        """Parse package name, extract core name and version information"""
        if not package_name:
            return {'core_name': '', 'version': {}}

        # Step 1: Remove roles (suffixes like -dev, -devel and role prefixes)
        cleaned_name = clean_package_name(package_name)

        # Step 2: Extract version info from cleaned name
        version = self._extract_version_dict(cleaned_name)

        # Step 3: Remove only versions recognized by the version parser.
        core_name = cleaned_name
        for component, version_number in version.items():
            pattern = rf'{re.escape(component)}{re.escape(str(version_number))}'
            core_name = re.sub(pattern, component, core_name)
            core_name = re.sub(rf'{re.escape(str(version_number))}', '', core_name)

        core_name = re.sub(r'-+', '-', core_name).strip('-')
        if not core_name:
            core_name = package_name.lower()

        return {
            'core_name': core_name,
            'version': version
        }
    
    def _calculate_lcs_similarity_optimized(self, str1: str, str2: str) -> float:
        """Calculates LCS similarity with optimized space complexity."""
        if not str1 or not str2:
            return 0.0
        
        if str1 == str2:
            return 1.0
        
        # Ensure str2 is the shorter string to minimize space usage
        if len(str1) < len(str2):
            str1, str2 = str2, str1

        m, n = len(str1), len(str2)

        previous_dp = [0] * (n + 1)
        current_dp = [0] * (n + 1)
        
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if str1[i-1] == str2[j-1]:
                    current_dp[j] = previous_dp[j-1] + 1
                else:
                    current_dp[j] = max(previous_dp[j], current_dp[j-1])
            previous_dp = current_dp[:]

        lcs_length = previous_dp[n]
        total_length = m + n
        
        if total_length == 0:
            return 0.0
            
        return (2.0 * lcs_length) / total_length

    def _extract_version_dict(self, package_name: str) -> dict:
        """Extract version information from package name, return in dictionary format."""
        if not package_name:
            return {}

        version_dict = {}
        working_name = package_name.lower()

        letter_number_pattern = r'([a-z]+)(\d+(?:\.\d+)*)'
        matches = re.findall(letter_number_pattern, working_name)
        for component, version in matches:
            if component not in version_dict:
               version_dict[component] = version

        return version_dict

    def _calculate_version_similarity(self, target_version: dict, candidate_version: dict) -> float:
        """Calculate version similarity (dictionary format).

        Returns 1 only when both packages have no version information or a
        shared component has the same version.
        """
        if not target_version and not candidate_version:
            return 1.0

        if not target_version or not candidate_version:
            return 0.0

        common_keys = set(target_version.keys()) & set(candidate_version.keys())

        for key in common_keys:
            if target_version[key] == candidate_version[key]:
                return 1.0

        return 0.0

    def _calculate_type_similarity(self, package_name: str, candidate_name: str) -> float:
        """Calculate type similarity."""
        return roles_related_score(package_name, candidate_name)

    def _calculate_semantic_similarity(self, desc1: str, desc2: str) -> float:
        """Calculate description similarity (using sentence embedding semantic similarity)."""
        if not desc1 or not desc2:
            return 0.0

        if desc1.strip() == desc2.strip():
            return 1.0

        if self.sentence_model is None:
            logger.error("Sentence model not available, using LCS similarity for descriptions")
            return self._calculate_lcs_similarity_optimized(desc1, desc2)

        try:
            embeddings = self.sentence_model.encode([desc1, desc2])

            embedding1 = embeddings[0]
            embedding2 = embeddings[1]

            dot_product = np.dot(embedding1, embedding2)
            norm1 = np.linalg.norm(embedding1)
            norm2 = np.linalg.norm(embedding2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            cosine_similarity = dot_product / (norm1 * norm2)

            return max(0.0, min(1.0, float(cosine_similarity)))

        except Exception as e:
            logger.error(f"Error computing semantic similarity: {e}. Using LCS similarity for descriptions.")
            return self._calculate_lcs_similarity_optimized(desc1, desc2)

    def _calculate_filelist_similarity(self, target: PackageInfo, candidate: PackageInfo) -> float:
        """Calculate file list similarity (how many files in target file list are in candidate package file list)."""
        cache_key = (id(getattr(target, 'filelist', None)), id(getattr(candidate, 'filelist', None)))
        cached = self._filelist_similarity_cache.get(cache_key)
        if cached is not None:
            return cached

        target_files = set()
        candidate_files = set()

        if hasattr(target, 'filelist') and target.filelist:
            target_files.update(target.filelist)
        if hasattr(candidate, 'filelist') and candidate.filelist:
            candidate_files.update(candidate.filelist)

        if not target_files or not candidate_files:
            self._filelist_similarity_cache[cache_key] = 0.0
            return 0.0

        intersection = target_files & candidate_files

        score = len(intersection) / len(target_files) if target_files else 0.0
        self._filelist_similarity_cache[cache_key] = score
        return score

    def _calculate_source_similarity(self, target: PackageInfo, candidate: PackageInfo) -> float:
        """
        Calculate source package similarity

        If candidate packages were collected through source_mapper.py (Repology API), it means they come from the same upstream project,
        should return 1.0. This is determined by checking if the 'source' field in metadata contains 'upstream'.

        Note: Even same-source packages may have different src_name on different distributions, so cannot simply compare src_name.

        Args:
            target: Target package information
            candidate: Candidate package information

        Returns:
            float: Source similarity score (0.0 or 1.0)
        """
        # Check if candidate package was collected through source_mapper (Repology API)
        candidate_metadata = getattr(candidate, 'metadata', None)
        if candidate_metadata:
            source = candidate_metadata.get('source')
            if isinstance(source, list) and 'upstream' in source:
                return 1.0

        if hasattr(target, 'src_name') and target.src_name:
            src_name = target.src_name
            if clean_package_name(src_name, remove_version=True) == clean_package_name(candidate.src_name, remove_version=True):
                return 1.0
        return 0.0
