"""Package the local extension without downloads, build tools or publishing."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(destination):
    source = ROOT / 'extensions/vscode-bots'
    manifest = '''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
<Metadata><Identity Language="en-US" Id="agent-orchestrator-bots" Version="0.1.0" Publisher="local-orchestrator"/>
<DisplayName>Agent Orchestrator Bots</DisplayName><Description xml:space="preserve">Local VS Code orchestration bots</Description><Tags>orchestration</Tags><Categories>Other</Categories>
<Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.95.0"/><Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/><Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/></Properties></Metadata>
<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/>
<Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets></PackageManifest>'''
    types = '''<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="json" ContentType="application/json"/><Default Extension="js" ContentType="application/javascript"/><Default Extension="vsixmanifest" ContentType="text/xml"/></Types>'''
    with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('extension.vsixmanifest', manifest)
        archive.writestr('[Content_Types].xml', types)
        for name in ('package.json', 'extension.js', 'bridge.js'):
            archive.write(source / name, 'extension/' + name)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    package(args.destination)
    print('Local VS Code bot extension packaged.')
