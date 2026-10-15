#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Check composed Make policy and rust-analyzer workspace routing."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from cargo_workspace import member_directories


class EntrypointTests(unittest.TestCase):
    def test_workspace_discovery_agrees_with_cargo_when_an_app_is_added(self):
        with tempfile.TemporaryDirectory(prefix='hyper workspace discovery ') as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'rust-toolchain.toml', root / 'rust-toolchain.toml')
            app = root / 'app'
            app.mkdir()
            manifest = app / 'Cargo.toml'
            manifest.write_text('[workspace]\nresolver="3"\n'
                                'members=["*", "../tests/app/*", "../lib/args", "../lib/*/shared"]\n'
                                'exclude=[".cargo", "target"]\n')
            original = manifest.read_bytes()
            (app / 'README.md').write_text('not a package')
            (app / '.cargo').mkdir()
            (app / 'target/debug').mkdir(parents=True)

            def package(relative, owner):
                path = root / relative
                (path / 'src').mkdir(parents=True)
                (path / 'src/lib.rs').write_text('')
                (path / 'Cargo.toml').write_text(
                    f'[package]\nname="{relative.replace("/", "-")}"\n'
                    f'version="0.0.0"\nworkspace="{owner}"\n')

            for relative, owner in [('app/tool', '..'), ('lib/args', '../../app'),
                                    ('lib/args/shared', '../../../app'),
                                    ('tests/app/probe', '../../../app')]:
                package(relative, owner)
            expected = {root / path for path in ('app/tool', 'lib/args', 'lib/args/shared',
                                                 'tests/app/probe')}
            for added in (False, True):
                if added:
                    package('app/new-tool', '..')
                    expected.add(root / 'app/new-tool')
                metadata = json.loads(subprocess.check_output(
                    ['cargo', 'metadata', '--offline', '--no-deps', '--format-version=1',
                     '--manifest-path', str(manifest)], cwd=root))
                cargo_members = {Path(item['manifest_path']).parent.resolve()
                                 for item in metadata['packages']
                                 if item['id'] in metadata['workspace_members']}
                self.assertEqual(cargo_members, {path.resolve() for path in expected})
                self.assertEqual(member_directories(manifest), cargo_members)
                self.assertEqual(manifest.read_bytes(), original)

    def test_application_feature_variants_do_not_share_default_outputs(self):
        def outputs(*arguments):
            with tempfile.NamedTemporaryFile(mode='w', suffix='.mk') as probe:
                probe.write('__variant:\n\t@printf "%s\\n" "$(APP_OUTPUT)" "$(APP_CARGO_OUTPUT)"\n')
                probe.flush()
                return subprocess.check_output(['make', '--no-print-directory', '-s', '-f',
                                                'Makefile', '-f', probe.name, '__variant',
                                                *arguments], cwd=ROOT, text=True).splitlines()
        ordinary = outputs()
        first = outputs('APP_FEATURES=hyper-io-runtime/storage-probe,hyper-vm-runtime/broker-test')
        reordered = outputs('APP_FEATURES=hyper-vm-runtime/broker-test hyper-io-runtime/storage-probe')
        other = outputs('APP_FEATURES=hyper-io-runtime/storage-probe')
        probe = outputs('APP_EXTRA_BINS=hyper-storage-probe')
        self.assertEqual(first, reordered)
        for column in (0, 1):
            self.assertEqual(len({ordinary[column], first[column], other[column], probe[column]}), 4)

    def test_make_composition_preserves_disk_policy(self):
        for arch in ('aarch64', 'riscv64', 'x86_64'):
            with tempfile.NamedTemporaryFile(mode='w', suffix='.mk') as probe:
                probe.write('__hyper_contract_dump:;\n')
                probe.flush()
                result = subprocess.run(
                    ['make', '-p', '-f', 'Makefile', '-f', probe.name, '__hyper_contract_dump',
                     f'ARCH={arch}'], cwd=ROOT, check=True, capture_output=True, text=True)
            database = result.stdout
            self.assertRegex(database, r'(?m)^\.DEFAULT_GOAL\s*:?=\s*all$')
            expected = 'board-build' if arch == 'aarch64' else ('image native-initramfs' if arch == 'riscv64' else 'image')
            self.assertRegex(database, rf'(?m)^all: {expected}$')
            self.assertNotRegex(database, r'(?m)^BOARD_SYSTEM_LAYOUT\s*[:?]?=')
            self.assertRegex(database, r'(?m)^APP_BUILD_PROFILE\s*[:?]?=\s*system$')
            self.assertRegex(database, r'(?m)^board-run: board-check-system$')
            self.assertRegex(database, r'(?m)^board-rebuild: image board-initramfs board-guest-images$')
            # Ask Make for its evaluated recipes, not a guessed source filename.
            rebuild = re.search(r'(?ms)^board-rebuild:.*?(?=^[^\s#]|\Z)', database).group(0)
            self.assertIn('--replace', rebuild)
            run = re.search(r'(?ms)^board-run:.*?(?=^[^\s#]|\Z)', database).group(0)
            self.assertNotIn('--replace', run)
            self.assertNotIn('board-image', run)
            self.assertRegex(database, r'(?m)^run: *$')
            expected_rebuild = 'board-rebuild' if arch == 'aarch64' else 'all'
            self.assertRegex(database, rf'(?m)^rebuild: {expected_rebuild}$')
            self.assertIn('$(BOARD_IMAGE)', run)

    def test_run_has_no_build_commands(self):
        for arch, profile in [('aarch64', 'board'), ('aarch64', 'native'),
                              ('aarch64', 'io'), ('riscv64', 'native')]:
            result = subprocess.run(
                ['make', '-n', 'run', f'ARCH={arch}', f'RUN_PROFILE={profile}'],
                cwd=ROOT, check=True, capture_output=True, text=True)
            for build_command in ('cargo build', 'cargo run', 'build-sysroot.sh',
                                  'pack-native-initramfs.py', 'pack-native-system.py',
                                  'fetch-io-vm.py', 'prepare_disk'):
                self.assertNotIn(build_command, result.stdout)
            for line in result.stdout.splitlines():
                if 'pack-board-image.py' in line:
                    self.assertIn('--check-system-payload', result.stdout)

    def test_board_build_keeps_existing_disk_and_run_does_not_create_missing_disk(self):
        with tempfile.TemporaryDirectory(prefix='hyper run policy ') as directory:
            output = Path(directory)
            disk = output / 'disk.img'
            disk.write_bytes(b'persistent disk contents')
            result = subprocess.run(
                ['make', '-o', 'image', '-o', 'board-initramfs', '-o', 'board-update-system',
                 'board-build', 'MAKE=true', f'BOARD_OUTPUT={output}'],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(disk.read_bytes(), b'persistent disk contents')
            disk.unlink()
            result = subprocess.run(
                ['make', 'run', f'BOARD_OUTPUT={output}', 'QEMU=false'],
                cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('run make first', result.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_default_composition_rejects_legacy_embedded_system_selection(self):
        output = subprocess.check_output([
            'make', '--no-print-directory', '-n', '-o', 'board-deployment',
            '-o', 'app', '-o', 'app-boot', '-o', 'fit-pack', '-o', 'sdk',
            'board-initramfs', 'IO_VM_PACKAGE=/test/io-package'], cwd=ROOT, text=True)
        self.assertIn('scripts/pack-native-system.py', output)
        self.assertNotIn('native-initramfs NATIVE_INITRAMFS=', output)
        for layout in ('ramfs', 'split', 'flat'):
            result = subprocess.run(['make', '-n', 'run', f'BOARD_SYSTEM_LAYOUT={layout}'],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('system requires independent storage', result.stderr)

    def test_native_fixtures_build_and_package_separate_boot_artifacts(self):
        for target in ('native-initramfs', 'test-storage-failure'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                apps = str(Path(directory) / 'apps')
                boot = str(Path(directory) / 'bootstrap')
                output = subprocess.check_output([
                    'make', '--no-print-directory', '-n', '-o', 'image',
                    '-o', 'app-fetch', '-o', 'native-fixture-init',
                    '-o', 'app-fixtures', '-o', 'guest-itb', target,
                    f'APP_OUTPUT={apps}', f'APP_BOOT_OUTPUT={boot}'], cwd=ROOT, text=True)
                commands = [shlex.split(line) for line in
                            output.replace('\\\n', '').splitlines()]
                install = next(command for command in commands
                               if 'scripts/app-deployment.py' in command
                               and 'install' in command
                               and boot in command)
                self.assertEqual(install[install.index('--output') + 1], boot)
                self.assertIn('io-runtime', install)
                pack = next(command for command in commands
                            if ('scripts/pack-native-system.py' in command or
                                'tests/qemu/verify-storage-failure.py' in command)
                            and '--apps' in command)
                self.assertEqual(pack[pack.index('--apps') + 1], apps)
                self.assertEqual(pack[pack.index('--boot-apps') + 1], boot)

    def test_pi_uses_custom_board_and_extra_volume_artifacts(self):
        for override, expected in (
                ([], str(ROOT / 'boards/rpi5.json')),
                (['BOARD_CONFIG=/tmp/custom pi.json'], '/tmp/custom pi.json'),
                (['BOARD_CONFIG=/tmp/custom pi.json',
                  'RPI5_BOARD_CONFIG=/tmp/other pi.json'], '/tmp/other pi.json')):
            with self.subTest(override=override):
                output = subprocess.check_output([
                    'make', '--no-print-directory', '-n', '-o', 'image', 'rpi5-sd',
                    'MAKE=true', 'BOARD_ARTIFACTS=--artifact extra=/tmp/extra.img', *override],
                    cwd=ROOT, text=True)
                commands = [shlex.split(line) for line in
                            output.replace('\\\n', '').splitlines()]
                build = next(command for command in commands
                             if command[:2] == ['true', 'board-initramfs'])
                self.assertIn(f'BOARD_CONFIG={expected}', build)
                pack = next(command for command in commands
                            if 'scripts/rpi5-bringup.py' in command)
                self.assertIn('extra=/tmp/extra.img', pack)
                self.assertIn('--system-root', pack)
                self.assertIn('--mkfs-erofs', pack)

    def test_board_image_always_supplies_independent_system_payload(self):
        output = subprocess.check_output([
            'make', '--no-print-directory', '-n', '-o', 'image',
            '-o', 'board-initramfs', '-o', 'board-guest-images', 'board-image'],
            cwd=ROOT, text=True)
        command = shlex.split(output.replace('\\\n', ''))
        self.assertIn('--system-root', command)
        self.assertIn('--mkfs-erofs', command)

    def test_pi_diskless_bringup_builds_only_static_recovery(self):
        output = subprocess.check_output([
            'make', '--no-print-directory', '-n', '-o', 'image',
            '-o', 'rpi5-bringup-deployment', 'rpi5-bringup', 'MAKE=true'],
            cwd=ROOT, text=True)
        commands = [shlex.split(line) for line in output.replace('\\\n', '').splitlines()]
        build = next(command for command in commands
                     if command[:2] == ['true', 'recovery-initramfs'])
        self.assertIn('APP_BOOT_COMPONENTS=init console-raw fallback-sh', build)
        self.assertNotIn('native-initramfs', output)
        pack = next(command for command in commands if 'scripts/rpi5-bringup.py' in command)
        self.assertIn('--recovery', pack)
        self.assertNotIn('--system-root', pack)

    def test_editor_and_build_keep_lockfiles_stable(self):
        # Exercise Cargo resolution, not just configuration spelling. No network,
        # installed SDK or Native compiler artifacts are needed for this fixture.
        with tempfile.TemporaryDirectory(prefix='hyper lockfiles ') as directory:
            root = Path(directory).resolve()
            shutil.copyfile(ROOT / 'rust-toolchain.toml', root / 'rust-toolchain.toml')
            shared = root / '.vscode/rust-analyzer.toml'
            shared.parent.mkdir()
            shutil.copyfile(ROOT / '.vscode/rust-analyzer.toml', shared)
            workspaces = ('tools/fit-pack', 'app', 'bootstrap/stage1', 'tests/native/io-smoke', 'sdk/toolchain/tests/std-smoke',
                          'sdk/toolchain/tests/rust-smoke')
            patches = {
                workspace: tuple(name.removeprefix('hyper-') for name in
                                 tomllib.loads((ROOT / workspace / '.cargo/config.toml')
                                               .read_text())['patch']['crates-io'])
                for workspace in workspaces if workspace != 'tools/fit-pack'
            }
            crates = sorted({name for names in patches.values() for name in names})
            for name in crates:
                paths = [root / ('sdk/abi' if name == 'abi' else f'stdlib/rust/hyper-{name}'),
                         root / f'installed/hyper-{name}']
                for path in paths:
                    path.mkdir(parents=True)
                    (path / 'Cargo.toml').write_text(
                        f'[package]\nname="hyper-{name}"\nversion="0.0.0"\n'
                        '[lib]\npath="lib.rs"\n')
                    (path / 'lib.rs').write_text('')
            for workspace in workspaces:
                cwd = root / workspace
                cwd.mkdir(parents=True)
                native = workspace != 'tools/fit-pack'
                # Every Native workspace must resolve the complete SDK set.
                # Using each workspace's own subset hid missing editor patches
                # when hyper-cargo supplied newly installed crates at check time.
                deps = ''.join(f'hyper-{name}="=0.0.0"\n' for name in crates) if native else (
                    'hyper-abi={path="../../sdk/abi"}\n')
                (cwd / 'Cargo.toml').write_text(
                    '[package]\nname="probe"\nversion="0.0.0"\n'
                    '[workspace]\n[lib]\npath="lib.rs"\n[dependencies]\n' + deps)
                (cwd / 'lib.rs').write_text('')
                if native:
                    (cwd / '.cargo').mkdir()
                    shutil.copyfile(ROOT / workspace / '.cargo/config.toml',
                                    cwd / '.cargo/config.toml')
                command = ['cargo', 'metadata', '--offline', '--format-version=1',
                           '--config', 'build.target="aarch64-unknown-none"']
                result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                lock = (cwd / 'Cargo.lock').read_bytes()
                for mode in ('editor', 'installed', 'editor'):
                    args = command + ['--locked', '--config', str(shared)]
                    if native and mode == 'installed':
                        for name in crates:
                            args += ['--config', f'patch.crates-io.hyper-{name}.path="'
                                     f'{root}/installed/hyper-{name}"']
                    result = subprocess.run(args, cwd=cwd, check=True,
                                            capture_output=True, text=True)
                    self.assertEqual((cwd / 'Cargo.lock').read_bytes(), lock)
                    if native:
                        crate = next(p for p in json.loads(result.stdout)['packages']
                                     if p['name'] == 'hyper-os')
                        expected = root / ('installed/hyper-os' if mode == 'installed'
                                           else 'stdlib/rust/hyper-os') / 'Cargo.toml'
                        self.assertEqual(Path(crate['manifest_path']), expected)

    def test_analyzer_patches_only_native_workspaces(self):
        with tempfile.TemporaryDirectory(prefix='hyper editor ') as directory:
            root = Path(directory).resolve()
            shutil.copyfile(ROOT / 'rust-toolchain.toml', root / 'rust-toolchain.toml')
            (root / 'scripts').mkdir()
            script = root / 'scripts/rust-analyzer-check.sh'
            shutil.copyfile(ROOT / 'scripts/rust-analyzer-check.sh', script)
            fake = root / 'bin'
            fake.mkdir()
            recorder = ('#!' + sys.executable + '\nimport os,json,sys\n'
                        'if sys.argv[1:2] == ["locate-project"]:\n'
                        f' os.execv({shutil.which("cargo")!r}, '
                        f'[{shutil.which("cargo")!r}, *sys.argv[1:]])\n'
                        'with open(os.environ["PROBE"],"w") as f: '
                        'json.dump({"argv":sys.argv[1:],"config":os.getenv("HYPER_CONFIG"),'
                        '"std":os.getenv("HYPER_RUST_STD"),"link":os.getenv("HYPER_LINK_MODE"),"cwd":os.getcwd()},f)\n')
            (fake / 'cargo').write_text(recorder)
            (fake / 'cargo').chmod(0o755)
            native = root / 'target/sdk/aarch64/bin'
            native.mkdir(parents=True)
            (native / 'hyper-cargo').write_text(recorder)
            (native / 'hyper-cargo').chmod(0o755)
            output = root / 'probe.json'
            env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ['PATH'], PROBE=str(output))
            env.pop('HYPER_CONFIG', None)
            env['HYPER_LINK_MODE'] = 'static'
            workspaces = ('kernel', 'tools/fit-pack', 'app', 'bootstrap/stage1', 'tests/native/io-smoke',
                          'sdk/toolchain/tests/std-smoke', 'sdk/toolchain/tests/rust-smoke')
            for workspace in workspaces:
                cwd = root / workspace
                cwd.mkdir(parents=True)
                (cwd / 'Cargo.toml').write_text('[workspace]\n[package]\n'
                                               'name="probe"\nversion="0.0.0"\n')
                (cwd / 'src').mkdir()
                (cwd / 'src/lib.rs').write_text('')
            (root / 'app/Cargo.toml').write_text(
                '[workspace]\nmembers=["cmd", "../svc/*", "../tests/app/*", "../lib/args", "../lib/args/shared"]\n')
            for member, owner in [('app/cmd', '..'), ('svc/system-startup', '../../app'),
                                  ('svc/io-runtime', '../../app'), ('svc/fs-backend', '../../app'),
                                  ('tests/app/probe', '../../../app'), ('lib/args', '../../app'),
                                  ('lib/args/shared', '../../../app'),
                                  ('bootstrap/stage1/init', '..')]:
                cwd = root / member
                (cwd / 'src').mkdir(parents=True)
                (cwd / 'src/lib.rs').write_text('')
                (cwd / 'Cargo.toml').write_text(
                    f'[package]\nname="{member.replace("/", "-")}"\nversion="0.0.0"\n'
                    f'workspace="{owner}"\n')
            (root / 'bootstrap/stage1/Cargo.toml').write_text('[workspace]\nmembers=["init"]\n')
            for relative in ('bootstrap/stage1/contracts',
                             'bootstrap/stage1/init/src/manifest'):
                (root / relative).mkdir(parents=True)
            for workspace in (*workspaces, '.', 'app/cmd/src', 'lib/args', 'lib/args/shared/src',
                              'bootstrap/stage1/init/src', 'bootstrap/stage1/contracts',
                              'bootstrap/stage1/init/src/manifest', 'tests/app/probe/src',
                              'svc/system-startup/src', 'svc/io-runtime/src', 'svc/fs-backend/src'):
                cwd = root / workspace
                subprocess.run(['sh', str(script)], cwd=cwd, env=env, check=True)
                result = json.loads(output.read_text())
                owner = (cwd if workspace in workspaces else root / (
                    'bootstrap/stage1' if workspace.startswith('bootstrap/') else 'app'))
                self.assertEqual(Path(result['cwd']), owner)
                self.assertIn('--locked', result['argv'])
                if workspace.startswith('bootstrap/') or workspace == 'tests/native/io-smoke':
                    self.assertEqual(result['link'], 'static')
                if workspace in ('kernel', 'tools/fit-pack'):
                    self.assertNotIn('--config', result['argv'])
                    self.assertEqual(result['config'], str(root / 'kernel/configs/qemu_aarch64_defconfig'))
                else:
                    index = result['argv'].index('--config')
                    self.assertEqual(result['argv'][index + 1], str(owner / '.cargo/config.toml'))
                    self.assertEqual(result['std'], '0' if workspace.endswith('rust-smoke') else '1')
                    if not workspace.startswith('bootstrap/') and workspace != 'tests/native/io-smoke':
                        self.assertEqual(result['link'], 'dynamic')
            env['HYPER_CONFIG'] = '/chosen/board.config'
            subprocess.run(['sh', str(script)], cwd=root / 'kernel', env=env, check=True)
            self.assertEqual(json.loads(output.read_text())['config'], '/chosen/board.config')


if __name__ == '__main__':
    unittest.main()
