# Software License Agreement (BSD License)
#
# Copyright (c) 2012, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import print_function

import os
import sys

from subprocess import check_output, CalledProcessError, check_call

from bloom.util import track_all_git_branches, warning
from bloom.util import bailout, execute_command, ansi, parse_stack_xml
from bloom.util import assert_is_not_gbp_repo, create_temporary_directory
from bloom.util import get_last_git_tag, get_current_git_branch, error
from bloom.util import get_versions_from_upstream_tag, segment_version

from distutils.version import StrictVersion

try:
    from vcstools import VcsClient
except ImportError:
    print("vcstools was not detected, please install it.", file=sys.stderr)
    sys.exit(1)


def usage():
    print("""\
usage: git bloom-import-upstream

Must be in a valid git bloom release repository.
""")


def convert_catkin_to_bloom(cwd=None):
    """
    Converts an old style catkin branch/catkin.conf setup to bloom.
    """
    # Rename the branch to bloom from catkin
    execute_command('git branch -m catkin bloom', cwd=cwd)
    # Change to the bloom branch
    execute_command('git checkout bloom', cwd=cwd)
    # Rename the config cwd
    if os.path.exists(os.path.join(cwd, 'catkin.conf')):
        execute_command('git mv catkin.conf bloom.conf', cwd=cwd)
    # Replace the `[catkin]` entry in the config file with `[bloom]`
    bloom_path = os.path.join(cwd, 'bloom.conf')
    if os.path.exists(bloom_path):
        conf_file = open(bloom_path, 'r').read()
        conf_file = conf_file.replace('[catkin]', '[bloom]')
        open(bloom_path, 'w+').write(conf_file)
        # Stage the config file changes
        execute_command('git add bloom.conf', cwd=cwd)
        # Commit the change
        cmd = 'git commit -m "rename catkin.conf to bloom.conf"'
        execute_command(cmd, cwd=cwd)


def not_a_bloom_release_repo():
    bailout('This does not appear to be a bloom release repo. ' \
            'Please initialize it first using: git ' \
            'bloom-set-upstream <UPSTREAM_VCS_URL> <VCS_TYPE>')


def check_for_bloom(cwd=None):
    """
    Checks for the bloom branch, else looks for and converts the catkin branch
    """
    cmd = 'git branch'
    if check_output(cmd, shell=True, cwd=cwd).count('bloom') == 0:
        # There is not bloom branch, check for the legacy catkin branch
        if check_output(cmd, shell=True, cwd=cwd).count('catkin') == 0:
            # Neither was found
            not_a_bloom_release_repo()
        else:
            # Found catkin branch, migrate it to bloom
            print('catkin branch detected, up converting to the bloom branch')
            convert_catkin_to_bloom(cwd)


def parse_bloom_conf(cwd=None):
    """
    Parses the bloom.conf file in the current directory and returns info in it.
    """
    cmd = 'git config -f bloom.conf bloom.upstream'
    upstream_repo = check_output(cmd, shell=True, cwd=cwd).strip()
    cmd = 'git config -f bloom.conf bloom.upstreamtype'
    upstream_type = check_output(cmd, shell=True, cwd=cwd).strip()
    try:
        cmd = 'git config -f bloom.conf bloom.upstreambranch'
        upstream_branch = check_output(cmd, shell=True, cwd=cwd).strip()
    except CalledProcessError:
        upstream_branch = ''
    return upstream_repo, upstream_type, upstream_branch


def get_tarball_name(pkg_name, full_version):
    """
    Creates a tarball name from a package name.
    """
    pkg_name = pkg_name.replace('_', '-')
    return '{0}-{1}'.format(pkg_name, full_version)


def create_initial_upstream_branch(cwd=None):
    """
    Creates an empty, initial upstream branch in the given git repository.
    """
    execute_command('git symbolic-ref HEAD refs/heads/upstream', cwd=cwd)
    execute_command('rm -f .git/index', cwd=cwd)
    execute_command('git clean -dfx', cwd=cwd)
    execute_command('git commit --allow-empty -m "Initial upstream branch"',
                    cwd=cwd)


def detect_git_import_orig():
    """
    Returns True if git-import-orig is in the path, False otherwise
    """
    from subprocess import PIPE
    try:
        check_call('git-import-orig --help', shell=True, stdout=PIPE,
                   stderr=PIPE)
        return True
    except (OSError, CalledProcessError):
        return False
    return False


def summarize_repo_info(upstream_repo, upstream_type, upstream_branch):
    msg = 'upstream repo: ' + ansi('boldon') + upstream_repo \
        + ansi('reset')
    print(msg)
    msg = 'upstream type: ' + ansi('boldon') + upstream_type \
        + ansi('reset')
    print(msg)
    upstream_branch = upstream_branch if upstream_branch else '(No branch set)'
    msg = 'upstream branch: ' + ansi('boldon') + upstream_branch \
        + ansi('reset')
    print(msg)


def import_upstream(bloom_repo):
    # Ensure the bloom and upstream branches are tracked
    track_all_git_branches(['bloom', 'upstream'])

    # Check for a bloom branch
    check_for_bloom(os.getcwd())

    # Check for bloom.conf
    bloom_repo.update('bloom')
    if not os.path.exists('bloom.conf'):
        # The repository has not been bloom initialized
        not_a_bloom_release_repo()
    # Parse the bloom config file
    upstream_repo, upstream_type, upstream_branch = parse_bloom_conf()
    # Summarize the config contents
    summarize_repo_info(upstream_repo, upstream_type, upstream_branch)
    # If git make some checks
    if upstream_type == 'git':
        print("Verifying a couple of things about the upstream git repo")
        # Ensure the upstream repo is not setup as a gbp
        assert_is_not_gbp_repo(upstream_repo)

    # Checkout upstream
    tmp_dir = create_temporary_directory()
    upstream_dir = os.path.join(tmp_dir, 'upstream')
    os.makedirs(upstream_dir)
    upstream_client = VcsClient(upstream_type, upstream_dir)
    branch = upstream_branch if upstream_branch != '(No branch set)' else ''
    upstream_client.checkout(upstream_repo, branch)

    # Parse the stack.xml
    if os.path.exists(os.path.join(upstream_dir, 'stack.xml')):
        stack = parse_stack_xml(os.path.join(upstream_dir, 'stack.xml'))
    else:
        bailout("No stack.xml at {0}".format(upstream_dir))

    # Summarize the stack.xml contents
    print("Upstream's stack.xml has version " + ansi('boldon')
        + stack.version + ansi('reset'))
    print("Upstream's name is " + ansi('boldon') + stack.name
        + ansi('reset'))

    # Export the repository to a tar ball
    tarball_prefix = get_tarball_name(stack.name, stack.version)
    print('Exporting version {0}'.format(stack.version))
    tarball_path = os.path.join(tmp_dir, tarball_prefix)
    upstream_client.export_repository(stack.version, tarball_path)

    # Get the gbp version elements from either the last tag or the default
    last_tag = get_last_git_tag()
    if last_tag == '':
        gbp_major, gbp_minor, gbp_patch = segment_version(stack.version)
    else:
        gbp_major, gbp_minor, gbp_patch = \
            get_versions_from_upstream_tag(last_tag)
        print("The latest upstream tag in the release repo is " \
            + ansi('boldon') + last_tag + ansi('reset'))
        # Ensure the new version is greater than the last tag
        full_version_strict = StrictVersion(stack.version)
        last_tag_version = '.'.join([gbp_major, gbp_minor, gbp_patch])
        last_tag_version_strict = StrictVersion(last_tag_version)
        if full_version_strict < last_tag_version_strict:
            warning("""\
Version discrepancy:
The upstream version, {0}, should be greater than the previous
release version, {1}.

Upstream should rerelease or you should fix the release repo.
""".format(stack.version, last_tag_version))
        if full_version_strict == last_tag_version_strict:
            warning("""\
Version discrepancy:
The upstream version, {0}, is equal to a previous version.
""".format(stack.verison))

    # Look for upstream branch
    output = check_output('git branch', shell=True)
    if output.count('upstream') == 0:
        print(ansi('boldon') + "No upstream branch" + ansi('reset') \
            + "... creating an initial upstream branch.")
        create_initial_upstream_branch()

    # Go to the master branch
    bloom_repo.update('master')

    # Detect if git-import-orig is installed
    if not detect_git_import_orig():
        bailout("git-import-orig not detected, did you install "
                "git-buildpackage?")

    # Import the tarball
    cmd = 'git import-orig {0}'.format(tarball_path + '.tar.gz')
    try:
        if check_call(cmd, shell=True) != 0:
            bailout("git-import-orig failed '{0}'".format(cmd))
    except CalledProcessError:
        bailout("git-import-orig failed '{0}'".format(cmd))


def main():
    # Check that the current directory is a servicable git/bloom repo
    bloom_repo = VcsClient('git', os.getcwd())
    if not bloom_repo.detect_presence():
        error("Not in a git repository.\n")
        usage()
        return 1

    # Get the current git branch
    current_branch = get_current_git_branch()

    try:
        import_upstream(bloom_repo)

        # Done!
        print("I'm happy.  You should be too.")
    finally:
        # Restore the original branch if it exists still
        local_branches = check_output('git branch', shell=True)
        if current_branch and current_branch in local_branches:
            bloom_repo.update(current_branch)
