# -*- coding: utf-8 -*-

from __future__ import print_function

import copy
import glob
import os
import sys
import zipfile

from .entity import _ModelDBEntity
from .._internal_utils import _utils

from .._protos.public.common import CommonService_pb2 as _CommonCommonService


from ..external import six


# location in DeploymentService model container
_CUSTOM_MODULES_DIR = os.environ.get('VERTA_CUSTOM_MODULES_DIR', "/app/custom_modules/")


class _DeployableEntity(_ModelDBEntity):
    def _custom_modules_as_artifact(self, paths=None):
        if isinstance(paths, six.string_types):
            paths = [paths]
        if paths is not None:
            paths = list(map(os.path.expanduser, paths))
            paths = list(map(os.path.abspath, paths))

        # collect local sys paths
        local_sys_paths = copy.copy(sys.path)
        ## replace empty first element with cwd
        ##     https://docs.python.org/3/library/sys.html#sys.path
        if local_sys_paths[0] == "":
            local_sys_paths[0] = os.getcwd()
        ## convert to absolute paths
        local_sys_paths = list(map(os.path.abspath, local_sys_paths))
        ## remove paths that don't exist
        local_sys_paths = list(filter(os.path.exists, local_sys_paths))
        ## remove .ipython
        local_sys_paths = list(filter(lambda path: not path.endswith(".ipython"), local_sys_paths))
        ## remove virtual (and real) environments
        def is_in_venv(path):
            """
            Roughly checks for:
                /
                |_ lib/
                |   |_ python*/ <- directory with Python packages, containing `path`
                |
                |_ bin/
                    |_ python*  <- Python executable

            """
            lib_python_str = os.path.join(os.sep, "lib", "python")
            i = path.find(lib_python_str)
            return i != -1 and glob.glob(os.path.join(path[:i], "bin", "python*"))
        local_sys_paths = list(filter(lambda path: not is_in_venv(path), local_sys_paths))

        # get paths to files within
        if paths is None:
            # Python files within filtered sys.path dirs
            paths = local_sys_paths
            extensions = ['py', 'pyc', 'pyo']
        else:
            # all user-specified files
            paths = paths
            extensions = None
        local_filepaths = _utils.find_filepaths(
            paths, extensions=extensions,
            include_hidden=True,
            include_venv=False,  # ignore virtual environments nested within
        )

        # obtain deepest common directory
        #     This directory on the local system will be mirrored in `_CUSTOM_MODULES_DIR` in
        #     deployment.
        curr_dir = os.path.join(os.getcwd(), "")
        paths_plus = list(local_filepaths) + [curr_dir]
        common_prefix = os.path.commonprefix(paths_plus)
        common_dir = os.path.dirname(common_prefix)

        # replace `common_dir` with `_CUSTOM_MODULES_DIR` for deployment sys.path
        depl_sys_paths = list(map(lambda path: os.path.relpath(path, common_dir), local_sys_paths))
        depl_sys_paths = list(map(lambda path: os.path.join(_CUSTOM_MODULES_DIR, path), depl_sys_paths))

        bytestream = six.BytesIO()
        with zipfile.ZipFile(bytestream, 'w') as zipf:
            for filepath in local_filepaths:
                arcname = os.path.relpath(filepath, common_dir)  # filepath relative to archive root
                try:
                    zipf.write(filepath, arcname)
                except:
                    # maybe file has corrupt metadata; try reading then writing contents
                    with open(filepath, 'rb') as f:
                        zipf.writestr(arcname, f.read())

            # add verta config file for sys.path and chdir
            working_dir = os.path.join(_CUSTOM_MODULES_DIR, os.path.relpath(curr_dir, common_dir))
            zipf.writestr(
                "_verta_config.py",
                six.ensure_binary('\n'.join([
                    "import os, sys",
                    "",
                    "",
                    "sys.path = sys.path[:1] + {} + sys.path[1:]".format(depl_sys_paths),
                    "",
                    "try:",
                    "    os.makedirs(\"{}\")".format(working_dir),
                    "except OSError:  # already exists",
                    "    pass",
                    "os.chdir(\"{}\")".format(working_dir),
                ]))
            )

            # add __init__.py
            init_filename = "__init__.py"
            if init_filename not in zipf.namelist():
                zipf.writestr(init_filename, b"")

            if self._conf.debug:
                print("[DEBUG] archive contains:")
                zipf.printdir()
        bytestream.seek(0)

        return bytestream