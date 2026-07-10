import os
import re
import sys
from typing import Optional, Set

try:
    # When imported as part of the rosdep_auditor package
    from .name_pattern import clean_package_name  # type: ignore
except Exception:
    # When executed as a standalone script: python rosdep_auditor/tools/file_signature_extractor.py
    try:
        _TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
        _PROJECT_ROOT = os.path.dirname(os.path.dirname(_TOOLS_DIR))
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)
        from rosdep_auditor.tools.name_pattern import clean_package_name  # type: ignore
    except Exception:
        # Fallback: keep behavior stable even if import fails.
        def clean_package_name(name: str, *args, **kwargs) -> str:  # type: ignore
            return (name or "").strip().lower().replace("_", "-")


# =============================================================================
# Filtering Rules
# =============================================================================

IGNORE_PREFIXES = (
    "/usr/lib/.build-id/",
    "/usr/lib64/.build-id/",
    "/lib/.build-id/",
    "/lib64/.build-id/",
    "/usr/lib/debug/",
    "/usr/src/debug/",
    "/dev/", "/proc/", "/sys/",
    "/var/lib/dpkg/",
    "/var/lib/rpm/",
    "/var/lib/dnf/",
    "/var/lib/apt/",
    "/tmp/", "/var/tmp/", "/var/cache/",
)

IGNORE_SUBSTRINGS = (
    "share/lintian",
    "icons/hicolor",
)

IGNORE_SUFFIXES = (
    ".lock", ".log", ".pid", ".sock",
    ".conf", ".cfg", ".ini", ".json",
    ".yaml", ".yml", ".toml", ".qmltypes",
    ".md", ".rst", ".html",
    ".pot", ".mo",
    "/__init__.py", "setup.py", "setup.cfg", "MANIFEST.in", "pyproject.toml",
    "/COPYING", "/AUTHORS", "/ChangeLog", "/INSTALL", "/README",
)

PATTERN_GROUP_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".bmp", ".qml",
    ".otf", ".woff", ".woff2", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4",
    ".h", ".hpp", ".hxx", ".hh",
    ".c", ".cpp", ".cc", ".cxx",
    ".pyc", ".pyo",
    ".cmake",
)

LIB_BASENAME_GROUP_SUFFIXES = (".so", ".a", ".jar")

IGNORE_USR_SHARE_TOP = {
    "doc", "man", "info", "help", "gtk-doc",
    "licenses", "common-licenses",
}

IGNORE_TOPLEVEL_DIR_SUFFIXES = (".dist-info", ".egg-info")


# =============================================================================
# Path Normalization Rules
# =============================================================================

_TRIPLET_SEG_RE = re.compile(
    r"^[a-z0-9_+.-]+-(linux|gnu|musl|android|darwin|freebsd)(-[a-z0-9_+.-]+)?$",
    re.IGNORECASE,
)

_ARCH_TOKEN_RE = r"(?:aarch64|arm64|amd64|x86_64|i386|i686|armhf|armel|ppc64el|riscv64|s390x|mips64el)"
_ARCH_TOKENS = {
    "aarch64", "arm64", "amd64", "x86_64", "i386", "i686",
    "armhf", "armel", "ppc64el", "riscv64", "s390x", "mips64el",
}
_ARCH_SUFFIX_DIR_RE = re.compile(
    rf"^(?P<base>.+?)-(?P<arch>{_ARCH_TOKEN_RE})(?:-(?P<variant>base|baseline|generic))?$",
    re.IGNORECASE,
)

_ARCH_PURE_SEG_RE = re.compile(rf"^(?:{_ARCH_TOKEN_RE})$", re.IGNORECASE)

_VERSION_SEG_RE = re.compile(r"^\d+(?:\.\d+)*$")
CUT_PREFIXES = sorted((
    "/dist-packages/",
    "/site-packages/",
    "/usr/share/nodejs/",
    "/node_modules/",
    "/usr/share/java/",
    "/perl5/",
    "/opt/",
    "/usr/libexec/",
    "/usr/X11R6/bin/",
    "/usr/X11R6/lib64/",
    "/usr/X11R6/lib/",
    "/usr/X11R6/",
    "/usr/bin/X11/",
    "/srv/www/",
    "/srv/",
    "/usr/local/bin/",
    "/usr/local/sbin/",
    "/usr/local/lib64/",
    "/usr/local/lib/",
    "/usr/local/include/",
    "/usr/local/share/",
    "/usr/local/",
    "/usr/bin/",
    "/usr/sbin/",
    "/usr/lib64/",
    "/usr/lib/",
    "/usr/include/",
    "/usr/share/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/lib64/",
    "/lib/",
    "/etc/",
    "/run/",
    "/var/run/",
    "/var/opt/",
    "/var/cache/",
    "/var/lib/",
    "/var/log/",
    "/var/",
    "/boot/",
    "/efi/",
), key=len, reverse=True)


# =============================================================================
# Filename Normalization Rules
# =============================================================================

_SO_VER_AFTER_RE = re.compile(r"^(?P<stem>.+?\.so)(?:\.\d+)+$")
_SO_VER_BEFORE_RE = re.compile(r"^(?P<stem>lib.+?)-\d[\d.]*\.so$")
_PYEXT_RE = re.compile(
    r"^(?P<stem>.+?)\.(?:cpython-\d+[a-z]*|abi3|pypy\d+)(?:-[^.]+)*\.so$",
    re.IGNORECASE,
)
_JAR_VER_RE = re.compile(r"^(?P<name>.+?)-\d[\w.\-+]*\.jar$", re.IGNORECASE)


# =============================================================================
# Functions
# =============================================================================

def _should_ignore(p: str) -> bool:
    """Return True if path should be ignored."""
    if any(p.startswith(x) for x in IGNORE_PREFIXES):
        return True
    if any(x in p for x in IGNORE_SUBSTRINGS):
        return True
    if any(p.endswith(x) for x in IGNORE_SUFFIXES):
        return True
    if p.startswith("/usr/share/"):
        rest = p[len("/usr/share/"):]
        top = rest.split("/", 1)[0] if rest else ""
        if top in IGNORE_USR_SHARE_TOP:
            return True
    if p.endswith(".d") or ".d/" in p:
        return True
    return False


def _get_pattern_group_ext(path: str) -> Optional[str]:
    """Check if file should be grouped by extension pattern.

    Returns the extension if file matches pattern group suffixes AND has subdirectory
    after cutting prefix. If file is directly under prefix (no subdirectory), returns None
    to avoid too many conflicts.

    Note: Files with extensions in LIB_BASENAME_GROUP_SUFFIXES (.so, .a, .jar) are
    excluded from pattern grouping - they will only keep their basename.
    """
    _, ext = os.path.splitext(path)

    if ext and ext.lower() in LIB_BASENAME_GROUP_SUFFIXES:
        return None

    if (ext and ext.lower() in PATTERN_GROUP_SUFFIXES) or path.startswith("/usr/share/"):
        rel = _cut_by_prefix(path)
        if rel and rel.count("/") >= 3:
            return ext.lower()
        return None

    return None


def _cut_by_prefix(p: str) -> str:
    """Remove standard prefix and clean multiarch architecture segments."""
    for pref in CUT_PREFIXES:
        idx = p.find(pref)
        if idx != -1:
            rel = p[idx + len(pref):].lstrip("/")

            while rel:
                first_seg = rel.split("/", 1)[0]
                if _TRIPLET_SEG_RE.match(first_seg) or _ARCH_PURE_SEG_RE.match(first_seg) or _VERSION_SEG_RE.match(first_seg):
                    rel = rel[len(first_seg):].lstrip("/")
                    continue
                break

            if rel and "/" in rel:
                segs = [s for s in rel.split("/") if s]
                if len(segs) >= 2:
                    file_seg = segs[-1]
                    dir_segs = segs[:-1]

                    cleaned_dirs = []
                    for s in dir_segs:
                        if _TRIPLET_SEG_RE.match(s) or _ARCH_PURE_SEG_RE.match(s) or _VERSION_SEG_RE.match(s):
                            continue

                        s_norm = s.lower().replace("_", "-")
                        parts = [p for p in s_norm.split("-") if p]

                        if any(p in _ARCH_TOKENS for p in parts):
                            parts_wo_arch = [p for p in parts if p not in _ARCH_TOKENS]
                            if parts_wo_arch:
                                candidate = "-".join(parts_wo_arch)
                                s = clean_package_name(candidate)
                                if not s:
                                    s = candidate

                        cleaned_dirs.append(s)

                    rel = "/".join(cleaned_dirs + [file_seg]) if cleaned_dirs else file_seg
            return rel

    return p.lstrip("/")


def _normalize_basename(basename: str) -> str:
    """Normalize filename by removing version numbers and architecture suffixes."""
    m = _SO_VER_AFTER_RE.match(basename)
    if m:
        basename = m.group("stem")
    m = _SO_VER_BEFORE_RE.match(basename)
    if m:
        basename = m.group("stem") + ".so"
        return basename.lower()
    if _SO_VER_AFTER_RE.match(basename):
        return basename.lower()

    m = _PYEXT_RE.match(basename)
    if m:
        return m.group("stem") + ".so"

    m = _JAR_VER_RE.match(basename)
    if m:
        return m.group("name") + ".jar"

    return basename


def get_component_identifier(path: str, return_original: bool = False):
    """Convert file path to normalized component identifier.

    Returns None if file is determined to be noise or useless.

    Args:
        path: Original file path
        return_original: If True, return normalized original path instead of component_id

    Returns:
        - return_original=False (default): component_id (normalized identifier)
        - return_original=True: normalized original path
        - None if file is filtered
    """
    if not path:
        return None

    p = path.strip()
    if not p:
        return None
    if not p.startswith("/"):
        p = "/" + p

    if return_original:
        return p

    p = re.sub(r"/{2,}", "/", p)

    if _should_ignore(p):
        return None

    pattern_ext = _get_pattern_group_ext(p)
    if pattern_ext != None:
        d, _ = os.path.split(p)
        p = os.path.join(d, f"*{pattern_ext}")

    rel = _cut_by_prefix(p)
    if not rel:
        return None

    top_dir = rel.split("/", 1)[0]
    if top_dir.endswith(IGNORE_TOPLEVEL_DIR_SUFFIXES):
        return None

    d, b = os.path.split(rel)
    if not b:
        return None

    b_norm = _normalize_basename(b)

    if b_norm.lower().endswith(LIB_BASENAME_GROUP_SUFFIXES):
        return b_norm

    d = d.lower()
    b_norm = b_norm.lower()
    return f"{d}/{b_norm}" if d else b_norm
