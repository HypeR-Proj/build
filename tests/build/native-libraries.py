#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise Native ELF dependency validation with linked-image-shaped fixtures."""

from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from native_libraries import add_dependencies, read_image, resolve, validate


def elf(*, needed=(), soname=None, exports=(), imports=(), weak=(), machine=183, interpreter=None):
    strings = bytearray(b'\0')

    def name(value):
        index = len(strings)
        strings.extend(value.encode() + b'\0')
        return index

    tags = [(1, name(value)) for value in needed]
    if soname:
        tags.append((14, name(soname)))
    symbols = bytearray(24)
    for names, binding, section in ((exports, 1, 1), (imports, 1, 0), (weak, 2, 0)):
        for value in names:
            symbols.extend(struct.pack('<IBBHQQ', name(value), binding << 4, 0, section, 0, 0))
    data = bytearray(4096)
    data[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', data, 16, 3, machine, 1, 0, 64, 0, 0,
                     64, 56, 3 if interpreter else 2, 0, 0, 0)
    if interpreter:
        payload = interpreter.encode() + b'\0'
        struct.pack_into('<IIQQQQQQ', data, 176, 3, 4, 3072, 0, 0, len(payload), len(payload), 1)
        data[3072:3072 + len(payload)] = payload
    tags += [(4, 512), (5, 2048), (6, 768), (10, len(strings)), (11, 24), (0, 0)]
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 4, 0, 0, 0, len(data), len(data), 4096)
    struct.pack_into('<IIQQQQQQ', data, 120, 2, 4, 256, 256, 0,
                     len(tags) * 16, len(tags) * 16, 8)
    for index, tag in enumerate(tags):
        struct.pack_into('<qQ', data, 256 + index * 16, *tag)
    struct.pack_into('<II', data, 512, 1, len(symbols) // 24)
    data[768:768 + len(symbols)] = symbols
    data[2048:2048 + len(strings)] = strings
    return data


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.entries = ['symlink', 'lib', 'lib64']

    def add(self, destination, **kwargs):
        path = self.root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(elf(**kwargs))
        self.entries.extend(['0755', destination, str(path)])
        return path

    def test_transitive_symbols_and_multiple_shared_libraries(self):
        self.add('bin/tool', needed=['libargs.so', 'libpolicy.so'], imports=['parse', 'policy'])
        self.add('lib64/aarch64-hyper-hyper/libargs.so', soname='libargs.so', needed=['libstd.so'],
                 exports=['parse'], imports=['allocate'], weak=['optional'])
        self.add('lib64/aarch64-hyper-hyper/libpolicy.so', soname='libpolicy.so', needed=['libstd.so'], exports=['policy'])
        self.add('lib64/aarch64-hyper-hyper/libstd.so', soname='libstd.so', exports=['allocate'])
        validate(self.entries)

    def test_collects_only_transitive_dependencies_and_interpreter(self):
        self.add('bin/tool', needed=['libargs.so'], imports=['parse'],
                 interpreter='/lib64/ld-hyper-aarch64.so')
        providers = self.root / 'providers'
        providers.mkdir()
        for name, data in {
            'ld-hyper-aarch64.so': elf(),
            'libargs.so': elf(soname='libargs.so', exports=['parse'], needed=['libstd.so']),
            'libstd.so': elf(soname='libstd.so'),
            'unused.so': elf(soname='unused.so'),
        }.items():
            (providers / name).write_bytes(data)
        result = add_dependencies(self.entries, [providers])
        validate(result)
        self.assertEqual(set(result[1::3]), {'lib', 'bin/tool', 'lib64/ld-hyper-aarch64.so',
                         'lib64/aarch64-hyper-hyper/libargs.so',
                         'lib64/aarch64-hyper-hyper/libstd.so'})
        self.assertEqual(add_dependencies(result, [providers]), result)

    def test_collect_rejects_missing_and_ambiguous_providers(self):
        self.add('bin/tool', needed=['libargs.so'])
        with self.assertRaisesRegex(ValueError, 'found 0'):
            add_dependencies(self.entries, [])
        providers = [self.root / 'a', self.root / 'b']
        for path in providers:
            path.mkdir()
            (path / 'libargs.so').write_bytes(elf(soname='libargs.so'))
        with self.assertRaisesRegex(ValueError, 'found 2'):
            add_dependencies(self.entries, providers)

    def test_collected_providers_still_require_matching_abi(self):
        self.add('bin/tool', needed=['libargs.so'], imports=['new_symbol'])
        providers = self.root / 'providers'
        providers.mkdir()
        (providers / 'libargs.so').write_bytes(elf(soname='libargs.so', exports=['old_symbol']))
        with self.assertRaisesRegex(ValueError, 'new_symbol'):
            validate(add_dependencies(self.entries, [providers]))

    def test_missing_library_is_rejected(self):
        self.add('bin/tool', needed=['libargs.so'])
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency lib64/aarch64-hyper-hyper/libargs.so'):
            validate(self.entries)

    def test_wrong_library_build_is_rejected(self):
        self.add('bin/tool', needed=['libargs.so'], imports=['parse_build_a'])
        self.add('lib64/aarch64-hyper-hyper/libargs.so', soname='libargs.so', exports=['parse_build_b'])
        with self.assertRaisesRegex(ValueError, 'unresolved dynamic symbol parse_build_a'):
            validate(self.entries)

    def test_soname_and_architecture_are_checked(self):
        self.add('bin/tool', needed=['libargs.so'])
        library = self.add('lib64/aarch64-hyper-hyper/libargs.so', soname='libother.so')
        with self.assertRaisesRegex(ValueError, 'SONAME mismatch'):
            validate(self.entries)
        library.write_bytes(elf(soname='libargs.so', machine=243))
        with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
            validate(self.entries)

    def test_dependency_cycles_are_bounded(self):
        self.add('lib64/aarch64-hyper-hyper/liba.so', soname='liba.so', needed=['libb.so'], exports=['a'], imports=['b'])
        self.add('lib64/aarch64-hyper-hyper/libb.so', soname='libb.so', needed=['liba.so'], exports=['b'], imports=['a'])
        validate(self.entries)

    def test_lib64_interpreters_and_architecture_libraries(self):
        for arch, machine in [('aarch64', 183), ('riscv64', 243)]:
            self.add(f'bin/{arch}', machine=machine, needed=['libruntime.so'],
                     interpreter=f'/lib64/ld-hyper-{arch}.so')
            self.add(f'lib64/ld-hyper-{arch}.so', machine=machine)
            self.add(f'lib64/{arch}-hyper-hyper/libruntime.so',
                     machine=machine, soname='libruntime.so')
        validate(self.entries)
        self.entries = self.entries[3:]
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency lib/'):
            validate(self.entries)

    def test_interpreter_is_opened_at_its_absolute_path_not_the_library_directory(self):
        self.add('bin/tool', interpreter='/lib64/ld-hyper-aarch64.so')
        self.add('lib64/aarch64-hyper-hyper/ld-hyper-aarch64.so')
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency lib64/ld-hyper-'):
            validate(self.entries)

    def test_no_fallback_to_flat_or_other_architecture_directories(self):
        self.add('bin/tool', needed=['libargs.so'])
        self.add('lib64/libargs.so', soname='libargs.so')
        self.add('lib64/riscv64-hyper-hyper/libargs.so', soname='libargs.so', machine=243)
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency'):
            validate(self.entries)

    def test_symlink_cycle_is_rejected(self):
        self.entries[2] = 'lib'
        self.add('bin/tool', needed=['libargs.so'])
        with self.assertRaisesRegex(ValueError, 'symlink loop'):
            validate(self.entries)

    def test_absolute_links_restart_at_the_archive_root(self):
        links = {'svc': 'boot/svc', 'boot/svc/vm-manager': '/system/svc/vm-manager',
                 'lib': 'lib64', 'lib64': 'boot/lib64'}
        self.assertEqual(resolve('svc/vm-manager', links), 'system/svc/vm-manager')
        self.assertEqual(resolve('lib/runtime.so', links), 'boot/lib64/runtime.so')

    def test_ramfs_system_libraries_and_explicit_system_loader(self):
        self.entries.extend(['symlink', 'lib64', 'boot/lib64',
                             'symlink', 'system/lib', 'lib64'])
        self.add('boot/init')
        self.add('system/lib64/ld-hyper-aarch64.so')
        self.add('system/bin/tool', needed=['libargs.so'], imports=['parse'],
                 interpreter='/lib64/ld-hyper-aarch64.so')
        self.add('system/lib64/aarch64-hyper-hyper/libargs.so', soname='libargs.so',
                 exports=['parse'], needed=['libstd.so'], imports=['allocate'])
        library = self.add('system/lib64/aarch64-hyper-hyper/libstd.so',
                           soname='libstd.so', exports=['allocate'])
        validate(self.entries, ['system'])
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency boot/lib64/'):
            validate(self.entries)
        library.write_bytes(elf(soname='libstd.so', exports=['wrong_build']))
        with self.assertRaisesRegex(ValueError, 'unresolved dynamic symbol allocate'):
            validate(self.entries, ['system'])

    def test_system_loader_cannot_fall_back_to_boot_loader(self):
        self.add('system/bin/tool', interpreter='/lib64/ld-hyper-aarch64.so')
        self.add('lib64/ld-hyper-aarch64.so')
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency system/lib64/ld-'):
            validate(self.entries, ['system'])

    def test_library_namespace_does_not_fallback_to_boot_libraries(self):
        self.add('system/bin/tool', needed=['libargs.so'])
        self.add('lib64/aarch64-hyper-hyper/libargs.so', soname='libargs.so')
        with self.assertRaisesRegex(ValueError, 'missing packaged ELF dependency system/lib/'):
            validate(self.entries, ['system'])
        for namespace in ('/system', '../system', 'system/', 'system//lib'):
            with self.subTest(namespace=namespace), self.assertRaises(ValueError):
                validate(self.entries, [namespace])

    def test_namespace_boundaries_and_nearest_scope_keep_same_named_libraries_separate(self):
        self.entries.extend(['symlink', 'system/lib', 'lib64',
                             'symlink', 'system/vendor/lib', 'lib64'])
        for namespace, symbol in (('', 'root_api'), ('system/', 'system_api'),
                                  ('system/vendor/', 'vendor_api')):
            self.add(namespace + 'bin/tool', needed=['libshared.so'], imports=[symbol])
            self.add(namespace + 'lib64/aarch64-hyper-hyper/libshared.so',
                     soname='libshared.so', exports=[symbol])
        # A matching textual prefix without the path separator is outside the
        # scope. Its interpreter and dependencies still use the root policy.
        self.add('system-extra/bin/tool', needed=['libshared.so'], imports=['root_api'])
        validate(self.entries, ['system/vendor', 'system'])
        validate(self.entries, ['system', 'system/vendor'])
        with self.assertRaisesRegex(ValueError, 'unresolved dynamic symbol vendor_api'):
            validate(self.entries, ['system'])

    def test_path_traversal_and_truncated_metadata_are_rejected(self):
        path = self.add('bin/tool', needed=['../libargs.so'])
        with self.assertRaisesRegex(ValueError, 'invalid Native library name'):
            validate(self.entries)
        for data in (b'\x7fELF', elf(needed=['libargs.so'])[:512]):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                read_image(path)
        path.write_text('ordinary configuration')
        self.assertIsNone(read_image(path))


if __name__ == '__main__':
    unittest.main()
