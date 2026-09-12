# Bundled dependencies

The Windows ZIP includes the official Python 3.13.15 embeddable distribution.
Its license and bundled component notices are in `python/LICENSE.txt`.
Source and release information: https://www.python.org/downloads/release/python-31315/

The exact Python dependencies are in `requirements.txt`. Their original metadata
and licenses are retained under `vendor/quota/*dist-info/` in the Windows ZIP:

- pywinpty 3.0.5 (MIT): https://github.com/andfoy/pywinpty
- pyte 0.8.2 (LGPL-3.0-or-later): https://github.com/selectel/pyte
- wcwidth 0.8.3 (MIT): https://github.com/jquast/wcwidth

These packages are distributed as separate, unmodified, replaceable files.
The complete pyte 0.8.2 source archive is included at
`third-party/sources/pyte-0.8.2.tar.gz` in the Windows ZIP.
Provider CLIs, model weights, VS Code and provider credentials are not bundled.
