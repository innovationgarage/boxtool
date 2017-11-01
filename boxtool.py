#! /usr/bin/env python

import click
import json
import subprocess

import os.path
import contextlib
import subprocess
import tempfile
import lxml.etree
import sys
import time
import pipes

def ensuredirs(pth):
    if os.path.exists(pth):
        return
    os.makedirs(pth)

@contextlib.contextmanager
def tmpfile(*arg, **kw):
    tempfd, tempname = tempfile.mkstemp(*arg, **kw)
    yield tempfd, tempname
    os.close(tempfd)
    os.unlink(tempname)

class SystemError(Exception):
    def __init__(self, cmd, retval, stderr, *arg, **kw):
        self.cmd = cmd
        self.retval = retval
        self.stderr = stderr
        Exception.__init__(self, *arg, **kw)

    def __str__(self):
        return "%s returned %s\n%s" % (self.cmd, self.retval, self.stderr)
        
def system(cmd):
    print "XXXXXXXXXXXXXXXXXXXXXXXX", cmd
    sys.stdout.flush()
    with tmpfile() as (tempfd, tempname):
        x = """{
%s
} 2> >(tee '%s' >&2)
""" % (cmd, tempname)
        ret = subprocess.Popen(x, shell=True, executable="/bin/bash").wait()
        if ret != 0:
            with open(tempname) as f:
                raise SystemError(x, ret, f.read())

def flatten_dict(d, prefix=''):
    res = {}
    for key, value in d.iteritems():
        if isinstance(value, dict):
            res.update(flatten_dict(value, "%s%s_" % (prefix, key)))
        else:
            res["%s%s" % (prefix, key)] = value
    return res

def deletePid(pidfile, status):
    with open(pidfile + ".control", "w") as f:
        f.write(str(status))

def createPid(pidfile):
    os.mkfifo(pidfile + ".control")
    pid1 = os.fork()
    if pid1 == 0:
        pid2 = os.fork()
        if pid2 == 0:
            with open(pidfile + ".control") as f:
                sys.exit(int(f.read()))
        else:
            with open(pidfile + ".x", "w") as f:
                f.write(str(pid2))
            os.rename(pidfile + ".x", pidfile)
    else:
        while not os.path.exists(pidfile):
            time.sleep(1)

def get_guest_ip(container_id):
    return subprocess.check_output(['bash', '-c', 'vboxmanage guestproperty get %s /VirtualBox/GuestInfo/Net/1/V4/IP | sed -e "s+Value: ++g"' % container_id]).strip()


def get_guest_path(vm):
    return subprocess.check_output(['bash', '-c', 'vboxmanage showvminfo %s | grep "Config file:" | sed -e "s+Config file:  *++g"' % vm]).strip()

def get_guest_uuid(vm):
    return subprocess.check_output(['bash', '-c', 'vboxmanage showvminfo %s | grep ^UUID:  | sed -e "s+UUID:  *++g"' % vm]).strip()

def get_guest_pid(vm):
    return subprocess.check_output(['bash', '-c', 'ps aux | grep %s | grep /usr/lib/virtualbox/VirtualBox | sed -e "s+[^ ]*  *\([0-9]*\) .*+\1+g"' % get_guest_uuid(vm)]).strip()

def clone_vm(vm, basefolder, name):
    old_config_path = get_guest_path(vm)
    new_dir = os.path.join(basefolder, name)
    new_config_path = os.path.join(new_dir, name + ".vbox")
    new_disk_path = os.path.join(new_dir, name + ".vdi")
    
    tree = lxml.etree.parse(old_config_path)
    disk = tree.xpath(".//x:HardDisk", namespaces={"x":"http://www.innotek.de/VirtualBox-settings"})[0]
    machine = tree.xpath(".//x:Machine/@uuid", namespaces={"x":"http://www.innotek.de/VirtualBox-settings"})[0]
    old_disk_path = os.path.join(os.path.dirname(old_config_path), disk.get("location"))
    
    ensuredirs(new_dir)

    system("vboxmanage unregistervm %s" % vm)
    system("vboxmanage createmedium disk --diffparent '%s' --filename '%s'" % (old_disk_path, new_disk_path))
    system("vboxmanage registervm '%s'" % old_config_path)
    new_disk_uuid = subprocess.check_output(['bash', '-c', "vboxmanage showmediuminfo '%s' | grep UUID | grep -v Parent | sed -e 's+.*:  *++g'" % new_disk_path]).strip()


    disk.set("uuid", "{%s}" % new_disk_uuid)
    disk.set("location", os.path.basename(new_disk_path))
    machine.set("uuid", "{%s}" % uuid.uuid4())
    machine.set("name", name)

    tree.write(new_config_path)
    system("vboxmanage registervm '%s'" % new_config_path)

@click.group()
@click.option("--root", default='/var/lib/boxtool')
@click.option("--log")
@click.option("--log-format")
@click.pass_context
def main(ctx, **kw):
    ctx.obj = {}
    ctx.obj['main'] = kw

@main.command()
@click.option("--bundle")
@click.option("--console")
@click.option("--pid-file")
@click.argument("container_id")
@click.pass_context
def create(ctx, **kw):
    ctx.obj['create'] = kw
    print json.dumps(ctx.obj, indent=2)

    with open(os.path.join(kw['bundle'], "config.json")) as f:
        ctx.obj['bundle_config'] = json.load(f)

    ctx.obj['bundle_config']['process']['stdin'] = os.path.join(kw['bundle'], 'init-stdin')
    ctx.obj['bundle_config']['process']['stdout'] = os.path.join(kw['bundle'], 'init-stdout')
    ctx.obj['bundle_config']['process']['stderr'] = os.path.join(kw['bundle'], 'init-stderr')

    cwd = "cd '%s'" % ctx.obj['bundle_config']['process']['cwd']
    exports = "export %s;" % ' '.join("'%s'" % item for item in ctx.obj['bundle_config']['process']['env'])
    cmd = ' '.join("'%s'" % item for item in ctx.obj['bundle_config']['process']['args'])
    ctx.obj['bundle_config']['process']['shell_cmd'] = pipes.quote("; ".join((cwd, exports, cmd)))

    # ctx.obj['bundle_config']['root']['path'] == "/path"
    # ctx.obj['bundle_config']['platform']['arch'] == "amd64"
    # ctx.obj['bundle_config']['platform']['os'] == "linux"
    # ctx.obj['bundle_config']['process']['args'] == ["/bin/sh", "-c", "ls"]
    # ctx.obj['bundle_config']['process']['cwd'] == "/path"
    # ctx.obj['bundle_config']['process']['env'] == ['FOO=bar', ...]
    # ctx.obj['bundle_config']['process']['user']['gid'] == 5
    # ctx.obj['bundle_config']['process']['user']['pid'] == 10

    args = flatten_dict(ctx.obj)

    ensuredirs("%(main_root)s/vms/%(create_container_id)s" % args)
    ensuredirs("%(main_root)s/mnt" % args)

    with open("%(main_root)s/vms/%(create_container_id)s/container.json" % args, "w") as f:
        json.dump(args, f, indent=2)

    createPid(args['create_pid_file'])

@main.command()
@click.argument("container_id")
@click.pass_context
def start(ctx, **kw):
    ctx.obj['start'] = kw
    args = flatten_dict(ctx.obj)
    with open("%(main_root)s/vms/%(start_container_id)s/container.json" % args) as f:
        args = json.load(f)

    # clone_vm("boxtool-linux", "%(main_root)s/vms" % args, args["create_container_id"])
    system("vboxmanage clonevm --basefolder=%(main_root)s/vms --register --name %(create_container_id)s boxtool-linux" % args)
    os.system("modprobe nbd")
    os.system("qemu-nbd -d /dev/nbd0")
    os.system("umount %(main_root)s/mnt" % args)
    system("qemu-nbd -c /dev/nbd0 %(main_root)s/vms/%(create_container_id)s/%(create_container_id)s.vdi" % args)
    system("mount /dev/nbd0p1 %(main_root)s/mnt" % args)
    system("rsync -a %(bundle_config_root_path)s/ %(main_root)s/mnt/" % args)
    system("umount %(main_root)s/mnt" % args)
    system("qemu-nbd -d /dev/nbd0")
        
    system("vboxmanage startvm --type headless %(create_container_id)s" % args)

    args['guest_ip'] = get_guest_ip(args['create_container_id'])

    system("ssh root@%(guest_ip)s %(bundle_config_process_shell_cmd)s < %(bundle_config_process_stdin)s > %(bundle_config_process_stdout)s 2> %(bundle_config_process_stderr)s &" % args)

    deletePid(args['create_pid_file'], 0)
    
@main.command()
@click.argument("container_id")
@click.pass_context
def delete(ctx, **kw):
    ctx.obj['delete'] = kw
    args = flatten_dict(ctx.obj)
    with open("%(main_root)s/vms/%(delete_container_id)s/container.json" % args) as f:
        args = json.load(f)

    try:
        system("vboxmanage controlvm %(create_container_id)s poweroff" % args)
    except Exception, e:
        print e
    
    os.system("modprobe nbd")
    os.system("qemu-nbd -d /dev/nbd0")
    os.system("umount %(main_root)s/mnt" % args)
    system("qemu-nbd -c /dev/nbd0 %(main_root)s/vms/%(create_container_id)s/%(create_container_id)s.vdi" % args)
    system("mount /dev/nbd0p1 %(main_root)s/mnt" % args)
    system("rsync -a %(main_root)s/mnt/ %(bundle_config_root_path)s/" % args)
    system("umount /dev/nbd0p1")
    system("qemu-nbd -d /dev/nbd0")

    system("vboxmanage unregistervm %(create_container_id)s --delete" % args)
    
main()
