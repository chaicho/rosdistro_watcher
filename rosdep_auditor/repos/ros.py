from .deb import enumerate_deb_packages
from .. import RepositoryCacheCollection

import os

_USE_FULLPATH = os.getenv('ROSDEP_USE_FULLPATH', 'false').lower() == 'true'

def enumerate_ros_packages(base_url, comp, os_code_name, os_arch, return_paths=False):
    """
    Enumerate ROS packages in a repository.

    :param base_url: the ROS repository base URL.
    :param comp: the component of the repository to enumerate.
    :param os_code_name: the OS version associated with the repository.
    :param os_arch: the system architecture associated with the repository.

    :returns: an enumeration of package entries.
    """
    # ROS repositories follow the same structure as Debian repositories
    return enumerate_deb_packages(base_url, comp, os_code_name, os_arch, return_paths=_USE_FULLPATH)


def ros_base_url(base_url, comp, return_paths=False):
    """
    Create an enumerable cache for a ROS repository.

    :param base_url: the URL of the ROS repository.
    :param comp: the component of the repository (usually 'main').

    :returns: an enumerable repository cache instance.
    """
    return RepositoryCacheCollection(
        lambda os_name, os_code_name, os_arch:
            enumerate_ros_packages(base_url, comp, os_code_name, os_arch, return_paths=return_paths)) 
    
def get_ros_package_name(pkg_name):
    """
    Get the ROS package name from the package name.
    """
    if 'ros-' in pkg_name:
        pkg_name = pkg_name.split('-', 2)[2]
        return pkg_name
    return pkg_name