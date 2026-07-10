import unittest
import sys
import os

# Add parent directory to path for direct import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.file_signature_extractor import get_component_identifier


class TestFileSignatureExtractor(unittest.TestCase):
    """Test file signature extraction functionality"""

    def test_component_identifier_extraction(self):
        """Test component identifier extraction with actual expected outputs"""
        test_cases = [
            ('/usr/share/libvncserver/examples/client/ppmtest', 'libvncserver/examples/client/*'),
            ('/usr/bin/clang-format', 'clang-format'),
            ('/usr/bin/core_perl/perl', 'core_perl/perl'),
            ('/usr/local/bin/cmake', 'cmake'),
            ('/usr/lib64/libcurl.so.4.8.0', 'libcurl.so'),
            ('/usr/lib64/libz.so.1', 'libz.so'),
            ('/usr/include/openssl/ssl.h', 'openssl/ssl.h'),
            ('/usr/include/zlib.h', 'zlib.h'),
            ('/etc/nginx/nginx.conf', None),  # filtered
            ('/etc/sshd_config', 'sshd_config'),
            ('/usr/lib/systemd/system/docker.service', 'systemd/system/docker.service'),
            ('/usr/lib64/libavcodec.a', 'libavcodec.a'),
            ('/usr/share/doc/nginx/README', None),  # filtered
            ('/usr/lib64/qt6/qml/QtQuick/Dialogs/Dialog.qml', 'qt6/qml/qtquick/dialogs/*.qml'),
            ('/usr/lib64/qt6/qml/QtQuick/Dialogs/libqtquickdialogsplugin.so', 'libqtquickdialogsplugin.so'),
            ('/usr/lib/systemd/system/docker.d', None),  # filtered
            ('/etc/X11/app-defaults/XBarrel', 'x11/app-defaults/xbarrel'),
            ('/usr/lib/python3/dist-packages/bson/_cbson.cpython-312-x86_64-linux-gnu.so', '_cbson.so'),
            ('/usr/lib/python3/dist-packages/bson/code.py', 'bson/code.py'),
            ('/usr/lib/.build-id/3a/a0a0a6f98f668a3c635153764b917f5b4ee7d6', None),  # filtered
            ('/lib64/python3.14/site-packages/gi/_gi_cairo.cpython-314-x86_64-linux-gnu.so', '_gi_cairo.so'),
            ('/usr/include/x86_64-linux-gnu/qt6/QtCore/6.4.2/QtCore/private/qcoffpeparser_p.h', 'qt6/qtcore/qtcore/private/*.h'),
            ('/usr/share/man/man1/ls.1.gz', None),  # filtered
            ('/usr/share/locale/zh_CN/LC_MESSAGES/wget.mo', None),  # filtered
            ('/usr/share/icons/hicolor/48x48/apps/firefox.png', None),  # filtered
            ('/usr/lib/debug/usr/bin/ls.debug', None),  # filtered
            ('/usr/lib/python3/dist-packages/pip/__init__.py', None),  # filtered
            ('/usr/share/doc/curl/COPYING', None),  # filtered
            ('/etc/X11/Xsession.d/99x11-common_start', None),  # filtered
            ('/usr/lib/.build-id/4b/e2d752f573b051340b4ccb70868e8842f6c664', None),  # filtered
            ('/usr/lib64/.build-id/4b/e2d752f573b051340b4ccb70868e8842f6c664', None),  # filtered
            ('pymongo-4.16.0.dist-info/licenses/LICENSE', None),  # filtered
            ('/usr/share/lintian/overrides/libqt5quick5', None),  # filtered
            ('/usr/include/x86_64-linux-gnu/atlas/atlas_csysinfo.h', 'atlas/atlas_csysinfo.h'),
            ('/usr/include/atlas-aarch64-base/zmm.h', 'atlas/zmm.h'),
            ('/usr/include/aarch64/atlas/atlas_csysinfo.h', 'atlas/atlas_csysinfo.h'),
            ('/usr/lib/x86_64-linux-gnu/qt6/qml/QtQml/libqmlplugin.so', 'libqmlplugin.so'),
            ('/usr/lib/qt6/qml/QtQml/libqmlplugin.so', 'libqmlplugin.so'),
            ('orjson/orjson.cpython-310-darwin.so', 'orjson.so'),
            ('/usr/lib/python3.6/site-packages/fabric/__pycache__/__init__.cpython-36.pyc', 'fabric/__pycache__/__init__.cpython-36.pyc'),
            ('/usr/lib/python3.6/site-packages/fabric/__pycache__/__main__.cpython-36.pyc', 'fabric/__pycache__/__main__.cpython-36.pyc'),
            ('/usr/share/icons/Adwaita/16x16/actions/call-stop-symbolic.symbolic.png', 'icons/adwaita/16x16/actions/*.png'),
            ('/usr/share/icons/adwaita/cursors/alias', 'icons/adwaita/cursors/*'),
            ('/usr/lib64/libSDL_ttf-2.0.so.0', 'libsdl_ttf.so'),
            ('/usr/share/java/mockito-core-2.23.0.jar', 'mockito-core.jar'),
            ('/usr/share/java/mockito/mockito-core-2.23.0.jar', 'mockito-core.jar'),
            ('/usr/include/x86_64-linux-gnu/qt6/QtCore/6.4.2/QtCore/private/qcoffpeparser_p.h', 'qt6/qtcore/qtcore/private/*.h'),
            ('/usr/lib/python3/dist-packages/PyQt6/QtWebEngineCore.abi3.so', 'QtWebEngineCore.so'),
        ]
        for path, expected in test_cases:
            with self.subTest(path=path):
                result = get_component_identifier(path)
                self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()
