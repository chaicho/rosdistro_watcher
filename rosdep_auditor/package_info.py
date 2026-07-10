from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List


@dataclass
class PackageInfo:
    """Package information data class."""
    name: str
    bin_name: str
    repo_name: str
    repo_version: str
    src_name: Optional[str] = None
    version: Optional[str] = None
    arch: Optional[str] = None
    description: Optional[str] = None
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    filelist: Optional[List[str]] = None
    final_score: float = 0.0

    def __eq__(self, other):
        """Compare two PackageInfo instances for equality based on src_name and bin_name"""
        if not isinstance(other, PackageInfo):
            return False
        return (self.src_name == other.src_name and
                self.bin_name == other.bin_name and
                self.repo_name == other.repo_name and
                self.repo_version == other.repo_version)

    def __hash__(self):
        return hash((self.src_name, self.bin_name, self.repo_name, self.repo_version))

    def __str__(self):
        """String representation of PackageInfo"""
        file_count = len(self.filelist) if self.filelist else 0
        return (f"PackageInfo(name='{self.name}', repo_name='{self.repo_name}', repo_version='{self.repo_version}'), bin_name='{self.bin_name}', src_name='{self.src_name}', arch='{self.arch}', description='{self.description}', files={file_count}, metadata='{self.metadata}'\n")

    @classmethod
    def from_package_entry(cls, package_entry, repo_name: str = "unknown", repo_version: str = "unknown"):
        """
        Create PackageInfo from PackageEntry

        Args:
            package_entry: PackageEntry instance
            repo_name: Name of the repository (default: "unknown")
            repo_version: Version of the repository (default: "unknown")
        """
        metadata = {}
        if hasattr(package_entry, 'url') and package_entry.url:
            metadata['url'] = package_entry.url

        # Handle description - convert list to string if needed
        description = package_entry.description
        if isinstance(description, list):
            # For Debian packages, join multiline descriptions
            description = ' '.join(description) if description else ''

        return cls(
            name=package_entry.name,
            bin_name=package_entry.binary_name,
            repo_name=repo_name,
            repo_version=repo_version,
            src_name=package_entry.source_name if package_entry.source_name else None,
            version=package_entry.version if package_entry.version else None,
            arch=None,
            description=description,
            confidence=1.0,
            metadata=metadata if metadata else None,
            filelist=getattr(package_entry, 'filelist', None) or None,
        )

    def get_repo_key(self):
        if self.repo_version == '' or self.repo_version is None:
            return self.repo_name
        else:
            return self.repo_name + "_" + self.repo_version

    def to_dict(self) -> dict:
        """
        Convert PackageInfo to dictionary for JSON serialization.

        Returns:
            Dictionary representation of PackageInfo
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'PackageInfo':
        """
        Reconstruct PackageInfo from dictionary.

        Args:
            data: Dictionary representation of PackageInfo

        Returns:
            PackageInfo instance
        """
        return cls(**data)

    def merge(self, other: 'PackageInfo') -> 'PackageInfo':
        """
        Merge metadata from another equal PackageInfo into this one.

        This is used during deduplication to ensure metadata (like 'source')
        is preserved when merging duplicate packages from different sources.

        The 'source' field is treated specially - it is converted to a list
        and all unique sources are preserved (e.g., ['upstream', 'name', 'filelist']).

        Args:
            other: Another PackageInfo instance to merge from

        Returns:
            PackageInfo: Returns self for method chaining
        """
        if not isinstance(other, PackageInfo) or self != other:
            return self

        # Merge metadata with special handling for 'source'
        if other.metadata:
            if self.metadata is None:
                self.metadata = {}

            for key, value in other.metadata.items():
                if key == 'source':
                    # Special handling: merge sources into a list
                    self_source = self.metadata.get('source')
                    other_source = value

                    # Convert existing source to list if needed
                    if self_source is None:
                        self_sources = []
                    elif isinstance(self_source, list):
                        self_sources = self_source
                    else:
                        self_sources = [self_source]

                    # Add other source(s)
                    if isinstance(other_source, list):
                        for src in other_source:
                            if src not in self_sources:
                                self_sources.append(src)
                    elif other_source not in self_sources:
                        self_sources.append(other_source)

                    self.metadata['source'] = self_sources
                elif key not in self.metadata:
                    self.metadata[key] = value

        # Fill in missing fields (prefer non-empty values)
        if not self.src_name and other.src_name:
            self.src_name = other.src_name
        if not self.version and other.version:
            self.version = other.version
        if not self.arch and other.arch:
            self.arch = other.arch
        if not self.description and other.description:
            self.description = other.description
        if not self.filelist and other.filelist:
            self.filelist = other.filelist

        # Keep higher confidence
        if other.confidence > self.confidence:
            self.confidence = other.confidence

        return self

    @classmethod
    def deduplicate(cls, packages: List['PackageInfo']) -> List['PackageInfo']:
        """
        Deduplicate a list of PackageInfo while merging metadata from duplicates.

        Unlike list(set(packages)), this method ensures that metadata from all
        duplicate packages is merged together, preserving important information
        like 'source': 'upstream'.

        Args:
            packages: List of PackageInfo to deduplicate

        Returns:
            List[PackageInfo]: Deduplicated list with merged metadata
        """
        if not packages:
            return []

        unique: Dict[int, 'PackageInfo'] = {}
        for pkg in packages:
            pkg_hash = hash(pkg)
            if pkg_hash in unique:
                unique[pkg_hash].merge(pkg)
            else:
                unique[pkg_hash] = pkg

        return list(unique.values())


class PackageEntry(str):
    """Lightweight data bag for information about an entry in a repository."""

    __slots__ = ('name', 'version', 'url', 'source_name', 'binary_name', 'description', 'filelist')

    def __new__(cls, name, version, url, source_name=None, binary_name=None, description=None, filelist=None):
        obj = str.__new__(cls, name)
        obj.name = str(name)
        obj.version = str(version) if version is not None else None
        obj.url = str(url) if url is not None else None
        obj.source_name = str(source_name) if source_name is not None else str(name)
        obj.binary_name = str(binary_name) if binary_name is not None else str(name)
        obj.description = description
        obj.filelist = list(set(filelist)) if filelist is not None else None
        return obj
