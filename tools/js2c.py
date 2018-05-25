#!/usr/bin/env python

# Copyright 2015-present Samsung Electronics Co., Ltd. and other contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#  This file converts src/js/*.js to a C-array in src/iotjs_js.[h|c] file.
# And this file also generates magic string list in src/iotjs_string_ext.inl.h
# file to reduce JerryScript heap usage.

import os
import re
import subprocess
import struct
import string

from common_py.system.filesystem import FileSystem as fs
from common_py import path

def regroup(l, n):
    return [l[i:i+n] for i in range(0, len(l), n)]


def remove_comments(code):
    pattern = r'(\".*?\"|\'.*?\')|(/\*.*?\*/|//[^\r\n]*$)'
    regex = re.compile(pattern, re.MULTILINE | re.DOTALL)

    def _replacer(match):
        if match.group(2) is not None:
            return ""
        else:
            return match.group(1)

    return regex.sub(_replacer, code)


def remove_whitespaces(code):
    return re.sub('\n+', '\n', re.sub('\n +', '\n', code))


def force_str(string):
    if not isinstance(string, str):
        return string.decode('utf-8')
    else:
        return string


def parse_literals(code):
    JERRY_SNAPSHOT_VERSION = 10
    JERRY_SNAPSHOT_MAGIC = 0x5952524A

    literals = set()
    # header format:
    # uint32_t magic
    # uint32_t version
    # uint32_t global opts
    # uint32_t literal table offset
    header = struct.unpack('I' * 4, code[0:4 * 4])
    if header[0] != JERRY_SNAPSHOT_MAGIC:
        print('Incorrect snapshot format! Magic number is incorrect')
        exit(1)
    print(header)
    if header[1] != JERRY_SNAPSHOT_VERSION:
        print ('Please check jerry snapshot version (Last confirmed: %d)'
               % JERRY_SNAPSHOT_VERSION)
        exit(1)

    code_ptr = header[3] + 4

    while code_ptr < len(code):
        length = struct.unpack('H', code[code_ptr : code_ptr + 2])[0]
        code_ptr = code_ptr + 2
        if length == 0:
            continue
        if length < 32:
            item = struct.unpack('%ds' % length,
                                 code[code_ptr : code_ptr + length])
            literals.add(force_str(item[0]))
        code_ptr = code_ptr + length + (length % 2)

    return literals


LICENSE = '''
/* Copyright 2015-present Samsung Electronics Co., Ltd. and other contributors
 *
 * Licensed under the Apache License, Version 2.0 (the \"License\");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an \"AS IS\" BASIS
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * This file is generated by tools/js2c.py
 * Do not modify this.
 */
'''

HEADER1 = '''#ifndef IOTJS_JS_H
#define IOTJS_JS_H
'''

FOOTER1 = '''
#endif
'''

HEADER2 = '''#include <stdio.h>
#include <stdint.h>
#include "iotjs_js.h"
'''

EMPTY_LINE = '\n'

MAGIC_STRINGS_HEADER = '#define JERRY_MAGIC_STRING_ITEMS \\\n'

MODULE_SNAPSHOT_VARIABLES_H = '''
extern const char module_{NAME}[];
extern const uint32_t module_{NAME}_idx;
'''

MODULE_SNAPSHOT_VARIABLES_C = '''
#define MODULE_{NAME}_IDX ({IDX})
const char module_{NAME}[] = "{NAME}";
const uint32_t module_{NAME}_idx = MODULE_{NAME}_IDX;
'''

NATIVE_SNAPSHOT_STRUCT_H = '''
typedef struct {
  const char* name;
  const uint32_t idx;
} iotjs_js_module_t;

extern const iotjs_js_module_t js_modules[];
'''

MODULE_VARIABLES_H = '''
extern const char {NAME}_n[];
extern const uint8_t {NAME}_s[];
extern const size_t {NAME}_l;
'''

MODULE_VARIABLES_C = '''
#define SIZE_{NAME_UPPER} {SIZE}
const size_t {NAME}_l = SIZE_{NAME_UPPER};
const char {NAME}_n[] = "{NAME}";
const uint8_t {NAME}_s[] = {{
{CODE}
}};
'''

NATIVE_STRUCT_H = '''
typedef struct {
  const char* name;
  const void* code;
  const size_t length;
} iotjs_js_module_t;

extern const iotjs_js_module_t js_modules[];
'''

NATIVE_STRUCT_C = '''
const iotjs_js_module_t js_modules[] = {{
{MODULES}
}};
'''


def hex_format(ch):
    if isinstance(ch, str):
        ch = ord(ch)

    return "0x{:02x}".format(ch)


def format_code(code, indent):
    lines = []
    # convert all characters to hex format
    converted_code = map(hex_format, code)
    # 10 hex number per line
    for line in regroup(", ".join(converted_code), 10 * 6):
        lines.append(('  ' * indent) + line.strip())

    return "\n".join(lines)


def merge_snapshots(snapshot_infos, snapshot_tool):
    output_path = fs.join(path.SRC_ROOT, 'js','merged.modules')
    cmd = [snapshot_tool, "merge", "-o", output_path]
    cmd.extend([item['path'] for item in snapshot_infos])

    ret = subprocess.call(cmd)

    if ret != 0:
        msg = "Failed to merge %s: - %d" % (snapshot_infos, ret)
        print("%s%s%s" % ("\033[1;31m", msg, "\033[0m"))
        exit(1)

    for item in snapshot_infos:
        fs.remove(item['path'])

    with open(output_path, 'rb') as snapshot:
        code = snapshot.read()

    fs.remove(output_path)
    return code


def get_snapshot_contents(js_path, snapshot_tool):
    """ Convert the given module with the snapshot generator
        and return the resulting bytes.
    """
    wrapped_path = js_path + ".wrapped"
    snapshot_path = js_path + ".snapshot"
    module_name = os.path.splitext(os.path.basename(js_path))[0]

    with open(wrapped_path, 'w') as fwrapped, open(js_path, "r") as fmodule:
        if module_name != "iotjs":
            fwrapped.write("(function(exports, require, module, native) {\n")

        fwrapped.write(fmodule.read())

        if module_name != "iotjs":
            fwrapped.write("});\n")

    ret = subprocess.call([snapshot_tool,
                           "generate",
                           "--context", "eval",
                           "-o", snapshot_path,
                           wrapped_path])

    fs.remove(wrapped_path)
    if ret != 0:
        msg = "Failed to dump %s: - %d" % (js_path, ret)
        print("%s%s%s" % ("\033[1;31m", msg, "\033[0m"))
        fs.remove(snapshot_path)
        exit(1)

    return snapshot_path


def get_js_contents(js_path, is_debug_mode=False):
    """ Read the contents of the given js module. """
    with open(js_path, "r") as f:
         code = f.read()

    # minimize code when in release mode
    if not is_debug_mode:
        code = remove_comments(code)
        code = remove_whitespaces(code)
    return code


def js2c(buildtype, js_modules, snapshot_tool=None, verbose=False):
    is_debug_mode = (buildtype == "debug")
    no_snapshot = (snapshot_tool == None)
    magic_string_set = set()

    str_const_regex = re.compile('^#define IOTJS_MAGIC_STRING_\w+\s+"(\w+)"$')
    with open(fs.join(path.SRC_ROOT, 'iotjs_magic_strings.h'), 'r') as fin_h:
        for line in fin_h:
            result = str_const_regex.search(line)
            if result:
                magic_string_set.add(result.group(1))

    # generate the code for the modules
    with open(fs.join(path.SRC_ROOT, 'iotjs_js.h'), 'w') as fout_h, \
         open(fs.join(path.SRC_ROOT, 'iotjs_js.c'), 'w') as fout_c:

        fout_h.write(LICENSE)
        fout_h.write(HEADER1)
        fout_c.write(LICENSE)
        fout_c.write(HEADER2)

        snapshot_infos = []
        js_module_names = []
        for idx, module in enumerate(sorted(js_modules)):
            [name, js_path] = module.split('=', 1)
            js_module_names.append(name)
            if verbose:
                print('Processing module: %s' % name)

            if no_snapshot:
                code = get_js_contents(js_path, is_debug_mode)
                code_string = format_code(code, 1)

                fout_h.write(MODULE_VARIABLES_H.format(NAME=name))
                fout_c.write(MODULE_VARIABLES_C.format(NAME=name,
                                                       NAME_UPPER=name.upper(),
                                                       SIZE=len(code),
                                                       CODE=code_string))
            else:
                code_path = get_snapshot_contents(js_path, snapshot_tool)
                info = {'name': name, 'path': code_path, 'idx': idx}
                snapshot_infos.append(info)

                fout_h.write(MODULE_SNAPSHOT_VARIABLES_H.format(NAME=name))
                fout_c.write(MODULE_SNAPSHOT_VARIABLES_C.format(NAME=name,
                                                                IDX=idx))

        if no_snapshot:
            modules_struct = [
               '  {{ {0}_n, {0}_s, SIZE_{1} }},'.format(name, name.upper())
               for name in sorted(js_module_names)
            ]
            modules_struct.append('  { NULL, NULL, 0 }')
        else:
            code = merge_snapshots(snapshot_infos, snapshot_tool)
            code_string = format_code(code, 1)
            magic_string_set |= parse_literals(code)

            name = 'iotjs_js_modules'
            fout_h.write(MODULE_VARIABLES_H.format(NAME=name))
            fout_c.write(MODULE_VARIABLES_C.format(NAME=name,
                                                   NAME_UPPER=name.upper(),
                                                   SIZE=len(code),
                                                   CODE=code_string))
            modules_struct = [
                '  {{ module_{0}, MODULE_{0}_IDX }},'.format(info['name'])
                for info in snapshot_infos
            ]
            modules_struct.append('  { NULL, 0 }')

        if no_snapshot:
            native_struct_h = NATIVE_STRUCT_H
        else:
            native_struct_h = NATIVE_SNAPSHOT_STRUCT_H

        fout_h.write(native_struct_h)
        fout_h.write(FOOTER1)

        fout_c.write(NATIVE_STRUCT_C.format(MODULES="\n".join(modules_struct)))
        fout_c.write(EMPTY_LINE)

    # Write out the external magic strings
    magic_str_path = fs.join(path.SRC_ROOT, 'iotjs_string_ext.inl.h')
    with open(magic_str_path, 'w') as fout_magic_str:
        fout_magic_str.write(LICENSE)
        fout_magic_str.write(MAGIC_STRINGS_HEADER)

        sorted_strings = sorted(magic_string_set, key=lambda x: (len(x), x))
        for idx, magic_string in enumerate(sorted_strings):
            magic_text = repr(magic_string)[1:-1]
            magic_text = string.replace(magic_text, "\"", "\\\"")

            fout_magic_str.write('  MAGICSTR_EX_DEF(MAGIC_STR_%d, "%s") \\\n'
                                 % (idx, magic_text))
        # an empty line is required to avoid compile warning
        fout_magic_str.write(EMPTY_LINE)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()

    parser.add_argument('--buildtype',
        choices=['debug', 'release'], default='debug',
        help='Specify the build type: %(choices)s (default: %(default)s)')
    parser.add_argument('--modules', required=True,
        help='List of JS files to process. Format: '
             '<module_name1>=<js_file1>,<module_name2>=<js_file2>,...')
    parser.add_argument('--snapshot-tool', default=None,
        help='Executable to use for generating snapshots and merging them '
             '(ex.: the JerryScript snapshot tool). '
             'If not specified the JS files will be directly processed.')
    parser.add_argument('-v', '--verbose', default=False,
        help='Enable verbose output.')

    options = parser.parse_args()

    if not options.snapshot_tool:
        print('Converting JS modules to C arrays (no snapshot)')
    else:
        print('Using "%s" as snapshot tool' % options.snapshot_tool)

    modules = options.modules.replace(',', ' ').split()
    js2c(options.buildtype, modules, options.snapshot_tool, options.verbose)
