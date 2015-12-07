#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert an rootfs archive to a bootable disk image with guestfish."""
from __future__ import division, unicode_literals

import os
import os.path as op
import sys
import subprocess
import argparse
import logging
import tempfile
import shutil
import contextlib


logger = logging.getLogger(__name__)


# Syntax sugar.
_ver = sys.version_info

#: Python 2.x?
is_py2 = (_ver[0] == 2)

#: Python 3.x?
is_py3 = (_ver[0] == 3)


# Python 2/3 compat
if is_py3:
    builtin_str = str
    str = str
    bytes = bytes
    basestring = (str, bytes)

    def is_bytes(x):
        """ Return True if `x` is bytes."""
        return isinstance(x, (bytes, memoryview, bytearray))

else:
    builtin_str = str
    bytes = str
    str = unicode

    def is_bytes(x):
        """ Return True if `x` is bytes."""
        return isinstance(x, (buffer, bytearray))


def to_unicode(obj, encoding='utf-8'):
    """Convert ``obj`` to unicode"""
    # unicode support
    if isinstance(obj, str):
        return obj

    # bytes support
    if is_bytes(obj):
        if hasattr(obj, 'tobytes'):
            return str(obj.tobytes(), encoding)
        return str(obj, encoding)

    # string support
    if isinstance(obj, basestring):
        if hasattr(obj, 'decode'):
            return obj.decode(encoding)
        else:
            return str(obj, encoding)

    return str(obj)


@contextlib.contextmanager
def temporary_directory():
    """Context manager for tempfile.mkdtemp()."""
    name = tempfile.mkdtemp()
    try:
        yield name
    finally:
        shutil.rmtree(name)


def which(command):
    """Locate a command.
    Snippet from: http://stackoverflow.com/a/377028
    """
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(command)
    if fpath:
        if is_exe(command):
            return command
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, command)
            if is_exe(exe_file):
                return exe_file

    raise ValueError("Command '{0!s}' not found".format(command))


def file_type(path):
    """Get file type."""
    if not op.exists(path):
        raise Exception("cannot open '{0!s}' (No such file or directory)".format(path))
    cmd = [which("file"), path]
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            env=os.environ.copy(),
                            shell=False)
    output, _ = proc.communicate()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, ' '.join(cmd))
    return output.split(':')[1].strip()


def qemu_convert(disk, output_fmt, output_filename):
    """Convert the disk image filename to disk image output_filename."""
    binary = which("qemu-img")
    cmd = [binary, "convert", "-p", "-O", output_fmt, disk, output_filename]
    if output_fmt in ("qcow", "qcow2"):
        cmd.insert(2, "-c")
    proc = subprocess.Popen(cmd, env=os.environ.copy(), shell=False)
    proc.communicate()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, ' '.join(cmd))


def run_guestfish_script(disk, script, mount=True, piped_output=False):
    """Run guestfish script."""
    args = [which("guestfish"), '-a', disk]
    if mount:
        script = "run\nmount /dev/sda1 /\n{0!s}".format(script)
    else:
        script = "run\n{0!s}".format(script)
    if piped_output:
        proc = subprocess.Popen(args,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                env=os.environ.copy())

        stdout, _ = proc.communicate(input=script)
    else:
        proc = subprocess.Popen(args,
                                stdin=subprocess.PIPE,
                                env=os.environ.copy())
        proc.communicate(input=script)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, ' '.join(args))

    if piped_output:
        return stdout


def find_mbr():
    """ ..."""
    search_paths = (
        "/usr/share/syslinux/mbr.bin",
        "/usr/lib/bios/syslinux/mbr.bin",
        "/usr/lib/syslinux/bios/mbr.bin",
        "/usr/lib/extlinux/mbr.bin",
        "/usr/lib/syslinux/mbr.bin",
        "/usr/lib/syslinux/mbr/mbr.bin",
        "/usr/lib/EXTLINUX/mbr.bin"
    )
    for path in search_paths:
        if op.exists(path):
            return path
    raise Exception("syslinux MBR not found")


def get_boot_information(disk):
    """Looking for boot information"""
    script_1 = """
blkid /dev/sda1 | grep ^UUID: | awk '{print $2}'
ls /boot/ | grep ^vmlinuz | head -n 1
ls /boot/ | grep ^init | grep -v fallback | head -n 1
ls /boot/ | grep ^init | grep fallback | head -n 1"""
    logger.info(get_boot_information.__doc__)
    output_1 = run_guestfish_script(disk, script_1, piped_output=True)
    try:
        infos = output_1.strip().split('\n')
        if len(infos) == 4:
            uuid, vmlinuz, initrd, initrd_fallback = infos
            if initrd:
                return uuid, vmlinuz, initrd
            else:
                return uuid, vmlinuz, initrd_fallback
        else:
            uuid, vmlinuz, initrd = infos
            return uuid, vmlinuz, initrd
    except:
        raise Exception("Invalid boot information (missing kernel ?)")


def generate_fstab(disk, uuid, filesystem_type):
    """Generate /etc/fstab file"""
    logger.info("Generating /etc/fstab")
    script = """
write /etc/fstab "# /etc/fstab: static file system information.\\n"
write-append  /etc/fstab "# Generated by kameleon-helpers.\\n\\n"
write-append /etc/fstab "UUID={0!s}\\t/\\t{1!s}\\tdefaults\\t0\\t1\\n"
""".format(uuid, filesystem_type)
    run_guestfish_script(disk, script)


def install_bootloader(disk, mbr, append):
    """Install a bootloader"""
    mbr_path = mbr or find_mbr()
    mbr_path = op.abspath(to_unicode(mbr_path))
    uuid, vmlinuz, initrd = get_boot_information(disk)
    logger.info("Root partition UUID: {0!s}".format(uuid))
    logger.info("Kernel image: /boot/{0!s}".format(vmlinuz))
    logger.info("Initrd image: /boot/{0!s}".format(initrd))
    script = """
echo "[guestfish] Upload the master boot record"
upload {0!s} /boot/mbr.bin

echo "[guestfish] Generate /boot/syslinux.cfg"
write /boot/syslinux.cfg "DEFAULT linux\\n"
write-append /boot/syslinux.cfg "LABEL linux\\n"
write-append /boot/syslinux.cfg "SAY Booting the kernel\\n"
write-append /boot/syslinux.cfg "KERNEL /boot/{1!s}\\n"
write-append /boot/syslinux.cfg "INITRD /boot/{2!s}\\n"
write-append /boot/syslinux.cfg "APPEND ro root=UUID={3!s} {4!s}\\n"

echo "[guestfish] Put the MBR into the boot sector"
copy-file-to-device /boot/mbr.bin /dev/sda size:440

echo "[guestfish] Install extlinux on the first partition"
extlinux /boot

echo "[guestfish] Set the first partition as bootable"
part-set-bootable /dev/sda 1 true

echo "[guestfish] Generate empty fstab"
write /etc/fstab "# UNCONFIGURED FSTAB FOR BASE SYSTEM\\n"
""".format(mbr_path, vmlinuz, initrd, uuid, append)
    run_guestfish_script(disk, script)
    return uuid, vmlinuz, initrd


def create_disk(input_, output_filename, fmt, size, filesystem, verbose):
    """Make a disk image from a tar archive or files."""
    input_type = file_type(input_).lower()

    make_tar_cmd = ""
    if "xz compressed data" in input_type:
        make_tar_cmd = "{0!s} {1!s}".format(which("xzcat"), input_)
    elif "bzip2 compressed data" in input_type:
        make_tar_cmd = "{0!s} {1!s}".format(which("bzcat"), input_)
    elif "gzip compressed data" in input_type:
        make_tar_cmd = "{0!s} {1!s}".format(which("zcat"), input_)

    # create a disk with empty filesystem
    logger.info("Creating an empty disk image")
    with temporary_directory() as empty_dir:
        virt_make_fs = which("virt-make-fs")
        cmd = [virt_make_fs, "--partition", "--size", size, "--type",
               "{0!s}".format(filesystem), "--format", "qcow2",
               "--", empty_dir, output_filename]
        if verbose:
            cmd.insert(1, "--verbose")

        proc = subprocess.Popen(cmd, env=os.environ.copy(), shell=False)
        proc.communicate()
        if proc.returncode:
            raise subprocess.CalledProcessError(proc.returncode, ' '.join(cmd))
    # Fill disk with our data
    logger.info("Copying the data into the disk image")
    if "directory" in input_type:
        excludes = ['dev/*', 'proc/*', 'sys/*', 'tmp/*', 'run/*',
                    '/mnt/*']
        tar_options_list = ['--numeric-owner', '--one-file-system',
                            ' '.join(('--exclude="{0!s}"'.format(s) for s in excludes))]
        tar_options = ' '.join(tar_options_list)
        make_tar_cmd = '{0!s} -cf - {1!s} -C {2!s} $(cd {3!s}; ls -A)'.format(which("tar"), tar_options, input_, input_)

    if make_tar_cmd:
        cmd = "{0!s} | {1!s} -a {2!s} -m /dev/sda1:/ tar-in - /".format(make_tar_cmd, which("guestfish"), output_filename)
    else:
        cmd = "{0!s} -a {1!s} -m /dev/sda1:/ tar-in {2!s} /".format(which("guestfish"), output_filename, input_)
    proc = subprocess.Popen(cmd, env=os.environ.copy(), shell=True)
    proc.communicate()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def create_appliance(args):
    """Convert disk to another format."""
    input_ = op.abspath(to_unicode(args.input))
    output = op.abspath(to_unicode(args.output))
    temp_filename = to_unicode(next(tempfile._get_candidate_names()))
    temp_file = op.abspath(to_unicode(".{0!s}".format(temp_filename)))
    output_fmt = args.format.lower()
    output_filename = "{0!s}.{1!s}".format(output, output_fmt)

    os.environ['LIBGUESTFS_CACHEDIR'] = os.getcwd()
    if args.verbose:
        os.environ['LIBGUESTFS_DEBUG'] = '1'

    create_disk(input_,
                temp_file,
                args.format,
                args.size,
                args.filesystem,
                args.verbose)
    logger.info("Installing bootloader")
    uuid, _, _ = install_bootloader(temp_file,
                                    args.extlinux_mbr,
                                    args.append)
    generate_fstab(temp_file, uuid, args.filesystem)

    logger.info("Exporting appliance to {0!s}".format(output_filename))
    if output_fmt == "qcow2":
        shutil.move(temp_file, output_filename)
    else:
        qemu_convert(temp_file, output_fmt, output_filename)
        os.remove(temp_file) if os.path.exists(temp_file) else None

if __name__ == '__main__':
    allowed_formats = ('qcow', 'qcow2', 'qed', 'vdi', 'raw', 'vmdk')
    allowed_formats_help = 'Allowed values are ' + ', '.join(allowed_formats)

    allowed_levels = ["{0:d}".format(i) for i in range(1, 10)] + ["best", "fast"]
    allowed_levels_helps = 'Allowed values are ' + ', '.join(allowed_levels)

    parser = argparse.ArgumentParser(
        description=sys.modules[__name__].__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('input', action="store",
                        help='input')
    parser.add_argument('-F', '--format', action="store", type=str,
                        help=('Choose the output disk image format. {0!s}'.format(
                              allowed_formats_help)), default='qcow2')
    parser.add_argument('-t', '--filesystem', action="store", type=str,
                        help='Choose the output filesystem type.',
                        default="ext2")
    parser.add_argument('-s', '--size', action="store", type=str,
                        help='choose the size of the output image',
                        default="10G")
    parser.add_argument('-o', '--output', action="store", type=str,
                        help='Output filename (without file extension)',
                        required=True, metavar='filename')
    parser.add_argument('--extlinux-mbr', action="store", type=str,
                        help='Extlinux MBR', metavar='')
    parser.add_argument('--append', action="store", type=str,
                        default="",
                        help='Additional kernel args', metavar='')
    parser.add_argument('--verbose', action="store_true", default=False,
                        help='Enable very verbose messages')
    log_format = '%(levelname)s: %(message)s'
    level = logging.INFO
    try:
        args = parser.parse_args()
        if args.verbose:
            level = logging.DEBUG

        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(log_format))

        logger.setLevel(level)
        logger.addHandler(handler)
        create_appliance(args)
    except Exception as exc:
        sys.stderr.write(u"\nError: {0!s}\n".format(exc))
        sys.exit(1)
