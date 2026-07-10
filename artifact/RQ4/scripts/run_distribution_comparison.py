#!/usr/bin/env python3

import os
import sys
import yaml
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import traceback

# TOOL_PATH points to the main repo root for importing rosdep_auditor modules
TOOL_PATH = Path(os.getenv("TOOL_PATH", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(TOOL_PATH))

# Add scripts directory to sys.path for baseline imports
SCRIPTS_PATH = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_PATH))

from rosdep_auditor.config import load_config
from rosdep_auditor.package_distribution_detector import PackageDistributionDetector
from rosdep_auditor.upstream_mapper import RepologyPackageMapper
from rosdep_auditor.distribution_table import DistributionTable
from rosdep_auditor.tools import logger
from rosdep_auditor.distro_parser import RosdepDatabase
from rosdep_auditor.tools.llm_caller import get_available_presets
from rosdep_auditor import _apply_name_replacements
from rosdep_auditor.existence_verifer import ExistenceVerifier


def _distribution_table_to_dict(table: DistributionTable) -> Dict:
    """Convert DistributionTable to dictionary."""
    if table is None:
        return {}
    result = {}
    for repo_key, package_set in table.items():
        result[repo_key] = [{
            'name': pkg.name,
            'bin_name': pkg.bin_name,
            'src_name': pkg.src_name,
            'repo_name': pkg.repo_name,
            'repo_version': pkg.repo_version,
            'arch': pkg.arch,
            'version': pkg.version,
            'description': pkg.description,
            'confidence': getattr(pkg, 'confidence', 1.0)
        } for pkg in sorted(package_set, key=lambda p: p.name)]
    return result


class DistributionComparisonAnalyzer:
    """Analyzer to compare distribution detection methods with modular design."""

    def __init__(self, ground_truth_file: str, config_file: str = None):
        """Initialize the analyzer with configuration and databases."""
        if config_file is None:
            config_file = str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config_distribution_comparison.yaml')
        try:
            self.config = load_config(config_file)
        except Exception as e:
            logger.info(f"Config loading failed, using fallback: {e}")
            self.config = load_config(str(TOOL_PATH / 'rosdep_auditor' / 'configs' / 'config.yaml'), use_cache=False)
        
        self.rosdep_database = self._build_rosdep_database_from_ground_truth(ground_truth_file)
        self.package_detector = PackageDistributionDetector(self.config)
        self.existence_verifier = ExistenceVerifier(self.config)
        self.repology_mapper = RepologyPackageMapper()
        
        try:
            from baseline.rosdep_ci_mapper import RosdepCIMapper
            self.rosdep_ci_mapper = RosdepCIMapper()
        except Exception as e:
            logger.warning(f"Warning: RosdepCIMapper not available: {e}")
            self.rosdep_ci_mapper = None
        
        self.available_tools = self._init_available_tools()
        self.comparison_results = []
    
    def _init_available_tools(self) -> Dict:
        """Initialize available tools configuration."""
        return {
            'detector': {
                'name': 'PackageDistributionDetector', 'emoji': '🔍',
                'func': self._run_detector, 'description': 'AI-based detection',
                'returns_distribution_table': True
            },
            'mapper': {
                'name': 'RepologyMapper', 'emoji': '🗺️',
                'func': self._run_mapper, 'description': 'Direct Repology API mapping',
                'returns_distribution_table': True
            },
            'llm': {
                'name': 'LLMDetector', 'emoji': '🤖',
                'func': self._run_llm, 'description': 'Direct LLM-based detection',
                'returns_distribution_table': True, 'preset': 'default'
            },
            'rosdep_ci': {
                'name': 'RosdepCIMapper', 'emoji': '🤖',
                'func': self._run_rosdep_ci, 'description': 'RosdepCI mapping',
                'optional': True, 'returns_distribution_table': True
            },
        }

    def _extract_binnames(self, packages, platform_key: str = None, apply_replacements: bool = False):
        """Extract and sort binnames from packages."""
        binnames = []
        for pkg in packages:
            bin_name = pkg.get('bin_name') or pkg.get('name', '')
            if bin_name:
                if apply_replacements and platform_key:
                    parts = platform_key.split('_', 1)
                    if len(parts) == 2:
                        os_name, os_version = parts[0], parts[1]
                        bin_name = _apply_name_replacements(self.config, bin_name, os_name, os_version)
                binnames.append(bin_name)
        return sorted(binnames)
    
    def _format_binnames_short(self, binnames):
        """Format binnames for table display."""
        if not binnames:
            return "None"
        if len(binnames) <= 3:
            return f"`{', '.join(binnames)}`"
        return f"`{', '.join(binnames[:2])}, ...` ({len(binnames)})"

    def _get_match_status(self, match_result, found_count, total_count):
        """Get formatted match status string."""
        status_map = {
            'exact_match': f"✅ Exact match ({found_count}/{total_count})",
            'partial_match': f"⚠️ Partial match ({found_count}/{total_count})",
        }
        return status_map.get(match_result, f"❌ No match ({found_count}/{total_count})")

    def _print_tool_statistics(self, tool_name, tool_emoji, stats):
        """Print statistics for a single tool."""
        logger.info(f"\n{tool_emoji} {tool_name}:")
        logger.info(f"  ✅ Exact match: {stats['exact_matches']} ({stats['exact_match_rate']:.1f}%)")
        logger.info(f"  ⚠️ Partial match: {stats['partial_matches']} ({stats['partial_match_rate']:.1f}%)")
        logger.info(f"  ❌ No match: {stats['no_matches']} ({stats['no_match_rate']:.1f}%)")
        
    def _run_tool_detection(self, tool_name, tool_func, *args, **kwargs):
        """Run tool detection with error handling."""
        try:
            kwargs = {k: v for k, v in kwargs.items() if k != 'tool_info'}
            logger.info(f"[_run_tool_detection] Calling {tool_name} with args={args}, kwargs={kwargs}")
            result = tool_func(*args, **kwargs)
            logger.info(f"[_run_tool_detection] {tool_name} returned: type={type(result)}, len={len(result) if hasattr(result, '__len__') else 'N/A'}")
            if isinstance(result, tuple):
                return [_distribution_table_to_dict(dist) for dist in result]
            converted = _distribution_table_to_dict(result)
            logger.info(f"[_run_tool_detection] {tool_name} converted dict keys: {list(converted.keys()) if isinstance(converted, dict) else 'not a dict'}")
            return converted
        except Exception as e:
            logger.error(f"Error with {tool_name}: {e}")
            traceback.print_exc()
            return {'error': str(e)}
        
    def _build_rosdep_database_from_ground_truth(self, ground_truth_file: str) -> RosdepDatabase:
        """Build RosdepDatabase from ground truth YAML file."""
        with open(ground_truth_file, 'r', encoding='utf-8') as f:
            ground_truth_content = f.read()
        database = RosdepDatabase()
        database.load_yaml(ground_truth_content)
        logger.info(f"Loaded {len(database.get_all_entries())} entries into RosdepDatabase")
        return database

    def _get_verified_ground_truth_table(self, rosdep_key: str) -> Dict:
        """Get ground truth distribution table with existence-verified packages.

        Returns:
            Dict: Distribution table with only verified packages
        """
        from rosdep_auditor.package_info import PackageInfo

        rosdep_entry = self.rosdep_database.get_entry(rosdep_key)
        if not rosdep_entry:
            return {'error': 'Entry not found'}

        # Get all package configs from ground truth
        package_configs = rosdep_entry.get_all_packages_from_config()
        distribution_table = rosdep_entry.to_distribution_table()
        distribution_table_packages = distribution_table.get_all_packages()
        verified_packages = []
        removed_count = 0
        skipped_count = 0

        for package_info in distribution_table_packages:
            platform = package_info.repo_name
            version = package_info.repo_version
            package_name = package_info.bin_name

            if not package_name:
                continue

            package_name = _apply_name_replacements(self.config, package_name, platform, version)
            arch = self.config.get('supported_arches', {}).get(platform, ['amd64'])[0]
            logger.info(f"Verifying package '{package_name}' in {platform} {version} {arch}")
            # Verify package exists
            result = self.existence_verifier.verify(package_name, platform, version, arch)
            if result is not None:
                verified_packages.append(result)
            else:
                logger.warning(f"Ground truth package '{package_name}' not found in {platform} {version}, removing")
                removed_count += 1

        if skipped_count > 0:
            logger.info(f"[{rosdep_key}] Skipped verification for {skipped_count} packages (rolling release)")
        if removed_count > 0:
            logger.info(f"[{rosdep_key}] Removed {removed_count} non-existent packages from ground truth")

        # Build distribution table from verified packages
        verified_table = DistributionTable(verified_packages)
        return _distribution_table_to_dict(verified_table)

    def get_baseline_packages(self, rosdep_key: str) -> List[Tuple[str, str, str]]:
        """Get baseline packages from rosdep entry. Priority: ubuntu_noble -> pypi -> Error.
        Returns all packages from the first matching repository."""
        rosdep_entry = self.rosdep_database.get_entry(rosdep_key)
        if not rosdep_entry:
            raise ValueError(f"Rosdep entry '{rosdep_key}' not found in database")
        
        distribution_table = rosdep_entry.to_distribution_table()
        if not distribution_table:
            raise ValueError(f"No packages found in distribution table for '{rosdep_key}'")
        
        # Priority 1: ubuntu_noble packages
        if 'ubuntu_noble' in distribution_table:
            package_set = distribution_table['ubuntu_noble']
            if package_set:
                packages = []
                for pkg in package_set:
                    packages.append((pkg.bin_name or pkg.name, pkg.repo_name or 'ubuntu', pkg.repo_version or 'noble'))
                return packages
                
        
        # Priority 2: PyPI packages
        if 'pypi' in distribution_table:
            package_set = distribution_table['pypi']
            if package_set:
                packages = []
                for pkg in package_set:
                    packages.append((pkg.bin_name or pkg.name, 'pypi', ''))
                return packages
        
        raise ValueError(f"No suitable baseline package for '{rosdep_key}'. Available: {list(distribution_table.keys())}")
      
    def compare_single_entry_modular(self, rosdep_key: str, available_tools: Dict,
                                      tool_dirs: Dict, force_rerun: bool = False) -> Dict:
        """Compare distribution detection for a single rosdep entry with modular tools."""
        try:
            primary_packages = self.get_baseline_packages(rosdep_key)
        except ValueError as e:
            logger.warning(f"No suitable baseline package for {rosdep_key}: {e}")
            return {
                'rosdep_key': rosdep_key, 'status': 'skipped',
                'reason': 'no_baseline_package', 'baseline_package': None,
                'timestamp': datetime.now().isoformat()
            }
        
        results = {
            'rosdep_key': rosdep_key, 'status': 'completed',
            'baseline_package': primary_packages, 'primary_package': primary_packages,
            'timestamp': datetime.now().isoformat()
        }
        
        # Run each tool
        for tool_name, tool_info in available_tools.items():
            tool_result_file = os.path.join(tool_dirs.get(tool_name, ''), f"{rosdep_key}.json") if tool_dirs else None

            # Load cached result if available
            if tool_result_file and not force_rerun and os.path.exists(tool_result_file):
                try:
                    with open(tool_result_file, 'r', encoding='utf-8') as f:
                        cached_result = json.load(f)
                    results[f'{tool_name}_result'] = cached_result
                    logger.info(f"[CACHE HIT] {tool_name} loaded from {tool_result_file}, keys: {list(cached_result.keys()) if isinstance(cached_result, dict) else 'not dict'}")
                    continue
                except Exception as e:
                    logger.warning(f"[CACHE FAIL] {tool_name}: {e}")
            
            # Run tool
            try:
                tool_result = tool_info['func'](primary_packages, tool_info=tool_info)
                results[f'{tool_name}_result'] = tool_result

                # Save result (even if empty)
                if tool_result_file and tool_dirs:
                    with open(tool_result_file, 'w', encoding='utf-8') as f:
                        json.dump(self._make_yaml_safe(tool_result), f, indent=2, default=str, ensure_ascii=False)
                    logger.debug(f"[CACHE SAVE] {tool_name} saved to {tool_result_file}")
            except Exception as e:
                logger.error(f"Error running {tool_name}: {e}")
                results[f'{tool_name}_result'] = {'error': str(e)}
                traceback.print_exc()
        # Ground truth (with existence verification)
        try:
            results['rosdep_result'] = self._get_verified_ground_truth_table(rosdep_key)
        except Exception as e:
            results['rosdep_result'] = {'error': str(e)}
            logger.error(f"Error getting verified ground truth table for {rosdep_key}: {e}")
        
        results['comparison_summary'] = self._generate_comparison_summary(results, available_tools)
        results['detailed_package_comparison'] = self._generate_detailed_package_comparison(results, available_tools)
        return results

    def _generate_comparison_summary(self, results: Dict, available_tools: Dict = None) -> Dict:
        """Generate comparison summary between the methods."""
        available_tools = available_tools or self.available_tools
        summary = {'repositories_found': {}, 'package_counts': {}, 'common_repositories': [], 'accuracy_metrics': {}}

        for tool_name in available_tools:
            summary[f'unique_to_{tool_name}'] = []

        tool_repos = {}
        rosdep_repos = set()

        # Ground truth repositories
        rosdep_result = results.get('rosdep_result', {})
        if rosdep_result and isinstance(rosdep_result, dict) and 'error' not in rosdep_result:
            rosdep_repos = set(rosdep_result.keys())
            summary['repositories_found']['rosdep'] = sorted(rosdep_repos)
            summary['package_counts']['rosdep'] = sum(len(pkgs) for pkgs in rosdep_result.values())

        # Tool repositories
        for tool_name in available_tools:
            result = results.get(f'{tool_name}_result', {})
            if result and isinstance(result, dict) and 'error' not in result:
                tool_repos[tool_name] = set(result.keys())
                summary['repositories_found'][tool_name] = sorted(tool_repos[tool_name])
                summary['package_counts'][tool_name] = sum(len(pkgs) for pkgs in result.values())
            else:
                tool_repos[tool_name] = set()

        # Common and unique repositories
        if rosdep_repos or any(tool_repos.values()):
            common = rosdep_repos.copy()
            for repos in tool_repos.values():
                common &= repos
            summary['common_repositories'] = sorted(common)

            for tool_name in available_tools:
                other = rosdep_repos.union(*(r for n, r in tool_repos.items() if n != tool_name))
                summary[f'unique_to_{tool_name}'] = sorted(tool_repos[tool_name] - other)
            
        # Accuracy metrics
        if rosdep_repos:
            for tool_name, repos in tool_repos.items():
                if repos:
                    intersection = len(repos & rosdep_repos)
                    precision = intersection / len(repos)
                    recall = intersection / len(rosdep_repos)
                    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
                    summary['accuracy_metrics'][tool_name] = {'precision': precision, 'recall': recall, 'f1_score': f1}

        return summary

    def _generate_detailed_package_comparison(self, results: Dict, available_tools: Dict = None) -> Dict:
        """Generate binname comparison analysis based on Ground Truth as baseline."""
        available_tools = available_tools or self.available_tools
            
        comparison = {
            'rosdep_key': results['rosdep_key'],
            'analysis_timestamp': datetime.now().isoformat(),
            'platform_binname_analysis': {},
            'summary': {'total_ground_truth_platforms': 0}
        }
        
        # Initialize summary counters
        for tool_name in available_tools:
            comparison['summary'].update({
                f'{tool_name}_exact_matches': 0,
                f'{tool_name}_partial_matches': 0,
                f'{tool_name}_no_matches': 0
            })
        
        rosdep_result = results.get('rosdep_result', {})
        if isinstance(rosdep_result, dict) and 'error' in rosdep_result:
            comparison['status'] = 'error'
            return comparison
        
        tool_results = {name: results.get(f'{name}_result', {}) for name in available_tools}
        ground_truth_platforms = list(rosdep_result.keys()) if rosdep_result else []
        comparison['summary']['total_ground_truth_platforms'] = len(ground_truth_platforms)
        
        def update_match_stats(gt_set, result_set, tool_name):
            """Update match statistics and return match result."""
            found = len(gt_set & result_set)
            if gt_set == result_set:
                comparison['summary'][f'{tool_name}_exact_matches'] += 1
                return 'exact_match', found
            elif found > 0:
                comparison['summary'][f'{tool_name}_partial_matches'] += 1
                return 'partial_match', found
            comparison['summary'][f'{tool_name}_no_matches'] += 1
            return 'no_match', found
        
        for platform_key in ground_truth_platforms:
            analysis = {'platform': platform_key}
            analysis['ground_truth_binnames'] = self._extract_binnames(rosdep_result[platform_key], platform_key, apply_replacements=True)
            gt_set = set(analysis['ground_truth_binnames'])

            for tool_name, tool_info in available_tools.items():
                tool_result = tool_results[tool_name]
                has_result = (tool_result and isinstance(tool_result, dict) and
                             platform_key in tool_result and 'error' not in tool_result)

                if has_result:
                    binnames = self._extract_binnames(tool_result[platform_key], platform_key, apply_replacements=True)
                    analysis[f'{tool_name}_binnames'] = binnames
                    if tool_info.get('returns_distribution_table'):
                        analysis[f'{tool_name}_total_packages'] = len(tool_result[platform_key])
                    match_result, found = update_match_stats(gt_set, set(binnames), tool_name)
                    analysis[f'{tool_name}_match_result'] = match_result
                    analysis[f'{tool_name}_found_count'] = found
                else:
                    analysis[f'{tool_name}_binnames'] = []
                    analysis[f'{tool_name}_match_result'] = 'no_match'
                    analysis[f'{tool_name}_found_count'] = 0
                    if tool_info.get('returns_distribution_table'):
                        analysis[f'{tool_name}_total_packages'] = 0
                    comparison['summary'][f'{tool_name}_no_matches'] += 1
            
            comparison['platform_binname_analysis'][platform_key] = analysis

        comparison['status'] = 'completed'
        return comparison

    def run_full_comparison(self, result_dir: str, markdown_file: str,
                           tools: Optional[List[str]] = None, max_entries: Optional[int] = None,
                           force_rerun: bool = False, limited_entries: Optional[List[str]] = None,
                           w_values: Optional[List[float]] = None,
                           llm_presets: Optional[List[str]] = None,
                           save_combined: bool = True) -> List[Dict]:
        """Run comparison for all entries with modular tool support.

        Args:
            w_values: If provided, automatically create tool variants with different w values for
                     tools that support it (detector). Original tools are replaced with w variants.
                     Results are stored separately for each w value to avoid conflicts.
            llm_presets: If provided, automatically create tool variants with different LLM presets
                        for tools that support it (llm). Hyphens in preset names are
                        converted to underscores in tool names (e.g., mimo-flash -> llm_mimo_flash).
                        Original llm tools are replaced with preset variants.
        """
        tools = tools or list(self.available_tools.keys())

        # Tools that support w parameter
        param_supporting_tools = ['detector']

        # Expand tools with w_values if provided
        # When w_values is provided, replace w-supporting tools with their w variants
        if w_values:
            expanded_tools = []
            for tool in tools:
                # If tool supports w, replace it with w variants (don't include original)
                if tool in param_supporting_tools:
                    for w in w_values:
                        w_tool = f"{tool}_w{w:.1f}".replace('.', '')
                        if w_tool not in self.available_tools:
                            # Create dynamic tool variant with w parameter
                            base_tool = self.available_tools[tool].copy()
                            base_tool['name'] = f"{base_tool['name']} (w={w})"
                            base_tool['description'] = f"{base_tool['description']} (w={w})"
                            base_tool['w'] = w
                            self.available_tools[w_tool] = base_tool
                        expanded_tools.append(w_tool)
                else:
                    # Keep original tool if it doesn't support w
                    expanded_tools.append(tool)
            tools = expanded_tools
            logger.info(f"Expanded tools with w_values: {w_values} -> {len([t for t in tools if '_w' in t])} w variants created")

        # Expand tools with llm_presets if provided
        # When llm_presets is provided, replace llm tools with their preset variants
        if llm_presets:
            # Validate presets against available presets
            available_presets = get_available_presets()
            invalid_presets = [p for p in llm_presets if p not in available_presets]
            if invalid_presets:
                raise ValueError(
                    f"Invalid LLM presets: {invalid_presets}. "
                    f"Available presets: {available_presets}"
                )

            expanded_tools = []
            for tool in tools:
                # Check if tool is an LLM tool (llm or llm_with_* variants)
                if tool == 'llm' or (tool.startswith('llm_with_') and tool in self.available_tools):
                    # Replace with preset variants
                    for preset in llm_presets:
                        # Convert hyphens to underscores for tool naming
                        preset_tool_suffix = preset.replace('-', '_')
                        preset_tool = f"llm_{preset_tool_suffix}"
                        if preset_tool not in self.available_tools:
                            # Create dynamic tool variant with preset parameter
                            # Find the base llm tool definition
                            base_tool_name = 'llm' if tool == 'llm' else tool
                            if base_tool_name not in self.available_tools:
                                # Skip if base tool doesn't exist
                                continue
                            base_tool = self.available_tools[base_tool_name].copy()
                            base_tool['name'] = f"{base_tool['name']} ({preset})"
                            base_tool['description'] = f"{base_tool['description']} ({preset})"
                            base_tool['preset'] = preset
                            self.available_tools[preset_tool] = base_tool
                        expanded_tools.append(preset_tool)
                else:
                    # Keep original tool if it's not an LLM tool
                    expanded_tools.append(tool)
            tools = expanded_tools
            logger.info(f"Expanded tools with llm_presets: {llm_presets} -> {len([t for t in tools if t.startswith('llm_')])} preset variants created")

        # Filter available tools
        available_tools = {
            t: self.available_tools[t] for t in tools 
            if t in self.available_tools and (
                not self.available_tools[t].get('optional') or self._is_tool_available(t)
            )
        }

        # Create directories
        tool_dirs = {t: os.path.join(result_dir, t) for t in available_tools}
        for d in tool_dirs.values():
            os.makedirs(d, exist_ok=True)
        combined_dir = os.path.join(result_dir, 'combined')
        if save_combined:
            os.makedirs(combined_dir, exist_ok=True)

        self._init_markdown_file(markdown_file, available_tools)

        # Get entries to process
        all_entries = limited_entries or sorted(self.rosdep_database.get_all_entries(include_distribution_entries=False))
        if max_entries:
            all_entries = all_entries[:max_entries]
        
        logger.info(f"Processing {len(all_entries)} entries with tools: {list(available_tools.keys())}")

        for i, rosdep_key in enumerate(all_entries, 1):
            logger.info(f"[{i}/{len(all_entries)}] {rosdep_key}")
            try:
                result = self.compare_single_entry_modular(rosdep_key, available_tools, tool_dirs, force_rerun)
                self.comparison_results.append(result)
                if save_combined:
                    self._save_entry_result(result, combined_dir)
                self._append_detailed_analysis_to_markdown(result, markdown_file, available_tools)

                if result['status'] == 'completed':
                    self._print_entry_summary(result, available_tools)
                elif result['status'] == 'skipped':
                    logger.info(f"⚠ {rosdep_key} skipped")
            except Exception as e:
                logger.error(f"Error processing {rosdep_key}: {e}")
                traceback.print_exc()
                error_result = {'rosdep_key': rosdep_key, 'status': 'error', 'error': str(e), 'timestamp': datetime.now().isoformat()}
                self.comparison_results.append(error_result)
                if save_combined:
                    self._save_entry_result(error_result, combined_dir)

        self._generate_and_print_overall_summary(available_tools, markdown_file)
        return self.comparison_results

    def _print_entry_summary(self, result: Dict, available_tools: Dict):
        """Print summary for a single entry."""
        detailed = result.get('detailed_package_comparison', {})
        if detailed.get('status') != 'completed':
            return
        summary = detailed['summary']
        total = summary['total_ground_truth_platforms']
        for tool_name, info in available_tools.items():
            exact = summary.get(f'{tool_name}_exact_matches', 0)
            logger.info(f"  {info['emoji']} {info['name']}: {exact}/{total} exact")
        
    def _generate_and_print_overall_summary(self, available_tools: Dict, markdown_file: str = None):
        """Generate and print overall summary."""
        stats = self._generate_overall_summary_statistics(available_tools)
        completed = [r for r in self.comparison_results if r['status'] == 'completed']
        
        logger.info(f"{'='*60}Summary{'='*60}")
        logger.info(f"Processed: {len(self.comparison_results)} | Completed: {len(completed)} | Platforms: {stats['total_platforms']}")

        # Tool statistics
        tools_info = [(info['name'], info['emoji'], stats[name]) 
                      for name, info in available_tools.items() if name in stats]
        for name, emoji, s in tools_info:
            self._print_tool_statistics(name, emoji, s)

        # Ranking
        logger.info(f"\n🏆 Performance Ranking:")
        ranking = sorted([(n, s['exact_match_rate']) for n, _, s in tools_info], key=lambda x: -x[1])
        for i, (name, rate) in enumerate(ranking, 1):
            logger.info(f"  {i}. {name}: {rate:.1f}%")

        if markdown_file:
            self._append_overall_summary_to_markdown(stats, markdown_file, available_tools)
        
    def _is_tool_available(self, tool: str) -> bool:
        """Check if an optional tool is available."""
        if tool == 'rosdep_ci':
            return self.rosdep_ci_mapper is not None
        return True

    def _run_detector(self, baseline_packages: List[Tuple[str, str, str]], **kwargs) -> Dict:
        """Run PackageDistributionDetector."""
        tool_info = kwargs.get('tool_info', {})
        w = tool_info.get('w', kwargs.get('w', 0.9))
        scorer = tool_info.get('scorer', kwargs.get('scorer', 'default'))
        show_scores = tool_info.get('show_scores', kwargs.get('show_scores', False))

        return self._run_tool_detection(
            "Detector", self.package_detector.detect_distribution,
            keyword=baseline_packages[0][0], specific_packages=baseline_packages, w=w, scorer=scorer, show_scores=show_scores, **kwargs
        )
        
    def _run_mapper(self, baseline_packages: List[Tuple[str, str, str]], **kwargs) -> Dict:
        """Run RepologyPackageMapper."""
        merged_table = DistributionTable([])
        for pkg_name, repo_name, repo_version in baseline_packages:
            packages = self.repology_mapper.map_package_distributions(
                platform=repo_name, os_version=repo_version, package_name=pkg_name,
                config_only=True, verify_existence=False
            )
            single_table = DistributionTable(packages)
            merged_table.add_distribution_table(single_table)
        return _distribution_table_to_dict(merged_table)
        
    def _run_llm(self, baseline_packages: List[Tuple[str, str, str]], **kwargs) -> Dict:
        """Run LLM-based detection."""
        tool_info = kwargs.get('tool_info', {})
        preset = tool_info.get('preset', 'default')
        logger.info(f"[_run_llm] baseline_packages={baseline_packages}, preset={preset}")
        return self._run_tool_detection(
            "LLM", self.package_detector.detect_distribution_with_llm, 
            specific_packages=baseline_packages, preset=preset
        )
        
    def _run_rosdep_ci(self, baseline_packages: List[Tuple[str, str, str]], **kwargs) -> Dict:
        """Run RosdepCIMapper."""
        if not self.rosdep_ci_mapper:
            return {'error': 'RosdepCIMapper not available'}
        merged_table = DistributionTable([])
        for pkg_name, repo_name, repo_version in baseline_packages:
            try:
                single_table = self.rosdep_ci_mapper.get_rosdep_ci_distribution_result(
                    platform=repo_name, os_version=repo_version, package_name=pkg_name
                )
                merged_table.add_distribution_table(single_table)
            except Exception as e:
                logger.error(f"Error with RosdepCI for {pkg_name}: {e}")
                # Continue with other packages even if one fails
        return _distribution_table_to_dict(merged_table)

    def generate_comparison_report(self, output_dir: str, available_tools: Dict = None) -> str:
        """Generate detailed comparison report."""
        os.makedirs(output_dir, exist_ok=True)
        available_tools = available_tools or self.available_tools
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        report_file = os.path.join(output_dir, f"comparison_report_{timestamp}.yaml")
        
        completed = [r for r in self.comparison_results if r['status'] == 'completed']
        
        # Collect accuracy metrics
        tool_accuracy = {t: {'precision': [], 'recall': [], 'f1_score': []} for t in available_tools}
        for result in completed:
            for tool, metrics in result.get('comparison_summary', {}).get('accuracy_metrics', {}).items():
                if tool in tool_accuracy:
                    for k, v in metrics.items():
                        tool_accuracy[tool][k].append(v)
        
        avg = lambda vals: sum(vals) / len(vals) if vals else 0
        platform_stats = self._generate_overall_summary_statistics(available_tools)
        
        report = {
            'metadata': {
                'timestamp': timestamp,
                'total': len(self.comparison_results),
                'completed': len(completed),
                'errors': len([r for r in self.comparison_results if r['status'] == 'error']),
                'skipped': len([r for r in self.comparison_results if r['status'] == 'skipped'])
            },
            'accuracy_metrics': {t: {f'avg_{k}': avg(v) for k, v in m.items()} for t, m in tool_accuracy.items()},
            'platform_statistics': platform_stats,
            'results': self.comparison_results
        }
        
        with open(report_file, 'w', encoding='utf-8') as f:
            yaml.dump(self._make_yaml_safe(report), f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        # Print summary
        logger.info(f"\n{'='*60}\nReport Summary\n{'='*60}")
        logger.info(f"Entries: {report['metadata']['total']} | Completed: {report['metadata']['completed']} | Platforms: {platform_stats['total_platforms']}")
        
        for name, info in available_tools.items():
            if name in platform_stats:
                s = platform_stats[name]
                logger.info(f"{info['name']}: exact={s['exact_matches']}({s['exact_match_rate']:.1f}%) partial={s['partial_matches']} none={s['no_matches']}")
        
        logger.info(f"\nSaved to: {report_file}")
        return report_file
        
    def _make_yaml_safe(self, obj):
        """Convert objects to YAML-safe format."""
        if isinstance(obj, dict):
            return {str(k): self._make_yaml_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._make_yaml_safe(i) for i in obj]
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        try:
            return str(obj)
        except Exception:
            return f"<{type(obj).__name__}>"
    
    def _save_entry_result(self, result: Dict, result_dir: str):
        """Save single entry result to JSON file (combined dir only; timestamps stripped)."""
        filepath = os.path.join(result_dir, f"{result['rosdep_key']}.json")
        clean = {k: v for k, v in result.items() if k != 'timestamp'}
        if 'detailed_package_comparison' in clean:
            clean['detailed_package_comparison'] = {
                k: v for k, v in clean['detailed_package_comparison'].items()
                if k != 'analysis_timestamp'
            }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self._make_yaml_safe(clean), f, indent=2, default=str, ensure_ascii=False)
        
    def _init_markdown_file(self, markdown_file: str, available_tools: Dict = None):
        """Initialize markdown analysis file."""
        available_tools = available_tools or self.available_tools
        tool_list = [f"{info['emoji']} **{info['name']}** - {info['description']}" for info in available_tools.values()]
        tool_list.append("✅ **RosdepDatabase (Ground Truth)**")

        content = f"""# Distribution Comparison Report

## Tools Compared

{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(tool_list))}

## Legend

- ✅ Exact match | ⚠️ Partial match | ❌ No match

---
"""
        with open(markdown_file, 'w', encoding='utf-8') as f:
            f.write(content)
            
    def _append_detailed_analysis_to_markdown(self, result: Dict, markdown_file: str, available_tools: Dict = None):
        """Append binname analysis to markdown file."""
        available_tools = available_tools or self.available_tools
        if result['status'] != 'completed':
            return
        
        detailed = result.get('detailed_package_comparison', {})
        if detailed.get('status') != 'completed':
            return

        summary = detailed['summary']
        rosdep_key = result['rosdep_key']

        # Summary section
        md = f"\n\n## 📦 {rosdep_key}\n\n"
        md += f"Platforms: {summary['total_ground_truth_platforms']}\n\n"
        
        for name, info in available_tools.items():
            exact = summary.get(f'{name}_exact_matches', 0)
            partial = summary.get(f'{name}_partial_matches', 0)
            none = summary.get(f'{name}_no_matches', 0)
            md += f"- {info['name']}: ✅{exact} ⚠️{partial} ❌{none}\n"
        
        # Table
        headers = ["Platform", "Ground Truth"] + [info['name'] for info in available_tools.values()]
        md += "\n| " + " | ".join(headers) + " |\n"
        md += "|" + "|".join(["---"] * len(headers)) + "|\n"
        
        for platform, analysis in detailed['platform_binname_analysis'].items():
            gt = analysis['ground_truth_binnames']
            row = [f"**{platform}**", f"{self._format_binnames_short(gt)} ({len(gt)})"]
            
            for name in available_tools:
                bins = analysis.get(f'{name}_binnames', [])
                match = analysis.get(f'{name}_match_result', 'no_match')
                found = analysis.get(f'{name}_found_count', 0)
                status = self._get_match_status(match, found, len(gt))
                row.append(f"{self._format_binnames_short(bins)}<br/>{status}")
            
            md += "| " + " | ".join(row) + " |\n"
        
        with open(markdown_file, 'a', encoding='utf-8') as f:
            f.write(md)
            
    def _generate_overall_summary_statistics(self, available_tools: Dict = None) -> Dict:
        """Generate overall summary statistics across all completed entries."""
        available_tools = available_tools or self.available_tools
        stats = {'total_platforms': 0}
        for name in available_tools:
            stats[name] = {'exact_matches': 0, 'partial_matches': 0, 'no_matches': 0}
        
        for result in self.comparison_results:
            if result.get('status') != 'completed':
                continue
            detailed = result.get('detailed_package_comparison', {})
            if detailed.get('status') != 'completed':
                continue

            summary = detailed['summary']
            stats['total_platforms'] += summary['total_ground_truth_platforms']

            for name in available_tools:
                stats[name]['exact_matches'] += summary.get(f'{name}_exact_matches', 0)
                stats[name]['partial_matches'] += summary.get(f'{name}_partial_matches', 0)
                stats[name]['no_matches'] += summary.get(f'{name}_no_matches', 0)
        
        total = stats['total_platforms']
        for name in available_tools:
            s = stats[name]
            s['exact_match_rate'] = (s['exact_matches'] / total * 100) if total else 0
            s['partial_match_rate'] = (s['partial_matches'] / total * 100) if total else 0
            s['no_match_rate'] = (s['no_matches'] / total * 100) if total else 0
        
        return stats
        
    def _append_overall_summary_to_markdown(self, stats: Dict, markdown_file: str, available_tools: Dict = None):
        """Append overall summary to markdown file."""
        available_tools = available_tools or self.available_tools
        completed = len([r for r in self.comparison_results if r['status'] == 'completed'])
        
        md = f"\n\n---\n\n# Summary\n\n"
        md += f"**Entries**: {completed} | **Platforms**: {stats['total_platforms']}\n\n"
        md += "| Tool | Exact | Partial | None | Rate |\n|------|-------|---------|------|------|\n"
        
        ranking = []
        for name, info in available_tools.items():
            if name not in stats:
                continue
            s = stats[name]
            display_name = f"{info['emoji']} {info['name']}"
            md += f"| {display_name} | {s['exact_matches']} | {s['partial_matches']} | {s['no_matches']} | {s['exact_match_rate']:.1f}% |\n"
            ranking.append((display_name, s['exact_match_rate']))

        ranking.sort(key=lambda x: -x[1])
        md += "\n## Ranking\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, (name, rate) in enumerate(ranking):
            medal = medals[i] if i < 3 else f"{i+1}."
            md += f"{medal} **{name}**: {rate:.1f}%\n"


        with open(markdown_file, 'a', encoding='utf-8') as f:
            f.write(md)


def main():
    """Main function to run distribution comparison analysis."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run distribution comparison analysis')
    parser.add_argument('--mode', '-m', type=str, default='detector',
                       help='Mode to run: "detector" for w-value comparison, "llm" for LLM-based detection, "baseline" for baseline tools, "compare" for all tools')
    parser.add_argument('--force', '-f', action='store_true',
                       help='Force rerun even if cached results exist')
    parser.add_argument('--w', '-w', type=float, nargs='+', default=None,
                       help='List of w values to compare (e.g., --w 0.5 0.7 0.9)')
    parser.add_argument('--llm_presets', '-l', type=str, nargs='+', default=None,
                       help='List of LLM presets to auto-generate tools')
    args = parser.parse_args()
    
    logger.set_log_level(logging.INFO)
    
    base_dir = Path(__file__).parent
    ground_truth_dir = TOOL_PATH / 'artifact' / 'RQ4' / 'data'
    # Load filtered ground truth file which filter the entries with only one repo
    ground_truth_file = ground_truth_dir / "ground_truth_dataset_filtered.yaml"
    result_dir = ground_truth_dir / "result"
    markdown_file = ground_truth_dir / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    if not ground_truth_file.exists():
        logger.error(f"Error: Ground truth file not found: {ground_truth_file}")
        return
        
    logger.info("=== Initializing Analyzer ===")
    analyzer = DistributionComparisonAnalyzer(str(ground_truth_file))
    
    # Select tools and parameters based on mode
    w_values = args.w
    llm_presets = args.llm_presets

    save_combined = False

    if args.mode == 'detector':
        # Detector mode: run with different w values
        tools = ['detector']
        if w_values is None:
            w_values = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]
        logger.info(f"Mode: detector (w_values={w_values})")
    elif args.mode == 'baseline':
        tools = ['mapper', 'rosdep_ci']
        w_values = None
        llm_presets = None
        logger.info(f"Mode: baseline (tools={tools})")
    elif args.mode == 'llm':
        # LLM mode: run LLM-based detection with preset variants
        tools = ['llm']
        # Use provided presets or default to the four main models
        if llm_presets is None:
            llm_presets = ['glm-4.7', 'deepseek-v3.2', 'gemini-3-pro', 'claude-opus-4-5']
        w_values = None
        logger.info(f"Mode: llm (llm_presets={llm_presets})")
    elif args.mode == 'compare':
        tools = ['detector', 'llm', "mapper", "rosdep_ci"]
        w_values = [0.7]
        llm_presets = ['glm-4.7', 'gemini-3-pro', 'claude-opus-4-5', 'deepseek-v3.2']
        save_combined = True
        logger.info(f"Mode: compare (tools={tools})")
    else:
        return
        
  
    logger.info("=== Running Comparison ===")
    results = analyzer.run_full_comparison(
        result_dir=str(result_dir),
        markdown_file=str(markdown_file),
        tools=tools,
        force_rerun=args.force,
        w_values=w_values,
        llm_presets=llm_presets,
        save_combined=save_combined,
    )
    
    logger.info(f"\n=== Complete ===")
    logger.info(f"Processed: {len(results)} entries")
    logger.info(f"Results: {result_dir}")
    logger.info(f"Report: {markdown_file}")
    
    # Generate summary report
    report = analyzer.generate_comparison_report(str(ground_truth_dir / "reports"))
    logger.info(f"Summary: {report}")


if __name__ == "__main__":
    main()
