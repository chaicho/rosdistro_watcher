from __future__ import annotations

import re
from typing import FrozenSet, List, Set, Dict

AFFIX_TO_ROLE = {
    # prefixes (ecosystem)
    "python3-": "py3",
    "python3Packages.": "py3",
    "python2-": "py2",
    "python-": "py",
    "py3-": "py3",
    "py2-": "py2",
    "py-": "py",
    "rubygem-": "ruby",
    "ruby-": "ruby",
    "nodejs-": "node",
    "node-": "node",
    "golang-": "go",
    "librust-": "rust",
    "rust-": "rust",
    "haskell-": "haskell",
    "perl-": "perl",
    "php-": "php",
    "lua-": "lua",
    "erlang-": "erlang",
    "elixir-": "elixir",
    "java-": "java",
    "octave-": "octave",
    "r-": "r",
    "ros-": "ros",
    "texlive-": "tex",

    # lib prefix marker
    "lib": "lib-prefix",

    # suffixes (functional)
    "-dev": "dev",
    "-devel": "dev",
    "-development": "dev",
    "-headers": "dev-helper",
    "-static": "dev-helper",
    "-api": "dev-helper",
    "-proto": "dev-helper",
    "-debuginfo": "debug",
    "-debug": "debug",
    "-dbg": "debug",
    "-dbgsym": "debug",
    "-debugsource": "debug-source",

    "-doc": "doc",
    "-docs": "doc",
    "-documentation": "doc",
    "-man": "doc",
    "-manual": "doc",
    "-info": "doc",
    "-examples": "examples",
    "-example": "examples",
    "-samples": "examples",
    "-demo": "examples",
    "-demos": "examples",

    "-data": "data",
    "-assets": "data",
    "-resources": "data",
    "-fonts": "data",
    "-themes": "data",
    "-icons": "data",
    "-media": "data",

    "-test": "test",
    "-tests": "test",
    "-testing": "test",
    "-qa": "test",

    "-locale": "locale",
    "-locales": "locale",
    "-translations": "lang",
    "-lang": "lang",
    "-i18n": "lang",

    # keep these distinct (finer than "common")
    "-common": "common",
    "-core": "core",
    "-base": "base",

    "-runtime": "runtime",
    "-libs": "runtime-lib",
    "-lib": "runtime-lib",
    "-headless": "headless",

    "-bin": "bin",
    "-cli": "cli",
    "-gui": "gui",
    "-utils": "tools",
    "-util": "tools",
    "-tools": "tools",

    "-server": "server",
    "-client": "client",
    "-daemon": "daemon",
    "-service": "service",
    "-agent": "agent",

    "-plugins": "plugins",
    "-plugin": "plugins",
    "-addon": "addons",
    "-addons": "addons",
    "-extra": "addons",
    "-extras": "addons",
    "-contrib": "addons",
    "-extension": "extensions",
    "-extensions": "extensions",
    "-module": "modules",
    "-modules": "modules",

    "-cpp": "cpp",
    "-java": "java",
    "-perl": "perl",
    "-ocaml": "ocaml",
    "-xml": "xml",

    "-src": "source",
    "-source": "source",
    
    "-full": "application",
    "-compat" : "compat",
    "compat-" : "compat",
    '-private' : "private",
}

RELATED_GROUPS: List[FrozenSet[str]] = [
    # build/runtime family (allow distro split differences)
    frozenset({
        "dev", "application",
        "runtime", "runtime-lib"
    }),
    
    frozenset({"compat", "application"}),
    
    frozenset({"plugins", "modules", "addons", "extensions"}),
    # services
    frozenset({"server", "client", "daemon", "service", "agent"}),
    # docs/examples
    frozenset({"doc", "examples"}),
    # i18n
    frozenset({"lang", "locale"}),
    # debug
    frozenset({"debug", "debug-source"}),
    # ecosystem (prefix roles)
    frozenset({"py", "py3", "application", "py3x"}),
]

# Debian SONAME libs like libssl3 / libicu72 / libpng16-16
_RE_DEBIAN_SONAME_LIB = re.compile(r"^lib[a-z0-9][a-z0-9+\-\.]*\d+([.\-]\d+)*$")

_RE_PREFIX_PATTERNS = [
    (re.compile(r"^python3(\d+)-"), "py3x"),
    (re.compile(r"^python2(\d+)-"), "py2x"),
    (re.compile(r"^ruby(\d+)-"), "ruby"),
    (re.compile(r"^node(\d+)-"), "node"),
    (re.compile(r"^php(\d+)-"), "php"),
    (re.compile(r"^python3(\d+)packages\."), "py3x"),
    (re.compile(r"^python3packages\."), "py3"),
    (re.compile(r"^python2(\d+)packages\."), "py2x"),
    (re.compile(r"^python2packages\."), "py2"),
    (re.compile(r"^pythonpackages\."), "py"),
    (re.compile(r"^ruby(\d*)packages\."), "ruby"),
    (re.compile(r"^node(\d*)packages\."), "node"),
    (re.compile(r"^php(\d*)packages\."), "php"),
    (re.compile(r"^perl(\d*)packages\."), "perl"),
    (re.compile(r"^\$\{?python-pn\}?-"), "py3"),
    (re.compile(r"^\$\{?python2-pn\}?-"), "py2"),
    (re.compile(r"^\$\{?pvn\}?-"), "pvn"),
    (re.compile(r"^python%\{?python3-pkgversion\}?"), "py3"),
    (re.compile(r"^python%\{?python2-pkgversion\}?"), "py2"),
    (re.compile(r"^python%\{?python-pkgversion\}?"), "py"),
    (re.compile(r"^python3%\{?python3-pkgversion\}?"), "py3"),
]

_RE_VERSION_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)*)$"),
    re.compile(r"-(\d+(?:\.\d+)*)$"),
    re.compile(r"-(\d+(?:\.\d+)*)-"),
    re.compile(r"^(\d+(?:\.\d+)*)-"),
    re.compile(r"-(\d+)\*"),
]


LIB_PREFIX_EXCLUDE_PREFIXES = [
    "libreoffice",
    "librewolf",
    "librecad",
    "libtool",
]

_PREFIX_AFFIXES = sorted([a for a in AFFIX_TO_ROLE if not a.startswith("-")], key=len, reverse=True)
_SUFFIX_AFFIXES = sorted([a for a in AFFIX_TO_ROLE if a.startswith("-")], key=len, reverse=True)

_ROLE_TO_PREFIXES: Dict[str, List[str]] = {"runtime-lib": ["lib"]}
_ROLE_TO_SUFFIXES: Dict[str, List[str]] = {}

for affix, role in AFFIX_TO_ROLE.items():
    if affix.startswith('-'):
        if role not in _ROLE_TO_SUFFIXES:
            _ROLE_TO_SUFFIXES[role] = []
        _ROLE_TO_SUFFIXES[role].append(affix)
    else:
        if role not in _ROLE_TO_PREFIXES:
            _ROLE_TO_PREFIXES[role] = []
        _ROLE_TO_PREFIXES[role].append(affix)



def normalize_name(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def normalize_special_package_name(package_name: str) -> str:
    """
    Normalize package names from special repositories like gentoo and openembedded.

    Handles:
    - Quotes: 'package' -> package
    - OpenEmbedded layers: package@layer -> package
    - Gentoo categories: category/package -> package
    - Wildcards: package* -> package
    - Version suffixes: package:version -> package
    """
    pkg = package_name.lower()

    if pkg.startswith("'") and pkg.endswith("'"):
        pkg = pkg[1:-1]

    if '@' in pkg:
        pkg = pkg.split('@')[0]

    if '/' in pkg:
        pkg = pkg.split('/')[-1]

    pkg = pkg.replace('*', '')

    if ':' in pkg:
        pkg = pkg.split(':')[0]

    return pkg


def infer_roles(package_name: str) -> Set[str]:
    n = normalize_name(package_name)
    roles: Set[str] = set()
    s = n

    while True:
        matched = False
        for pattern, role in _RE_PREFIX_PATTERNS:
            match = pattern.match(s)
            if match:
                roles.add(role)
                s = s[match.end():]
                matched = True
                break

        if not matched:
            for p in _PREFIX_AFFIXES:
                if s.startswith(p):
                    roles.add(AFFIX_TO_ROLE[p])
                    s = s[len(p):]
                    matched = True
                    break

        if not matched:
            break

    while True:
        matched = False
        for suf in _SUFFIX_AFFIXES:
            if s.endswith(suf):
                roles.add(AFFIX_TO_ROLE[suf])
                s = s[:-len(suf)]
                matched = True
                break
        if not matched:
            break

    if "-plugin-" in n:
        roles.add("plugins")

    if _RE_DEBIAN_SONAME_LIB.match(n):
        roles.add("runtime-lib")

    if n.startswith("lib") and not any(n.startswith(x) for x in LIB_PREFIX_EXCLUDE_PREFIXES):
        roles.add("runtime-lib")
        if "lib-prefix" in roles:
          roles.remove("lib-prefix")

    if not roles:
      roles.add("application")
    return roles

def package_roles_related(package_name_a: str, package_name_b: str, tolerance=False) -> bool:
    roles_a = infer_roles(package_name_a)
    roles_b = infer_roles(package_name_b)
    if roles_a & roles_b:
        return True
    if tolerance:
        return roles_related(roles_a, roles_b)

def roles_related(roles_a: Set[str], roles_b: Set[str]) -> bool:
    for g in RELATED_GROUPS:
        if (roles_a & g) and (roles_b & g):
            return True
    if not roles_a or not roles_b:
        return True
    return False

def roles_related_score(source_package_name: str, candidate_package_name: str) -> float:
    source_roles = infer_roles(source_package_name)
    candidate_roles = infer_roles(candidate_package_name)
    if source_roles & candidate_roles:
      # return 0.8 + 0.2 * len(source_roles & candidate_roles) / len(source_roles)
      return len(source_roles & candidate_roles) / len(source_roles)
    else:
       if roles_related(source_roles, candidate_roles):
        if 'application' in source_roles or 'application' in candidate_roles:
              return 0.5
          # return 0.5
        return 0.0
       else:
          return 0.0
      


def clean_package_name(name: str, remove_version: bool = False, remove_symbols: bool = False,
                     remove_subversion: bool = False, distribution_specific: bool = True) -> str:
    """Clean package name by removing known prefix/suffix.

    Returns a name closer to the "base/upstream" name.

    Args:
        name: Package name to clean
        remove_version: Whether to remove version numbers
        remove_symbols: Whether to remove non-alphanumeric symbols
        remove_subversion: Whether to collapse subversions to single digit
        distribution_specific: Whether to handle distribution-specific formats
            (e.g., OpenEmbedded @layer, Gentoo category/package). Default True.
    """
    # First handle distribution-specific formats
    if distribution_specific:
        name = normalize_special_package_name(name)

    s = normalize_name(name)

    while True:
        matched = False
        for suf in _SUFFIX_AFFIXES:
            if s.endswith(suf):
                s = s[:-len(suf)]
                matched = True
                break
        if not matched:
            break

    while True:
        matched = False
        for pattern, role in _RE_PREFIX_PATTERNS:
            match = pattern.match(s)
            if match:
                s = s[match.end():]
                matched = True
                break

        if not matched:
            for p in _PREFIX_AFFIXES:
                if p == "lib":
                    if s.startswith("lib") and not any(s.startswith(x) for x in LIB_PREFIX_EXCLUDE_PREFIXES):
                        s = s[len(p):]
                        matched = True
                        break
                else:
                    if s.startswith(p):
                        s = s[len(p):]
                        matched = True
                        break

        if not matched:
            break

    if remove_version:
        s = re.sub(r"\d+(\.\d+)+", "", s)
        s = re.sub(r"\d+", "", s)

    if remove_subversion:
        s = re.sub(r"(\d+)(\.\d+)+", r"\1", s)

    if remove_symbols:
        s = re.sub(r"[^a-z0-9]", "", s)

    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s


def generate_name_variants(package_name: str) -> List[str]:
    """Generate name variants based on role and related role prefix/suffix combinations.

    For example: python3-numpy-dev generates py3-numpy, py-numpy, numpy-dev, etc.

    Args:
        package_name: Original package name

    Returns:
        List of generated variants (including original package name)
    """
    variants = set([package_name])

    if '_' in package_name:
        variants.add(package_name.replace('_', '-'))
    if '-' in package_name:
        variants.add(package_name.replace('-', '_'))

    basename = clean_package_name(package_name)

    variants.add(basename)

    if basename:
        original_roles = infer_roles(package_name)

        related_sets = set()

        for role in original_roles:
            role_group = None
            for group in RELATED_GROUPS:
                if role in group:
                    related_sets.add(group)
                    role_group = group

            if role_group is None:
                related_sets.add(role)

        for related_set in related_sets:
            prefixes_to_try = ['']
            suffixes_to_try = ['']

            for role in related_set:
                if role in _ROLE_TO_PREFIXES:
                    prefixes_to_try.extend(_ROLE_TO_PREFIXES[role])
                if role in _ROLE_TO_SUFFIXES:
                    suffixes_to_try.extend(_ROLE_TO_SUFFIXES[role])

            prefixes_to_try = sorted(set(prefixes_to_try), key=lambda x: (len(x), x), reverse=True)
            suffixes_to_try = sorted(set(suffixes_to_try), key=lambda x: (len(x), x), reverse=True)

            for prefix in prefixes_to_try:
                for suffix in suffixes_to_try:
                    if prefix.strip('-') == suffix.strip('-') and prefix:
                       continue
                    if prefix and suffix:
                        variant = f"{prefix}{basename}{suffix}"
                    elif prefix:
                        variant = f"{prefix}{basename}"
                    elif suffix:
                        variant = f"{basename}{suffix}"
                    else:
                        variant = basename

                    if variant != package_name:
                        variants.add(variant)

    py_match = re.match(r'^python(\d)-(.*)', package_name)
    if py_match:
        python_version = py_match.group(1)
        python_package = py_match.group(2)
        variants.add(f'python{python_version}dist({python_package})')
        if '-' in python_package:
            variants.add(f'python{python_version}dist({python_package.replace("-", "_")})')
        if '_' in python_package:
            variants.add(f'python{python_version}dist({python_package.replace("_", "-")})')

    clean_name = basename if basename else package_name
    variants.add(f'cmake({clean_name})')
    variants.add(f'pkgconfig({clean_name})')

    cleaned_subversion_name = clean_package_name(package_name, remove_subversion=True)
    if cleaned_subversion_name != package_name and cleaned_subversion_name != clean_name:
        variants.update(generate_name_variants(cleaned_subversion_name))

    cleaned_version_name = clean_package_name(package_name, remove_version=True, remove_symbols=True)
    if cleaned_version_name != clean_name:
        variants.update(generate_name_variants(cleaned_version_name))

    return list(set(variants))
