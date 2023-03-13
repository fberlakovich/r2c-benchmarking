#  Copyright 2023 Felix Berlakovich
#  Copyright 2018 Taddeus Kroes
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import os
import shutil
from typing import List, Iterable
from ...package import Package
from ...util import Namespace, run, apply_patch, download, param_attrs, FatalError, require_program
from ..gnu import Bash, CoreUtils, BinUtils, Make, AutoMake
from ..cmake import CMake
from ..ninja import Ninja


class LLVM(Package):
    """
    LLVM dependency package. Includes the Clang compiler and optionally
    `compiler-rt <https://compiler-rt.llvm.org>`_ (which contains runtime
    support for ASan).

    Supports a number of patches to be passed as arguments, which are
    :func:`applied <util.apply_patch>` (with ``patch -p1``) before building. A
    patch in the list can either be a full path to a patch file, or the name of
    a built-in patch. Available built-in patches are:

    - **gold-plugins** (for 3.8.0/3.9.1/4.0.0/5.0.0/7.0.0): adds a ``-load``
      option to load passes from a shared object file during link-time
      optimizations, best used in combination with :class:`LLVMPasses`

    - **statsfilter** (for 3.8.0/3.9.1/5.0.0/7.0.0): adds ``-stats-only``
      option, which relates to ``-stats`` like ``-debug-only`` relates to
      ``-debug``

    - **lto-nodiscard-value-names** (for 7.0.0): preserves value names when
      producing bitcode for LTO, this is very useful when debugging passes

    - **safestack** (for 3.8.0): adds ``-fsanitize=safestack`` for old LLVM

    - **compiler-rt-typefix** (for 4.0.0): fixes a compiler-rt-4.0.0 bug to make
      it compile for recent glibc, is applied automatically if ``compiler_rt``
      is set

    :identifier: llvm-<version>
    :param version: the full LLVM version to download, like X.Y.Z
    :param compiler_rt: whether to enable compiler-rt
    :param patches: optional patches to apply before building
    :param build_flags: additional `build flags
                        <https://www.llvm.org/docs/CMake.html#options-and-variables>`_
                        to pass to cmake
    """

    #supported_versions = ('3.8.0', '3.9.1', '4.0.0', '5.0.0')
    binutils = BinUtils('2.38', gold=True)

    def __init__(self, version: str,
                       compiler_rt: bool,
                       lld: bool = False,
                       patches: List[str] = [],
                       build_flags: List[str] = []):
        #if version not in self.supported_versions:
        #    raise FatalError('LLVM version must be one of %s' %
        #            '/'.join(self.supported_versions))

        self.version = version
        self.compiler_rt = compiler_rt
        self.lld = lld
        self.patches = patches
        self.build_flags = build_flags

        if compiler_rt and version == '4.0.0':
            patches.append('compiler-rt-typefix')

    def ident(self):
        suffix = '-lld' if self.lld else ''
        return 'llvm-' + self.version + suffix

    def dependencies(self):
        # TODO: prune these
        yield Bash('4.3')
        yield CoreUtils('9.0')
        yield self.binutils
        yield Make('4.3')
        yield AutoMake.default()
        yield CMake('3.14.0')
        yield Ninja('1.8.2')

    def fetch(self, ctx):
        def get(repo, clonedir):
            basedir = os.path.dirname(clonedir)
            if basedir:
                os.makedirs(basedir, exist_ok=True)

            #url = 'http://llvm.org/svn/llvm-project/%s/trunk' % repo
            #run(ctx, ['svn', 'co', '-r' + ctx.params.commit, url, clonedir])

            dirname = '%s-%s.src' % (repo, self.version)
            tarname = dirname + '.tar.xz'
            major_version = int(self.version.split('.')[0])

            if major_version >= 8:
                # use github now
                url_prefix = 'https://github.com/llvm/llvm-project/releases/download'
                download(ctx, '%s/llvmorg-%s/%s' % (url_prefix, self.version, tarname))
            else:
                download(ctx, 'https://releases.llvm.org/%s/%s' % (self.version, tarname))

            run(ctx, ['tar', '-xf', tarname])
            shutil.move(dirname, clonedir)
            os.remove(tarname)

        # download and unpack sources
        get('llvm', 'src')

        major_version = int(self.version.split('.')[0])
        if major_version >= 8:
            get('clang', 'src/tools/clang')
        else:
            get('cfe', 'src/tools/clang')

        if self.compiler_rt:
            get('compiler-rt', 'src/projects/compiler-rt')
        if self.lld:
            get('lld', 'src/projects/lld')

    def build(self, ctx):
        # TODO: verify that any applied patches are in self.patches, error
        # otherwise

        # apply patches from the directory this file is in
        # do this in build() instead of fetch() to make sure patches are applied
        # with --force-rebuild
        os.chdir('src')
        config_path = os.path.dirname(os.path.abspath(__file__))
        for path in self.patches:
            if '/' not in path:
                path = '%s/%s-%s.patch' % (config_path, path, self.version)
            apply_patch(ctx, path, 1)
        os.chdir('..')

        os.makedirs('obj', exist_ok=True)
        os.chdir('obj')
        run(ctx, [
            'cmake',
            '-G', 'Ninja',
            '-DCMAKE_INSTALL_PREFIX=' + self.path(ctx, 'install'),
            '-DLLVM_BINUTILS_INCDIR=' + self.binutils.path(ctx, 'install/include'),
            '-DCMAKE_BUILD_TYPE=Release',
            '-DLLVM_ENABLE_ASSERTIONS=On',
            '-DLLVM_OPTIMIZED_TABLEGEN=On',
            '-DCMAKE_C_COMPILER=gcc',
            '-DCMAKE_CXX_COMPILER=g++', # must be the same as used for compiling passes
            *self.build_flags,
            '../src'
        ])
        run(ctx, 'cmake --build . -- -j %d' % ctx.jobs)

    def install(self, ctx):
        os.chdir('obj')
        run(ctx, 'cmake --build . --target install')

    def is_fetched(self, ctx):
        return os.path.exists('src')

    def is_built(self, ctx):
        return os.path.exists('obj/bin/llvm-config')

    def is_installed(self, ctx):
        if not self.patches:
            # allow preinstalled LLVM if version matches
            # TODO: do fuzzy matching on version?
            proc = run(ctx, 'llvm-config --version', allow_error=True)
            if proc and proc.returncode == 0:
                installed_version = proc.stdout.strip()
                if installed_version == self.version:
                    return True
                else:
                    ctx.log.debug('installed llvm-config version %s is '
                                  'different from required %s' %
                                  (installed_version, self.version))

        return os.path.exists('install/bin/llvm-config')

    def configure(self, ctx: Namespace):
        """
        Set LLVM toolchain programs in **ctx**. Should be called from the
        ``configure`` method of an instance.

        :param ctx: the configuration context
        """
        ctx.cc = 'clang'
        ctx.cxx = 'clang++'
        ctx.ar = 'llvm-ar'
        ctx.nm = 'llvm-nm'
        ctx.ranlib = 'llvm-ranlib'
        ctx.cflags = []
        ctx.cxxflags = []
        ctx.ldflags = []

    @staticmethod
    def add_plugin_flags(ctx: Namespace, *flags: Iterable[str], gold_passes: bool = True):
        """
        Helper to pass link-time flags to the LLVM gold plugin. Prefixes all
        **flags** with ``-Wl,-plugin-opt=`` before adding them to
        ``ctx.ldflags``.

        :param ctx: the configuration context
        :param flags: flags to pass to the gold plugin
        """
        for flag in flags:
            if gold_passes:
                ctx.ldflags.append('-Wl,-plugin-opt=' + str(flag))
            else:
                ctx.ldflags.append('-Wl,-mllvm=' + str(flag))


class LLVMBinDist(Package):
    """
    LLVM + Clang binary distribution package.

    Fetches and extracts a tarfile from http://releases.llvm.org.

    :identifier: llvm-<version>
    :param version: the full LLVM version to download, like X.Y.Z
    :param target: target machine in tarfile name, e.g., "x86_64-linux-gnu-ubuntu-16.10"
    :param suffix: if nonempty, create {clang,clang++,opt,llvm-config}<suffix> binaries
    """
    @param_attrs
    def __init__(self, version, target, bin_suffix=''):
        pass

    def ident(self):
        return 'llvmbin-' + self.version

    def is_fetched(self, ctx):
        return os.path.exists('src')

    def fetch(self, ctx):
        ident = 'clang+llvm-%s-%s' % (self.version, self.target)
        tarname = ident + '.tar.xz'
        download(ctx, 'http://releases.llvm.org/%s/%s' % (self.version, tarname))
        run(ctx, ['tar', '-xf', tarname])
        shutil.move(ident, 'src')
        os.remove(tarname)

    def is_built(self, ctx):
        return True

    def build(self, ctx):
        pass

    def is_installed(self, ctx):
        return os.path.exists('install')

    def install(self, ctx):
        shutil.move('src', 'install')
        os.chdir('install/bin')

        if self.bin_suffix:
            for src in ('clang', 'clang++', 'opt', 'llvm-config'):
                tgt = src + self.bin_suffix
                if os.path.exists(src) and not os.path.exists(tgt):
                    ctx.log.debug('creating symlink %s -> %s' % (tgt, src))
                    os.symlink(src, tgt)


class LLVMSource(LLVM):
    def __init__(self, source_type: str, source: str, version: str, build_flags: List[str] = []):
        super().__init__(version, False, build_flags=build_flags)

        if source_type not in ('directory', 'git'):
            raise FatalError('invalid source type "%s"' % source_type)

        self.source_type = source_type
        self.source = source

    def ident(self):
        return 'llvm-src-' + self.version

    def build(self, ctx):
        os.makedirs('obj', exist_ok=True)
        os.chdir('obj')
        run(ctx, [
            'cmake',
            '-G', 'Ninja',
            '-DCMAKE_INSTALL_PREFIX=' + self.path(ctx, 'install'),
            '-DLLVM_BINUTILS_INCDIR=' + self.binutils.path(ctx, 'install/include'),
            '-DCMAKE_BUILD_TYPE=Release',
            '-DLLVM_ENABLE_ASSERTIONS=On',
            '-DLLVM_OPTIMIZED_TABLEGEN=On',
            *self.build_flags,
            '../src/llvm'
        ])
        run(ctx, 'cmake --build . -- -j %d' % ctx.jobs)

    def _parse_git_url(self, source):
        url = source
        parts = source.split(':')
        rev = None
        if len(parts) > 2:
            rev = parts[2]
            url = source.replace(':' + rev, '')
        return [url, rev]

    def fetch(self, ctx):
        if self.source_type == 'directory':
            os.symlink(self.source, "src")
        elif self.source_type == 'git':
            url, rev = self._parse_git_url(self.source)
            require_program(ctx, 'git')
            ctx.log.debug('cloning LLVM repo')
            options = ['--depth', 1]
            if rev is not None:
                options += ['--branch', rev]
            run(ctx, ['git', 'clone'] + options + [url, 'src'])

    def is_installed(self, ctx):
        return os.path.exists('install/bin/llvm-config')
