"""Build the Windows x64 helper and verify both template and paired EXEs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-build-tools', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build this Windows executable on Windows with 64-bit Python.')
    if args.install_build_tools:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'tools/assistant-build-requirements.txt')], check=True)
    output = ROOT / 'assistant_dist'
    work = ROOT / 'work' / 'assistant-build'
    output.mkdir(exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
               '--name', 'AIStudioAssistant', '--paths', str(ROOT), '--distpath', str(output),
               '--workpath', str(work / 'build'), '--specpath', str(work)]
    for module in ('aiortc', 'av'):
        command += ['--collect-all', module]
    command += ['--collect-submodules', 'websockets']
    for module in ('tkinter', 'tkinter.filedialog', 'tkinter.messagebox', 'win32file', 'win32api',
                   'win32crypt', 'pywintypes', 'file_mcp.agent', 'file_mcp.rpc', 'file_mcp.files',
                   'file_mcp.safe_fs', 'file_mcp.desktop_bootstrap', 'file_mcp.desktop_assistant',
                   'file_mcp.desktop_turn', 'websockets.asyncio.client', 'aioice.turn'):
        command += ['--hidden-import', module]
    command += [str(ROOT / 'file_mcp/desktop_assistant.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    template = output / 'AIStudioAssistant.exe'
    from file_mcp.desktop_bootstrap import build_download
    paired = work / 'AIStudioAssistant-paired-test.exe'
    paired.write_bytes(build_download(template.read_bytes(), {
        'version': 1, 'server': 'http://127.0.0.1:8000', 'pairing_id': 'build-smoke-test',
        'ticket': 'build-test-not-an-active-ticket-' + 'a' * 32, 'expires_at': time.time() + 600,
    }))
    for executable, expected in ((template, 'absent'), (paired, 'valid')):
        report = work / (executable.stem + '-self-test.json')
        report.unlink(missing_ok=True)
        subprocess.run([str(executable), '--self-test-report', str(report)], timeout=120, check=True)
        result = json.loads(report.read_text(encoding='utf-8'))
        if not result['ok'] or result['bootstrap'] != expected:
            raise RuntimeError('Frozen helper self-test failed: ' + str(report))
    print('Built and verified: ' + str(template))
    print('Template and personalized EXE both start without Python or runtime dependency installation.')


if __name__ == '__main__':
    main()
