#!/usr/bin/env python3

# lwobker@cisco.com    
# usage:  copy script into shell of running XR machine
# to execute, just do "nohup python3 ./drop_script.py -n 60 -t 60 &> npudrops.log &"
# this will run it 60 times, once every 60 seconds, (i.e. for 1 hour) 


import argparse
import datetime
import json
import logging
import lzma
import os
import subprocess
import sys
import time



def runShowCommands(cmdTable) -> dict:
    """return a dictionary of captured output from commands defined in cmdTable.
    Note: not all of these commands will exist on all systems (e.g. the fabric stuff doesn't 
    exist on a fixed system. 
    """
    procOutput = {}  # dict to store output text from show commands 
    procHandles = {}
    for cmd in cmdTable.keys():
        try:
            procHandles[cmd] = subprocess.Popen(cmdTable[cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            log.debug(f"process handle {cmd} could not execute --  likely because fixed vs. distributed")
    for handle, proc in procHandles.items():
        try:
            procOutput[handle] = proc.communicate(timeout=180)[0].decode("utf-8")  # turn stdout portion into text
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError("process with handle '{}' timed out!".format(handle))
    return procOutput

def saveJsonXz(cmdOutput: dict, filename: str) -> None:
    with lzma.open(filename, "wt", encoding='utf-8') as outfile:
        json.dump(cmdOutput, outfile)

def getParser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--time_interval", type=int, default=30, help="seconds between subsequent runs - default 30 sec")
    parser.add_argument("-n", "--num_runs", type=int, default=1, help="number of runs to execute - use '0' to run forever")
    parser.add_argument("-o", "--output_dir", default='/var/xr/disk1/envSnaps', help="output directory, default: /var/xr/disk1/envSnaps")
    parser.add_argument("-l", "--leader", default = '', help="descriptive string prepended to snapshot filenames")
    if len(sys.argv)==1:
        parser.print_help(sys.stderr)
    return parser.parse_args()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

"""dictionary of commands to run when collecting.  values are lists with the shell executable
and arguments.  Any XR command can be translated to its shell equivalent with _describe_
"""
cmdTable = {
    "timestamp": ["date", "+%s"],           # XR: "show clock"
    "showVersion": ["show_version"],        # XR: "show version"
    "showIntf": ["show_interface", "-a"],   # XR: "show interface"
    "showInv": ["show_inventory", "-e"],        # XR: show inventory"
    "etcHostname": ["cat", "/etc/hostname"],        # hostname (no direct XR equiv.)
    "showNpuSlice": ["show_slicemgr", "-I", "0xff", "-n", "A"],   # XR: show contr npu slice info...
    "showPolMapInt": ["qos_ma_show_stats", "-i", "Bundle-Ether21", "-p", "0x1", "-q", "0x2",]
}

clearCmdTable = {}

for card in range(18):
    for npu_inst in range(4):
        # this monster is the shell command to run "show controller npu stats" to get all the drops
        # iterated over each possible LC / NPU combination.  (note: nonexistent LC/NPUs safely return)
        # create commands to clear the counters (we run this once at the beginning)
        clearCmdVal = ["npd_npu_driver_clear", "-c", "s", "-i", f"0x{str(npu_inst)}", "-n", f"{str(256*card)}"]
        clearCmdTable[f"clear_command_{card}_{npu_inst}"] = clearCmdVal
        # build the show command for each (LC, NPU) tuple
        cmdval = ["ofa_npu_stats_show", "-v", "a", "-t", "e", "-p", "0xffffffff", "-s", "0x0", "-d", "A",]
        cmdval = cmdval + ["-i", f"0x{str(npu_inst)}", "-n", f"{str(256*card)}"]
        cmdTable[f"npu_drops{card}_{npu_inst}"] = cmdval


if __name__ == '__main__':
    os.nice(20)
    args = getParser()
    snapshot_dir = args.output_dir
    runs_remaining = args.num_runs
    log.info(f"Using output directory {snapshot_dir}")
    if not os.path.exists(snapshot_dir):
        os.mkdir(snapshot_dir)

    ClearCommandOutput = runShowCommands(clearCmdTable)   # run once, clear counters...

    while True:
        commandOutput = runShowCommands(cmdTable)       # run commands
        snapTime = datetime.datetime.fromtimestamp(int(commandOutput["timestamp"]))
        timestamp = snapTime.strftime("%y%m%d-%H%M%S")
        filename_leader = args.leader + commandOutput["etcHostname"].strip() 
        out_fname = filename_leader + "_cmds_" + timestamp + ".json.xz"     # assemble output filename
        output_fullpath = "/".join([snapshot_dir, out_fname])
        log.info(f"Writing compressed JSON output to {output_fullpath}.")
        saveJsonXz(commandOutput, output_fullpath)

        runs_remaining = runs_remaining - 1
        if (runs_remaining > 0 or args.num_runs == 0):    # are we done?
            time.sleep(args.time_interval)
        else:
            break


